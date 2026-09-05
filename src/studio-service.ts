import path from "node:path";
import { readdir } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent, ExecutionPlan, InputFormField, OperationKind, RecordingSession, ReviewNext } from "./domain.js";
import type { StudioConfig } from "./config.js";
import { defaultSkillOutputRoot, loadConfig } from "./config.js";
import { BrowserRecorder } from "./browser/recorder.js";
import { id, readJson, readJsonl, writeJson } from "./utils.js";
import { buildCapabilityCandidates } from "./inference/build-candidates.js";
import { finalizeSessionSlice, sealWriteCapabilities } from "./inference/finalize-capabilities.js";
import { OpenAIReasoner } from "./llm/openai.js";
import { fallbackPlan } from "./planner/fallback.js";
import { applyPlanPolicy } from "./planner/policy.js";
import { exportSkill } from "./export/skill-exporter.js";
import { executeCapability } from "./execution/http-executor.js";
import { capabilitiesForSession, evidencePageKey, sessionBusinessPageKeys, sessionCatalogSlice, usableRelatedLookups } from "./inference/export-scope.js";
import { mergeCatalogByTransport, normalizeCatalog } from "./catalog/normalize.js";
import { applyPiCatalogJudgment } from "./inference/pi-skill-runtime.js";
import { reanalyzeIncoming } from "./inference/reanalyze.js";
import { reviewSession } from "./review/catalog-review.js";
import { rerecordBlockedMessage, reviewFindingSignature, sameReviewPage } from "./review/review-action.js";
import { SkillLibrary } from "./catalog/skill-library.js";
import { buildApprovedRoutes } from "./planner/routes.js";
import { materializeSkillCredentials, requiredCredentialOrigins } from "./credentials/credential-store.js";

function schemaPathExists(schema: CapabilityContract["inputSchema"], jsonPath: string) {
  const parts = jsonPath.replace(/^\$\.?/, "").split(".").filter(Boolean);
  let current: any = schema;
  for (const rawPart of parts) {
    const wildcard = rawPart.endsWith("[*]");
    const part = wildcard ? rawPart.slice(0, -3) : rawPart;
    if (part) current = current?.properties?.[part];
    if (!current) return false;
    if (wildcard) current = current.items;
  }
  return parts.length > 0 && Boolean(current);
}

export class StudioService {
  readonly config: StudioConfig;
  readonly recorder: BrowserRecorder;
  readonly reasoner: OpenAIReasoner;
  readonly skillLibrary: SkillLibrary;
  private lastAnalyzedSessionId?: string;
  private state?: {
    lastAnalyzedSessionId?: string;
    lastReview?: {
      sessionId?: string;
      status: "passed" | "blocked";
      next: ReviewNext;
      allowRerecord: boolean;
      pageKeys: string[];
      startUrl?: string;
      signature: string;
    };
  };
  private sessionListCache?: RecordingSession[];
  private eventCache = new Map<string, EvidenceEvent[]>();
  private catalogCache?: CapabilityContract[];

  constructor(config = loadConfig()) {
    this.config = config;
    this.recorder = new BrowserRecorder(config);
    this.reasoner = new OpenAIReasoner(config.openaiModel);
    this.skillLibrary = new SkillLibrary(defaultSkillOutputRoot(config.rootDir), config.dataDir);
  }

  private catalogFile() {
    return path.join(this.config.catalogDir, "capabilities.json");
  }

  async startRecording(
    url: string,
    name?: string,
    expectedOperations: OperationKind[] = [],
    completeFieldCoverage = false,
    completePageCoverage = false
  ) {
    const gate = await this.evaluateRerecord(url);
    if (!gate.allowed) throw new Error(gate.message);
    if (this.recorder.isActive()) await this.stopRecording();
    this.sessionListCache = undefined;
    return this.recorder.start(url, name, undefined, expectedOperations, completeFieldCoverage, completePageCoverage);
  }

  async stopRecording() {
    const session = await this.recorder.stop();
    this.sessionListCache = undefined;
    this.eventCache.delete(session.id);
    return session;
  }

