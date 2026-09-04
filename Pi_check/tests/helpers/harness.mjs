/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { RecordingFiles } from "../../src/fs-store.mjs";
import { EvidenceStore } from "../../src/evidence-store.mjs";
import { ResultGate } from "../../src/result-gate.mjs";
import { RecordingController } from "../../src/recording-controller.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export function sampleResult(overrides = {}) {
  return {
    recording_goal: "演示目标",
    business: { object: "leave", intent: "create" },
    capabilities: [
      {
        capability_id: "cap_create_leave",
        name: "create_leave",
        title: "创建请假",
        intent: "创建请假",
        kind: "write",
        request_refs: [{ step_id: "step_1", usage: "execute" }],
      },
    ],
    steps: [
      {
        step_id: "step_1",
        method: "POST",
        path: "/api/leave",
        params: [
          {
            key: "days",
            path: "body.days",
            label: "天数",
            type: "number",
            source_kind: "caller_input",
            required: true,
            exposed_to_user: true,
          },
        ],
      },
    ],
    links: [],
    request_order: ["req_1"],
    dependencies: [],
    success_condition: "status==200",
    failure_condition: "status>=400",
    evidence_refs: [1],
    unresolved: [],
    ...overrides,
  };
}

export class ScriptedPiSession {
  constructor({
    tools,
    recordingId,
    sessionId = "scripted-pi",
    behavior = "submit_on_final",
    result,
  }) {
    this.tools = tools;
    this.recordingId = recordingId;
    this.sessionId = sessionId;
    this.behavior = behavior;
    this.result = result || sampleResult();
    this.alive = true;
    this.started = true;
    this.unfrozenRejected = false;
    this.secondSubmitError = "";
    this.exitListeners = new Set();
  }

  onExit(listener) {
    this.exitListeners.add(listener);
  }

  kill() {
    this.alive = false;
    for (const listener of this.exitListeners) listener();
  }

  async notifyEvidence() {
    if (!this.alive) return;
    if (this.behavior === "submit_unfrozen") {
      try {
        await this.tools.submit_recording_result({
          recording_id: this.recordingId,
          final: true,
          result: this.result,
        });
      } catch {
        this.unfrozenRejected = true;
      }
    }
    if (this.behavior === "die_on_notify") {
      this.kill();
    }
  }

  async requestFinalAnalysis() {
    if (!this.alive) throw new Error("PI 在录制期间退出");
    if (this.behavior === "never_submit" || this.behavior === "submit_unfrozen") return;
    if (this.behavior === "empty_result") {
      await this.tools.submit_recording_result({
        recording_id: this.recordingId,
        final: true,
        result: {},
      });
      return;
    }
    if (this.behavior === "wrong_id") {
      await this.tools.submit_recording_result({
        recording_id: "rec_does_not_match",
        final: true,
        result: this.result,
      });
      return;
    }
    if (this.behavior === "not_final") {
      await this.tools.submit_recording_result({
        recording_id: this.recordingId,
        final: false,
        result: this.result,
      });
      return;
    }
    await this.tools.submit_recording_result({
      recording_id: this.recordingId,
      final: true,
      result: this.result,
    });
    if (this.behavior === "submit_twice") {
      try {
        await this.tools.submit_recording_result({
          recording_id: this.recordingId,
          final: true,
          result: { ...this.result, injected_by_second_submit: true },
        });
      } catch (error) {
        this.secondSubmitError = error.message;
      }
    }
  }

  async close() {
    this.alive = false;
  }
}

export class FakeBrowser {
  constructor({ appendEvidence, emitOnStart = true } = {}) {
    this.started = true;
    this.closed = false;
    this.appendEvidence = appendEvidence;
    this.ready = emitOnStart
      ? Promise.resolve(appendEvidence?.("network_request", {
        method: "GET",
        url: "http://fixture.local/demo",
        resource_type: "xhr",
      }))
      : Promise.resolve();
  }

  async close() {
    this.closed = true;
    this.started = false;
  }
}

export async function createHarness(options = {}) {
  const dir = await mkdtemp(path.join(ROOT, "data", "tmp-"));
  const files = new RecordingFiles(dir);
  const evidence = new EvidenceStore(files);
  const gate = new ResultGate(files);
  const browserCalls = [];
  const piBehavior = options.piBehavior || "submit_on_final";
  const result = options.result;
  let piRef = null;
  const controller = new RecordingController({
    files,
    evidence,
    gate,
    finalTimeoutMs: options.finalTimeoutMs || 2000,
    createPi: options.createPi || (async ({ recording, tools }) => {
      if (options.piFailStart) {
        throw new Error("PI 初始化失败");
      }
      piRef = new ScriptedPiSession({
        tools,
        recordingId: recording.id,
        behavior: piBehavior,
        result,
        sessionId: options.piSessionId || "scripted-pi",
      });
      return piRef;
    }),
    createBrowser: options.createBrowser || (async ({ appendEvidence }) => {
      browserCalls.push(true);
      const browser = new FakeBrowser({ appendEvidence, emitOnStart: options.emitOnStart !== false });
      await browser.ready;
      return browser;
    }),
  });
  return {
    root: ROOT,
    dir,
    files,
    evidence,
    gate,
    controller,
    browserCalls,
    getPi: () => piRef,
    async cleanup() {
      await rm(dir, { recursive: true, force: true });
    },
  };
}
