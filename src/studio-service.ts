import path from "node:path";
import { readdir } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent, ExecutionPlan, RecordingSession } from "./domain.js";
import type { StudioConfig } from "./config.js";
import { loadConfig } from "./config.js";
import { BrowserRecorder } from "./browser/recorder.js";
import { id, readJson, readJsonl, writeJson } from "./utils.js";
import { buildCapabilityCandidates } from "./inference/build-candidates.js";
import { OpenAIReasoner } from "./llm/openai.js";
import { validateCapability } from "./validation/validator.js";
import { fallbackPlan } from "./planner/fallback.js";
import { applyPlanPolicy } from "./planner/policy.js";
import { exportSkill } from "./export/skill-exporter.js";
import { executeCapability } from "./execution/http-executor.js";

export class StudioService {
  readonly config: StudioConfig;
  readonly recorder: BrowserRecorder;
  readonly reasoner: OpenAIReasoner;

  constructor(config = loadConfig()) {
    this.config = config;
    this.recorder = new BrowserRecorder(config);
    this.reasoner = new OpenAIReasoner(config.openaiModel);
  }

  private catalogFile() {
    return path.join(this.config.catalogDir, "capabilities.json");
  }

  async startRecording(url: string, name?: string) {
    return this.recorder.start(url, name);
  }

  async stopRecording() {
    return this.recorder.stop();
  }

  async listSessions(): Promise<RecordingSession[]> {
    try {
      const ids = await readdir(this.config.recordingsDir);
      const sessions = await Promise.all(
        ids.map(id => readJson<RecordingSession | null>(
          path.join(this.config.recordingsDir, id, "session.json"),
          null
        ))
      );
      return sessions.filter((s): s is RecordingSession => Boolean(s)).sort((a, b) => b.startedAt.localeCompare(a.startedAt));
    } catch (error: any) {
      if (error?.code === "ENOENT") return [];
      throw error;
    }
  }

  async sessionEvents(sessionId: string): Promise<EvidenceEvent[]> {
    return readJsonl<EvidenceEvent>(path.join(this.config.recordingsDir, sessionId, "events.jsonl"));
  }

  async allEvents(): Promise<EvidenceEvent[]> {
    const sessions = await this.listSessions();
    const chunks = await Promise.all(sessions.map(s => this.sessionEvents(s.id)));
    return chunks.flat();
  }

  async capabilities(): Promise<CapabilityContract[]> {
    return readJson<CapabilityContract[]>(this.catalogFile(), []);
  }

  async analyze(sessionId?: string, useLlm = true) {
    const events = sessionId ? await this.sessionEvents(sessionId) : await this.allEvents();
    const existing = await this.capabilities();
    const existingByTransport = new Map(existing.map(c => [
      `${c.transport.method}|${c.transport.urlTemplate}|${c.operation}`,
      c
    ]));

    let candidates = buildCapabilityCandidates(events);
    if (useLlm && this.reasoner.available()) {
      candidates = await Promise.all(candidates.map(c => this.reasoner.refineCapability(c)));
    }

    // Preserve human-edited descriptions, approved bindings, and validation until new validation is run.
    candidates = candidates.map(candidate => {
      const old = existingByTransport.get(`${candidate.transport.method}|${candidate.transport.urlTemplate}|${candidate.operation}`);
      if (!old) return candidate;
      return {
        ...candidate,
        id: old.id,
        title: old.title || candidate.title,
        description: old.description || candidate.description,
        bindings: old.bindings || [],
        validation: old.validation
      };
    });

    await writeJson(this.catalogFile(), candidates);
    return candidates;
  }

  async validate() {
    const events = await this.allEvents();
    const caps = await this.capabilities();
    const validated = caps.map(cap => validateCapability(cap, events));
    await writeJson(this.catalogFile(), validated);
    return validated;
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
    if (from.validation.status !== "verified" || to.validation.status !== "verified") {
      throw new Error("Bindings can only be approved between verified capabilities");
    }
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
    await writeJson(this.catalogFile(), caps);
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
    let field = target.inputForm.find(f => f.path === input.inputPath);
    if (!field) {
      field = { path: input.inputPath, label: input.inputPath.replace(/^\$\./, ""), required: false, widget: "select" };
      target.inputForm.push(field);
    }
    field.widget = "select";
    field.candidates = {
      type: "capability",
      capabilityId: source.id,
      valuePath: input.valuePath,
      labelPath: input.labelPath,
      dependsOn: input.dependsOn || []
    };
    await writeJson(this.catalogFile(), caps);
    return target;
  }

  async plan(goal: string): Promise<{ plan: ExecutionPlan; policy: ReturnType<typeof applyPlanPolicy> }> {
    const caps = await this.capabilities();
    const plan = (await this.reasoner.plan(goal, caps)) || fallbackPlan(goal, caps);
    return { plan, policy: applyPlanPolicy(plan, caps) };
  }

  async execute(capabilityId: string, input: Record<string, unknown>, confirmWrite = false) {
    const cap = (await this.capabilities()).find(c => c.id === capabilityId);
    if (!cap) throw new Error(`Unknown capability: ${capabilityId}`);
    return executeCapability(cap, input, confirmWrite);
  }

  async export(name: string, outputRoot = path.join(this.config.rootDir, "dist", "skills")) {
    return exportSkill(outputRoot, name, await this.capabilities());
  }
}