  async listSessions(): Promise<RecordingSession[]> {
    if (this.sessionListCache) return this.sessionListCache;
    try {
      const ids = await readdir(this.config.recordingsDir);
      const sessions = await Promise.all(
        ids.map(id => readJson<RecordingSession | null>(
          path.join(this.config.recordingsDir, id, "session.json"),
          null
        ))
      );
      this.sessionListCache = sessions.filter((s): s is RecordingSession => Boolean(s)).sort((a, b) => b.startedAt.localeCompare(a.startedAt));
      return this.sessionListCache;
    } catch (error: any) {
      if (error?.code === "ENOENT") return [];
      throw error;
    }
  }

  async sessionEvents(sessionId: string): Promise<EvidenceEvent[]> {
    const cached = this.eventCache.get(sessionId);
    if (cached) return cached;
    const events = await readJsonl<EvidenceEvent>(path.join(this.config.recordingsDir, sessionId, "events.jsonl"));
    this.eventCache.set(sessionId, events);
    return events;
  }

  async allEvents(): Promise<EvidenceEvent[]> {
    const sessions = await this.listSessions();
    const chunks = await Promise.all(sessions.map(s => this.sessionEvents(s.id)));
    return chunks.flat();
  }

  private stateFile() {
    return path.join(this.config.dataDir, "studio-state.json");
  }

  private async studioState() {
    if (!this.state) this.state = await readJson<NonNullable<StudioService["state"]>>(this.stateFile(), {});
    return this.state;
  }

  private async rememberAnalyzedSession(sessionId?: string) {
    if (!sessionId) return;
    this.lastAnalyzedSessionId = sessionId;
    const state = await this.studioState();
    if (state.lastAnalyzedSessionId === sessionId) return;
    state.lastAnalyzedSessionId = sessionId;
    await writeJson(this.stateFile(), state);
  }

  private async persistSessionPageKeys(session: RecordingSession, events: EvidenceEvent[]) {
    const pageKeys = sessionBusinessPageKeys(events, session.startUrl);
    if ((session.pageKeys || []).join("\n") === pageKeys.join("\n")) return;
    session.pageKeys = pageKeys;
    await writeJson(path.join(this.config.recordingsDir, session.id, "session.json"), session);
  }

  private async writeCatalog(capabilities: CapabilityContract[]) {
    this.catalogCache = normalizeCatalog(capabilities);
    await writeJson(this.catalogFile(), this.catalogCache);
  }

  private sliceNeedsExtraEvents(slice: CapabilityContract[], events: EvidenceEvent[]) {
    const have = new Set(events.map(event => event.id));
    return slice.some(capability => capability.evidence.some(ref => !have.has(ref.eventId)));
  }

  private async eventsFromRefs(capabilities: CapabilityContract[], haveEvents: EvidenceEvent[]) {
    const have = new Set(haveEvents.map(event => event.id));
    const sessionIds = new Set<string>();
    for (const capability of capabilities) {
      for (const ref of capability.evidence) {
        if (!have.has(ref.eventId) && ref.sessionId) sessionIds.add(ref.sessionId);
      }
    }
    if (!sessionIds.size) return [];
    const extra = (await Promise.all([...sessionIds].map(id => this.sessionEvents(id).catch(() => [] as EvidenceEvent[])))).flat();
    return extra.filter(event => !have.has(event.id));
  }

  private async resolveSessionId(sessionId?: string, sessions?: RecordingSession[]) {
    const list = sessions || await this.listSessions();
    if (sessionId && list.some(item => item.id === sessionId)) return sessionId;
    if (this.lastAnalyzedSessionId && list.some(item => item.id === this.lastAnalyzedSessionId)) {
      return this.lastAnalyzedSessionId;
    }
    return list[0]?.id;
  }

  private async scopedEvidence(sessionId?: string) {
    const sessions = await this.listSessions();
    const current = await this.resolveSessionId(sessionId, sessions);
    const scopeEvents = current ? await this.sessionEvents(current) : [];
    const session = current ? sessions.find(item => item.id === current) : undefined;
    if (session) await this.persistSessionPageKeys(session, scopeEvents);
    const catalog = await this.capabilities();
    const preliminary = sessionCatalogSlice(catalog, scopeEvents, scopeEvents);
    const extraEvents = this.sliceNeedsExtraEvents(preliminary, scopeEvents)
      ? await this.eventsFromRefs(preliminary, scopeEvents)
      : [];
    return { current, session, scopeEvents, events: extraEvents.length ? [...scopeEvents, ...extraEvents] : scopeEvents, catalog };
  }

