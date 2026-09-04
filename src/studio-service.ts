import path from "node:path";
import { readdir } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent, ExecutionPlan, InputFormField, OperationKind, RecordingSession } from "./domain.js";
import type { StudioConfig } from "./config.js";
import { loadConfig } from "./config.js";
import { BrowserRecorder } from "./browser/recorder.js";
import { id, readJson, readJsonl, writeJson } from "./utils.js";
import { buildCapabilityCandidates } from "./inference/build-candidates.js";
import { finalizeSessionSlice, sealWriteCapabilities, sessionExportReady } from "./inference/finalize-capabilities.js";
import { OpenAIReasoner } from "./llm/openai.js";
import { fallbackPlan } from "./planner/fallback.js";
import { applyPlanPolicy } from "./planner/policy.js";
import { exportSkill } from "./export/skill-exporter.js";
import { executeCapability } from "./execution/http-executor.js";
import { capabilitiesForSession, reviewSessionIds, sessionBusinessPageKeys, sessionCatalogSlice } from "./inference/export-scope.js";
import { mergeCatalogByTransport, normalizeCatalog } from "./catalog/normalize.js";
import { reanalyzeIncoming } from "./inference/reanalyze.js";
import { reviewSession } from "./review/catalog-review.js";
import { SkillLibrary } from "./catalog/skill-library.js";
import { buildApprovedRoutes } from "./planner/routes.js";

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
  private state?: { lastAnalyzedSessionId?: string };
  private sessionListCache?: RecordingSession[];
  private eventCache = new Map<string, EvidenceEvent[]>();
  private catalogCache?: CapabilityContract[];

  constructor(config = loadConfig()) {
    this.config = config;
    this.recorder = new BrowserRecorder(config);
    this.reasoner = new OpenAIReasoner(config.openaiModel);
    this.skillLibrary = new SkillLibrary(path.join(config.rootDir, "dist", "skills"), config.dataDir);
  }

  private catalogFile() {
    return path.join(this.config.catalogDir, "capabilities.json");
  }

  async startRecording(url: string, name?: string) {
    if (this.recorder.isActive()) await this.stopRecording();
    this.sessionListCache = undefined;
    return this.recorder.start(url, name);
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
    if (!this.state) this.state = await readJson<{ lastAnalyzedSessionId?: string }>(this.stateFile(), {});
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

  private async resolveSessionId(sessionId?: string, sessions?: RecordingSession[]) {
    const list = sessions || await this.listSessions();
    if (sessionId && list.some(item => item.id === sessionId)) return sessionId;
    return list[0]?.id;
  }

  private async scopedEvidence(sessionId?: string) {
    const sessions = await this.listSessions();
    const current = await this.resolveSessionId(sessionId, sessions);
    const scopeEvents = current ? await this.sessionEvents(current) : [];
    const session = current ? sessions.find(item => item.id === current) : undefined;
    if (session) await this.persistSessionPageKeys(session, scopeEvents);
    const catalog = await this.capabilities();
    const ids = current ? reviewSessionIds(sessions, current, scopeEvents) : new Set<string>();
    const preliminary = sessionCatalogSlice(catalog, scopeEvents, scopeEvents);
    const extraIds = this.sliceNeedsExtraEvents(preliminary, scopeEvents)
      ? [...ids].filter(id => id !== current)
      : [];
    const extraEvents = extraIds.length
      ? (await Promise.all(extraIds.map(id => this.sessionEvents(id)))).flat()
      : [];
    return { current, scopeEvents, events: extraEvents.length ? [...scopeEvents, ...extraEvents] : scopeEvents, catalog };
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

    let candidates = buildCapabilityCandidates(events);
    if (useLlm && this.reasoner.available()) {
      candidates = await Promise.all(candidates.map(c => this.reasoner.refineCapability(c)));
    }

    candidates = reanalyzeIncoming(candidates, existing);
    candidates = mergeCatalogByTransport(candidates, existing);
    await this.writeCatalog(candidates);
    return capabilitiesForSession(candidates, events, events);
  }

  async validate(sessionId?: string) {
    const { capabilities } = await this.review(sessionId);
    return capabilities;
  }

  async review(sessionId?: string) {
    const { current, scopeEvents, events, catalog } = await this.scopedEvidence(sessionId);
    await this.rememberAnalyzedSession(current);
    const slice = sessionCatalogSlice(catalog, events, scopeEvents);
    const validated = finalizeSessionSlice(slice, events, catalog);
    if (validated !== slice) await this.writeCatalog(mergeCatalogByTransport(validated, catalog));
    return reviewSession(validated, events, scopeEvents);
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
    const { current, scopeEvents, events, catalog } = await this.scopedEvidence(sessionId);
    await this.rememberAnalyzedSession(current);
    const slice = sessionCatalogSlice(catalog, events, scopeEvents);
    if (sessionExportReady(slice)) return { catalog: slice, events };
    const finalized = finalizeSessionSlice(slice, events, catalog);
    await this.writeCatalog(mergeCatalogByTransport(finalized, catalog));
    return {
      catalog: finalized,
      events
    };
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
        if (patch.defaultRule !== undefined && patch.defaultRule && !/^(literal:.+|env:[A-Za-z_][A-Za-z0-9_]*|uuid|now:iso|from:[^|]+(?:\|via:[A-Za-z_][A-Za-z0-9_]*)?|computed:.+|copy:[A-Za-z_][A-Za-z0-9_]*)$/.test(patch.defaultRule)) {
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
    if (from.validation.status !== "verified" || to.validation.status !== "verified") {
      throw new Error("Bindings can only be approved between verified capabilities");
    }
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
    targetField.sourceDetail = `由已确认绑定从 ${from.id}${input.fromPath} 提供`;
    targetField.defaultRule = undefined;
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

  async export(name: string, outputRoot = path.join(this.config.rootDir, "dist", "skills"), match: string[] = [], sessionId?: string) {
    const { catalog, events } = await this.exportCatalog(sessionId);
    return exportSkill(outputRoot, name, catalog, match, events);
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