  async capabilities(): Promise<CapabilityContract[]> {
    if (this.catalogCache) return this.catalogCache;
    this.catalogCache = normalizeCatalog(await readJson<CapabilityContract[]>(this.catalogFile(), []));
    return this.catalogCache;
  }

  async analyze(sessionId?: string, useLlm = true) {
    const sessions = await this.listSessions();
    const latest = sessionId || sessions[0]?.id;
    await this.rememberAnalyzedSession(latest);
    const events = latest ? await this.sessionEvents(latest) : [];
    const session = latest ? sessions.find(item => item.id === latest) : undefined;
    if (session) await this.persistSessionPageKeys(session, events);
    const existing = await this.capabilities();

    let candidates = await applyPiCatalogJudgment(
      buildCapabilityCandidates(events),
      events,
      this.reasoner,
      this.config.rootDir,
      useLlm
    );

    candidates = reanalyzeIncoming(candidates, existing);
    candidates = mergeCatalogByTransport(candidates, existing);
    const scoped = capabilitiesForSession(candidates, events, events);
    const related = usableRelatedLookups(candidates, scoped, events);
    const extra = await this.eventsFromRefs([...scoped, ...related], events);
    const allEvents = extra.length ? [...events, ...extra] : events;
    const judged = await applyPiCatalogJudgment(
      [...scoped, ...related.filter(item => !scoped.some(current => current.id === item.id))],
      allEvents,
      this.reasoner,
      this.config.rootDir,
      useLlm
    );
    candidates = mergeCatalogByTransport(judged, candidates);
    const slice = sessionCatalogSlice(candidates, allEvents, events);
    const finalized = finalizeSessionSlice(slice, allEvents, candidates);
    await this.writeCatalog(mergeCatalogByTransport(finalized, candidates));
    return sessionCatalogSlice(this.catalogCache || candidates, allEvents, events);
  }

  async validate(sessionId?: string) {
    const { capabilities } = await this.review(sessionId);
    return capabilities;
  }

  async review(sessionId?: string) {
    let scoped = await this.scopedEvidence(sessionId);
    await this.rememberAnalyzedSession(scoped.current);
    let result = await this.reviewScopedEvidence(scoped, false);
    const state = await this.studioState();
    const signature = reviewFindingSignature(result.review.findings);
    const alreadySettled = Boolean(
      result.review.status === "blocked"
      && state.lastReview
      && state.lastReview.sessionId === scoped.session?.id
      && state.lastReview.signature === signature
    );

    if (!alreadySettled && scoped.current) {
      result = await this.reviewScopedEvidence(scoped, true);
      const seen = new Set<string>();
      const steps = [
        () => this.repairSessionContracts(scoped),
        () => this.analyze(scoped.current!, false)
      ];
      for (const step of steps) {
        const repairable = result.review.findings.filter(finding => finding.next === "re-analyze");
        if (!repairable.length) break;
        const repairSignature = reviewFindingSignature(repairable);
        if (seen.has(repairSignature)) break;
        seen.add(repairSignature);
        await step();
        scoped = await this.scopedEvidence(scoped.current);
        result = await this.reviewScopedEvidence(scoped, true);
      }
    } else if (alreadySettled && result.review.next !== "re-record") {
      result = {
        ...result,
        review: {
          ...result.review,
          summary: `审核结果与上次相同，已停止自动修复。不要再分析、不要开新录制。\n${result.review.summary}`
        }
      };
    }
    await this.rememberReview(scoped.session, result.review);
    return result;
  }

  private async rememberReview(
    session: RecordingSession | undefined,
    review: { status: "passed" | "blocked"; next: ReviewNext; findings: Array<{ code: string; capabilityId?: string; fieldPath?: string }> }
  ) {
    const state = await this.studioState();
    state.lastReview = {
      sessionId: session?.id,
      status: review.status,
      next: review.next,
      allowRerecord: review.status === "blocked" && review.next === "re-record",
      pageKeys: session?.pageKeys?.length
        ? session.pageKeys
        : session?.startUrl
          ? [evidencePageKey(session.startUrl)]
          : [],
      startUrl: session?.startUrl,
      signature: reviewFindingSignature(review.findings)
    };
    await writeJson(this.stateFile(), state);
  }

  async evaluateRerecord(url: string) {
    const state = await this.studioState();
    const last = state.lastReview;
    if (!last || last.status !== "blocked" || last.allowRerecord) return { allowed: true as const };
    const page = evidencePageKey(url);
    const samePage = last.pageKeys.includes(page)
      || sameReviewPage(last.startUrl, url)
      || last.pageKeys.some(key => sameReviewPage(key, url));
    if (!samePage) return { allowed: true as const };
    return { allowed: false as const, message: rerecordBlockedMessage(last.next) };
  }

  private async repairSessionContracts(scoped: {
    scopeEvents: EvidenceEvent[];
    events: EvidenceEvent[];
  }) {
    const catalog = await this.capabilities();
    const slice = sessionCatalogSlice(catalog, scoped.events, scoped.scopeEvents);
    const extra = this.sliceNeedsExtraEvents(slice, scoped.events)
      ? await this.eventsFromRefs(slice, scoped.events)
      : [];
    const events = extra.length ? [...scoped.events, ...extra] : scoped.events;
    const related = usableRelatedLookups(catalog, slice, scoped.scopeEvents);
    const derived = await applyPiCatalogJudgment(
      [...slice, ...related.filter(item => !slice.some(current => current.id === item.id))],
      events,
      this.reasoner,
      this.config.rootDir,
      true
    );
    const merged = mergeCatalogByTransport(derived, catalog);
    const nextSlice = sessionCatalogSlice(merged, events, scoped.scopeEvents);
    const finalized = finalizeSessionSlice(nextSlice, events, merged);
    await this.writeCatalog(mergeCatalogByTransport(finalized, merged));
  }

  private async reviewScopedEvidence(scoped: {
    session?: RecordingSession;
    scopeEvents: EvidenceEvent[];
    events: EvidenceEvent[];
    catalog: CapabilityContract[];
  }, persist = true) {
    const { session, scopeEvents, events, catalog } = scoped;
    const slice = sessionCatalogSlice(catalog, events, scopeEvents);
    const validated = finalizeSessionSlice(slice, events, catalog);
    if (persist && validated !== slice) await this.writeCatalog(mergeCatalogByTransport(validated, catalog));
    return reviewSession(validated, events, scopeEvents, session?.expectedOperations, session?.completeFieldCoverage);
  }

  async sealWrites() {
    const { scopeEvents, events, catalog } = await this.scopedEvidence();
    const slice = sessionCatalogSlice(catalog, events, scopeEvents);
    const sealedSlice = sealWriteCapabilities(slice, events);
    const sealed = mergeCatalogByTransport(sealedSlice, catalog);
    await this.writeCatalog(sealed);
    return sealed;
  }

  private async exportCatalog(sessionId?: string) {
    const scoped = await this.scopedEvidence(sessionId);
    const state = await this.studioState();
    if (state.lastReview?.status === "passed" && state.lastReview.sessionId === scoped.session?.id) {
      const checked = await this.reviewScopedEvidence(scoped, false);
      if (checked.review.status === "passed") return { catalog: checked.capabilities, events: scoped.events };
    }
    const reviewed = await this.review(sessionId);
    if (reviewed.review.status !== "passed") throw new Error(reviewed.review.summary);
    return { catalog: reviewed.capabilities, events: scoped.events };
  }

  async routes() {
    return buildApprovedRoutes(await this.capabilities());
  }

  async updateCapability(capabilityId: string, input: {
    title?: string;
    description?: string;
    operation?: OperationKind;
    fields?: Array<Partial<InputFormField> & { path: string }>;
  }) {
    const capabilities = await this.capabilities();
    const capability = capabilities.find(item => item.id === capabilityId);
    if (!capability) throw new Error("能力不存在");
    const now = new Date().toISOString();
    capability.editing ||= { title: "generated", description: "generated", fields: "generated" };
    if (input.title !== undefined) {
      if (!input.title.trim()) throw new Error("能力名称不能为空");
      capability.title = input.title.trim();
      capability.editing.title = "manual";
    }
    if (input.description !== undefined) {
      if (!input.description.trim()) throw new Error("业务描述不能为空");
      capability.description = input.description.trim();
      capability.editing.description = "manual";
    }
    if (input.operation !== undefined) {
      if (!["query", "create", "update", "review", "delete", "authenticate", "upload", "download", "action", "unknown"].includes(input.operation)) {
        throw new Error("不支持的原子能力类型");
      }
      capability.operation = input.operation;
      capability.editing.operation = "manual";
      capability.sideEffect = ["create", "update", "review", "delete", "upload", "action"].includes(input.operation);
      capability.confirmation = {
        required: capability.sideEffect,
        reason: capability.sideEffect ? "该操作会改变业务或文件数据" : undefined
      };
    }
    if (input.fields) {
      for (const patch of input.fields) {
        const field = capability.inputForm.find(item => item.path === patch.path);
        if (!field) throw new Error(`不能添加没有录制证据的字段：${patch.path}`);
        if (patch.path !== field.path) throw new Error("不能修改字段路径");
        if (patch.source && !["caller", "fixed", "session", "generated", "computed", "binding", "system"].includes(patch.source)) {
          throw new Error(`未知字段来源：${patch.source}`);
        }
        if (patch.valueType && !["string", "number", "integer", "boolean", "array", "object", "unknown"].includes(patch.valueType)) {
          throw new Error(`未知字段类型：${patch.valueType}`);
        }
        if (patch.defaultRule !== undefined && patch.defaultRule && !/^(literal:.+|env:[A-Za-z_][A-Za-z0-9_]*|uuid|now:iso|from:[^|]+(?:\|via:[A-Za-z_][A-Za-z0-9_]*)?(?:\|fallback:.*)?|computed:.+|copy:[A-Za-z_][A-Za-z0-9_]*)$/.test(patch.defaultRule)) {
          throw new Error(`字段 ${patch.path} 的处理规则不可执行`);
        }
        Object.assign(field, patch, { path: field.path });
        field.systemHandled = field.source !== "caller";
        field.requiredBasis = patch.required !== undefined ? "manual" : field.requiredBasis;
        field.sourceDetail = patch.sourceDetail || (field.source === "caller" ? "由调用方提供（人工确认）" : "由系统处理（人工确认）");
        capability.editing.fieldPaths = [...new Set([...(capability.editing.fieldPaths || []), patch.path])];
      }
      capability.editing.fields = "manual";
    }
    capability.editing.updatedAt = now;
    capability.validation = { version: 2, status: "candidate", checks: [] };
    await this.writeCatalog(capabilities);
    return capability;
  }

  async approveBinding(input: {
    fromCapabilityId: string;
    fromPath: string;
    toCapabilityId: string;
    toPath: string;
    note?: string;
  }) {
    const caps = await this.capabilities();
    const from = caps.find(c => c.id === input.fromCapabilityId);
    const to = caps.find(c => c.id === input.toCapabilityId);
    if (!from || !to) throw new Error("Both source and target capabilities must exist");
    if (from.id === to.id) throw new Error("能力不能绑定到自身");
    if (from.operation !== "query") throw new Error("绑定来源必须是已录制查询");
    const targetField = to.inputForm.find(field => field.path === input.toPath);
    if (!targetField) throw new Error("绑定目标必须是已录制的输入字段");
    if (!schemaPathExists(from.outputSchema, input.fromPath)) {
      throw new Error("绑定来源路径不在已录制的返回结构中");
    }
    const adjacency = new Map<string, Set<string>>();
    for (const targetCapability of caps) {
      for (const binding of targetCapability.bindings.filter(item => item.approved)) {
        const targets = adjacency.get(binding.fromCapabilityId) || new Set<string>();
        targets.add(targetCapability.id);
        adjacency.set(binding.fromCapabilityId, targets);
      }
    }
    const reachesSource = (current: string, visited = new Set<string>()): boolean => {
      if (current === from.id) return true;
      if (visited.has(current)) return false;
      visited.add(current);
      return [...(adjacency.get(current) || [])].some(next => reachesSource(next, visited));
    };
    if (reachesSource(to.id)) throw new Error("该绑定会形成循环执行路线");
    const existing = to.bindings.find(b =>
      b.fromCapabilityId === from.id && b.fromPath === input.fromPath && b.toPath === input.toPath
    );
    if (existing) {
      existing.approved = true;
      existing.approvalSource = "human";
      existing.approvedAt = new Date().toISOString();
      existing.note = input.note ?? existing.note;
    } else {
      to.bindings.push({
        id: id("bind"),
        fromCapabilityId: from.id,
        fromPath: input.fromPath,
        toPath: input.toPath,
        confidence: 1,
        evidenceIds: [],
        approved: true,
        approvalSource: "human",
        approvedAt: new Date().toISOString(),
        note: input.note || "Human-confirmed data binding"
      });
    }
    targetField.source = "binding";
    targetField.systemHandled = true;
    targetField.required = false;
    targetField.sourceDetail = `由已确认绑定从 ${from.id}${input.fromPath} 提供`;
    targetField.defaultRule = `from:${from.id}:${input.fromPath}`;
    to.validation = { version: 2, status: "candidate", checks: [] };
    await this.writeCatalog(caps);
    return to;
  }

  async setDynamicCandidates(input: {
    targetCapabilityId: string;
    inputPath: string;
    sourceCapabilityId: string;
    valuePath: string;
    labelPath: string;
    dependsOn?: string[];
  }) {
    const caps = await this.capabilities();
    const target = caps.find(c => c.id === input.targetCapabilityId);
    const source = caps.find(c => c.id === input.sourceCapabilityId);
    if (!target || !source) throw new Error("Target and source capabilities must exist");
    if (source.validation.status !== "verified" || source.operation !== "query") {
      throw new Error("Dynamic candidate source must be a verified query capability");
    }
    if (!schemaPathExists(source.outputSchema, input.valuePath) || !schemaPathExists(source.outputSchema, input.labelPath)) {
      throw new Error("候选值或显示名称路径不在已录制的返回结构中");
    }
    let field = target.inputForm.find(f => f.path === input.inputPath);
    if (!field) {
      throw new Error("候选规则只能配置到已录制的输入字段");
    }
    if (field.source !== "caller") throw new Error("动态候选只能配置给调用方选择的字段");
    field.widget = "select";
    field.candidates = {
      type: "capability",
      capabilityId: source.id,
      valuePath: input.valuePath,
      labelPath: input.labelPath,
      dependsOn: input.dependsOn || []
    };
    target.editing ||= { title: "generated", description: "generated", operation: "generated", fields: "generated" };
    target.editing.fields = "manual";
    target.editing.fieldPaths = [...new Set([...(target.editing.fieldPaths || []), field.path])];
    target.validation = { version: 2, status: "candidate", checks: [] };
    await this.writeCatalog(caps);
    return target;
  }

  async plan(goal: string): Promise<{ plan: ExecutionPlan; policy: ReturnType<typeof applyPlanPolicy> }> {
    const caps = await this.capabilities();
    const plan = (await this.reasoner.plan(goal, caps)) || fallbackPlan(goal, caps);
    return { plan, policy: applyPlanPolicy(plan, caps) };
  }

  async execute(capabilityId: string, input: Record<string, unknown>, confirmWrite = false) {
    const caps = await this.capabilities();
    const cap = caps.find(c => c.id === capabilityId);
    if (!cap) throw new Error(`Unknown capability: ${capabilityId}`);
    return executeCapability(cap, input, confirmWrite, caps);
  }

  async export(name: string, outputRoot = defaultSkillOutputRoot(this.config.rootDir), match: string[] = [], sessionId?: string) {
    const { catalog, events } = await this.exportCatalog(sessionId);
    const exported = await exportSkill(outputRoot, name, catalog, match, events);
    const exportedIds = new Set(exported.capabilityIds);
    const exportedCapabilities = catalog.filter(capability => exportedIds.has(capability.id));
    const credentialFile = await materializeSkillCredentials(
      this.config.dataDir,
      outputRoot,
      exported.skillName,
      exportedCapabilities.map(capability => capability.transport.origin),
      requiredCredentialOrigins(exportedCapabilities, events)
    );
    return { ...exported, credentialFile };
  }

  async listSkills() {
    return this.skillLibrary.list();
  }

  async exportManaged(name: string, confirmed: boolean, sessionId?: string) {
    const { catalog, events } = await this.exportCatalog(sessionId);
    return this.skillLibrary.export(name, catalog, confirmed, events);
  }

  async setSkillFrozen(name: string, frozen: boolean, confirmed: boolean) {
    return this.skillLibrary.setFrozen(name, frozen, confirmed);
  }

  async deleteSkill(name: string, confirmed: boolean) {
    return this.skillLibrary.delete(name, confirmed);
  }

  async invokeSkill(name: string, goal: string) {
    return this.skillLibrary.invocation(name, goal);
  }
}
