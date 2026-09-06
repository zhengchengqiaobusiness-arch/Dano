import { randomBytes } from "node:crypto";
import type { ServerResponse } from "node:http";
import path from "node:path";
import { seedPageProfile, syncLoginState } from "../browser/login-profile.js";
import { BrowserRecorder, normalizePreviewViewport } from "../browser/recorder.js";
import type { StudioConfig } from "../config.js";
import type { OperationKind } from "../domain.js";
import { formatProcessLog } from "../process-lifecycle.js";
import { PiRpcBridge } from "./pi-rpc.js";
import { PiTranscript } from "./transcript.js";

export type BrowserMode = "manual" | "automatic";
export type PageLogLevel = "PLAIN" | "CHECK" | "START" | "INFO" | "READY" | "BROWSER" | "PI" | "TOOL" | "WAIT" | "WARN" | "ERROR" | "PROCESS";
export const PAGE_LEAVE_GRACE_MS = 3_000;

interface ManualTakeover {
  id: string;
  reason: string;
  requestedAt: string;
  returnMode: BrowserMode;
  waiting: Promise<{ completed: boolean }>;
  resolve: (result: { completed: boolean }) => void;
}

const PAGE_SESSION_PATTERN = /^page_[A-Za-z0-9_-]{8,80}$/;

export function isPageSessionId(value: string) {
  return PAGE_SESSION_PATTERN.test(value);
}

export function sendEvent(response: ServerResponse, payload: unknown) {
  response.write(`data: ${JSON.stringify(payload)}\n\n`);
}

export class WorkbenchPage {
  readonly id: string;
  readonly browserToken: string;
  readonly transcript: PiTranscript;
  readonly pi: PiRpcBridge;
  readonly recorder: BrowserRecorder;
  epoch = 0;
  transcriptOpen = true;
  mode: BrowserMode = "automatic";
  readonly clients = new Set<ServerResponse>();
  lastSeen = Date.now();
  lastRecordingSessionId?: string;
  preferredViewport?: { width: number; height: number; scale?: number };
  private readonly sharedProfileDir: string;
  private readonly pageProfileDir: string;
  private starting?: Promise<void>;
  private abandonTimer?: ReturnType<typeof setTimeout>;
  private disposing?: Promise<void>;
  private manualTakeover?: ManualTakeover;
  private coverageContinuationPending = false;
  private coverageContinuations = 0;
  private promptGeneration = 0;

  constructor(
    id: string,
    config: StudioConfig,
    origin: string,
    sanitize: (value: unknown) => unknown,
    private readonly onLog: (level: PageLogLevel, message: unknown) => void,
    private readonly onDisposed?: (page: WorkbenchPage, reason: string) => void
  ) {
    this.id = id;
    this.browserToken = randomBytes(32).toString("hex");
    this.sharedProfileDir = config.profileDir;
    this.pageProfileDir = path.join(config.profileDir, id);
    this.transcript = new PiTranscript(sanitize);
    this.recorder = new BrowserRecorder({
      ...config,
      headless: true,
      profileDir: this.pageProfileDir
    });
    this.pi = new PiRpcBridge(config.rootDir, origin, this.browserToken);
    this.attachPi();
  }

  touch() {
    this.lastSeen = Date.now();
  }

  broadcast(payload: unknown) {
    for (const client of this.clients) sendEvent(client, payload);
  }

  broadcastSession(payload: Record<string, unknown>) {
    this.broadcast({ ...payload, epoch: this.epoch, pageSession: this.id });
  }

  async ensureStarted() {
    if (this.pi.status().ready) return;
    if (this.starting) {
      await this.starting;
      return;
    }
    if (!this.starting) {
      this.starting = this.pi.start()
        .then(() => {
          const pid = this.pi.processId();
          if (pid) this.onLog("PROCESS", formatProcessLog("OPEN", "pi-rpc", { pid, page: this.id }));
        })
        .catch(error => {
          throw error;
        })
        .finally(() => {
          this.starting = undefined;
        });
    }
    await this.starting;
  }

  cancelAbandon() {
    if (!this.abandonTimer) return;
    clearTimeout(this.abandonTimer);
    this.abandonTimer = undefined;
  }

  scheduleAbandon(reason: string, delayMs = PAGE_LEAVE_GRACE_MS) {
    if (this.disposing) return;
    this.cancelAbandon();
    this.abandonTimer = setTimeout(() => {
      this.abandonTimer = undefined;
      if (this.clients.size > 0) return;
      void this.dispose(reason);
    }, delayMs);
  }

  async browserState() {
    return { ...(await this.recorder.state()), mode: this.mode, manualTakeover: this.manualTakeoverState() };
  }

  manualTakeoverState() {
    if (!this.manualTakeover) return undefined;
    const { id, reason, requestedAt } = this.manualTakeover;
    return { id, reason, requestedAt };
  }

  requestManualTakeover(reason: string) {
    if (this.manualTakeover) return this.manualTakeover.waiting;
    let resolve!: (result: { completed: boolean }) => void;
    const waiting = new Promise<{ completed: boolean }>(done => { resolve = done; });
    const takeover: ManualTakeover = {
      id: `takeover_${randomBytes(12).toString("hex")}`,
      reason,
      requestedAt: new Date().toISOString(),
      returnMode: this.mode,
      waiting,
      resolve
    };
    this.manualTakeover = takeover;
    this.setMode("manual");
    this.onLog("WAIT", `Automatic browser operation paused for manual takeover: ${reason}`);
    this.broadcast({ type: "manual_takeover_required", takeover: this.manualTakeoverState() });
    return waiting;
  }

  completeManualTakeover(id: string) {
    const takeover = this.manualTakeover;
    if (!takeover || takeover.id !== id) return false;
    this.manualTakeover = undefined;
    this.recorder.resumeAfterManualTakeover();
    this.setMode(takeover.returnMode);
    takeover.resolve({ completed: true });
    this.onLog("BROWSER", "Manual takeover completed; automatic execution resumed from the current page.");
    this.broadcast({ type: "manual_takeover_completed", id });
    return true;
  }

  private cancelManualTakeover(reason: string) {
    const takeover = this.manualTakeover;
    if (!takeover) return;
    this.manualTakeover = undefined;
    this.setMode(takeover.returnMode);
    takeover.resolve({ completed: false });
    this.broadcast({ type: "manual_takeover_cancelled", id: takeover.id, reason });
  }

  setMode(mode: BrowserMode) {
    if (this.manualTakeover && mode === "automatic") return;
    if (this.mode === mode) return;
    this.mode = mode;
    this.onLog("BROWSER", mode === "manual" ? "Switched to manual recording mode." : "Switched to Pi automatic click mode.");
    this.broadcast({ type: "browser_mode", mode });
  }

  async startRecording(
    url: string,
    name?: string,
    viewport?: { width?: number; height?: number; scale?: number },
    expectedOperations: OperationKind[] = [],
    completeFieldCoverage = false,
    completePageCoverage = false
  ) {
    this.coverageContinuations = 0;
    this.coverageContinuationPending = false;
    this.cancelManualTakeover("new-recording");
    if (this.recorder.isActive()) {
      const oldPid = this.recorder.browserProcessId();
      await this.recorder.stop().catch(() => this.recorder.disposeAndKill("rerecord"));
      this.onLog("PROCESS", formatProcessLog("CLOSE", "playwright-browser", { pid: oldPid, page: this.id, reason: "rerecord" }));
      await this.rememberLogin();
    }
    await seedPageProfile(this.sharedProfileDir, this.pageProfileDir);
    const size = viewport || this.preferredViewport;
    if (size) this.preferredViewport = normalizePreviewViewport(size);
    const session = await this.recorder.start(url, name || "web-session", this.preferredViewport, expectedOperations, completeFieldCoverage, completePageCoverage);
    this.lastRecordingSessionId = session.id;
    this.onLog("PROCESS", formatProcessLog("OPEN", "playwright-browser", { pid: this.recorder.browserProcessId(), page: this.id }));
    const login = await this.recorder.loginPageState();
    if (login.detected) await this.requestManualTakeover(login.reason || "检测到登录页面，请人工完成登录后继续");
    return session;
  }

  private scheduleCoverageContinuation() {
    if (this.coverageContinuationPending || this.manualTakeover || this.mode !== "automatic") return;
    const session = this.recorder.activeSession();
    if (!session || (!session.completePageCoverage && !session.completeFieldCoverage && !(session.expectedOperations || []).length)) return;
    this.coverageContinuationPending = true;
    setTimeout(() => {
      void (async () => {
        try {
          if (this.pi.status().streaming || this.manualTakeover || !this.recorder.activeSession()) return;
          const readiness = await this.recorder.stopReadiness();
          const remaining = readiness.pageCoverage?.remaining || 0;
          const missingPageOperations = readiness.missingPageOperations || [];
          const missingOperations = readiness.missingOperations || [];
          const missingFields = readiness.missingFields || [];
          const continuationLimit = Math.max(24, (readiness.pageCoverage?.discovered || 0) * 2);
          if (this.coverageContinuations >= continuationLimit) {
            this.onLog("ERROR", `Live recording audit did not finish after ${continuationLimit} automatic continuations.`);
            this.broadcast({ type: "agent_error", message: `实时审核闭环在 ${continuationLimit} 次自动续跑后仍未完成。` });
            return;
          }
          this.coverageContinuations += 1;
          if (readiness.ready) {
            this.onLog("PI", "Live recording audit passed; resuming the same task to stop and export.");
            await this.pi.prompt("当前同一录制的实时审核已经通过。不要继续点击页面；立即调用 business_skill_record_stop，然后调用 business_skill_export 完成内部分析、审核、修复、复审和导出。不要只总结结果。");
            return;
          }
          this.onLog("PI", `Continuing recording from live audit; ${remaining} pages, ${missingPageOperations.length} page operations, ${missingOperations.length} global operations, and ${missingFields.length} fields remain.`);
          const missing = missingPageOperations.slice(0, 8).map(item => `${item.label}（${item.operations.join("、")}）`).join("、");
          const operations = missingOperations.slice(0, 8).join("、");
          const fields = missingFields.slice(0, 8).map(field => field.label || field.name).filter(Boolean).join("、");
          await this.pi.prompt(`继续当前同一录制，不要总结、不要重启录制。实时审核 nextAction=${readiness.nextAction.action}；还有 ${remaining} 个页面未访问、${missingPageOperations.length} 个页面能力缺少成功证据${missing ? `：${missing}` : ""}、${missingOperations.length} 个总体操作缺口${operations ? `：${operations}` : ""}、${missingFields.length} 个字段未完成${fields ? `：${fields}` : ""}。只处理当前缺口；每次 business_browser_control 后读取 recordingAudit 并继续，直到 recordingAudit.ready=true，再结束录制并导出。`);
        } catch (error) {
          this.onLog("WARN", `Automatic full-page continuation failed: ${error instanceof Error ? error.message : String(error)}`);
        } finally {
          this.coverageContinuationPending = false;
        }
      })();
    }, 200);
  }

  async rememberViewport(viewport?: { width?: number; height?: number; scale?: number }) {
    const size = normalizePreviewViewport(viewport);
    this.preferredViewport = size;
    if (this.recorder.isActive()) await this.recorder.fitViewport(size);
    return size;
  }

  async stopRecording() {
    this.cancelManualTakeover("recording-stopped");
    const pid = this.recorder.browserProcessId();
    const session = await this.recorder.stop();
    if (session?.id) this.lastRecordingSessionId = session.id;
    this.onLog("PROCESS", formatProcessLog("CLOSE", "playwright-browser", { pid, page: this.id, reason: "stop-recording" }));
    await this.rememberLogin();
    return session;
  }

  acceptUserMessage(text: string) {
    this.transcriptOpen = true;
    const userEvent = this.transcript.addUser(text);
    this.broadcastSession(userEvent);
    return userEvent;
  }

  async runPrompt(message: string) {
    const generation = this.promptGeneration;
    try {
      await this.ensureStarted();
      if (generation !== this.promptGeneration || this.disposing) return;
      this.transcriptOpen = true;
      await this.pi.prompt(message);
    } catch (error) {
      if (generation !== this.promptGeneration || this.disposing) return;
      const text = error instanceof Error ? error.message : String(error);
      this.onLog("ERROR", text);
      this.broadcast({ type: "agent_error", message: text });
      this.broadcast({ type: "agent_status", ready: this.pi.status().ready, streaming: false });
    }
  }

  async abortWork(reason = "abort") {
    this.promptGeneration += 1;
    this.cancelManualTakeover(reason);
    const pid = this.pi.processId();
    if (this.pi.status().streaming) {
      try {
        await this.pi.abort();
        if (this.pi.status().streaming) await new Promise(resolve => setTimeout(resolve, 200));
        if (this.pi.status().streaming) throw new Error("abort did not stop streaming");
        this.onLog("PROCESS", formatProcessLog("CLOSE", "pi-rpc-task", { pid, page: this.id, reason }));
      } catch {
        if (pid) {
          await this.pi.stop();
          this.onLog("PROCESS", formatProcessLog("CLOSE", "pi-rpc", { pid, page: this.id, reason: `${reason}-force` }));
        }
        await this.ensureStarted();
      }
    }
    this.broadcast({ type: "agent_status", ready: this.pi.status().ready, streaming: false });
  }

  async reset() {
    this.epoch += 1;
    this.transcriptOpen = false;
    this.lastRecordingSessionId = undefined;
    this.coverageContinuations = 0;
    this.coverageContinuationPending = false;
    await this.abortWork("clear");
    if (this.recorder.isActive() || this.recorder.browserProcessId()) {
      const pid = this.recorder.browserProcessId();
      await this.recorder.disposeAndKill("clear");
      this.onLog("PROCESS", formatProcessLog("CLOSE", "playwright-browser", { pid, page: this.id, reason: "clear" }));
      this.onLog("BROWSER", "Active recording was discarded so the next conversation starts clean.");
      await this.rememberLogin();
    }
    const pid = this.pi.processId();
    if (pid || this.pi.status().ready || this.pi.status().running) {
      this.pi.stop();
      if (pid) this.onLog("PROCESS", formatProcessLog("CLOSE", "pi-rpc", { pid, page: this.id, reason: "clear" }));
    }
    this.transcript.clear();
    this.broadcast({ type: "agent_status", ready: false, streaming: false });
    this.broadcast({ type: "browser_changed" });
    this.broadcast({ type: "session_reset", epoch: this.epoch, pageSession: this.id });
    if (this.clients.size > 0) {
      void this.ensureStarted().catch(error => {
        this.onLog("WARN", `Failed to prestart Pi after clear: ${error instanceof Error ? error.message : String(error)}`);
      });
    }
  }

  async dispose(reason = "page-closed") {
    if (this.disposing) return this.disposing;
    this.disposing = this.disposeNow(reason);
    return this.disposing;
  }

  private async disposeNow(reason: string) {
    this.cancelAbandon();
    this.cancelManualTakeover(reason);
    this.transcriptOpen = false;
    const piPid = this.pi.processId();
    await this.pi.stop();
    if (piPid) this.onLog("PROCESS", formatProcessLog("CLOSE", "pi-rpc", { pid: piPid, page: this.id, reason }));
    const browserPid = this.recorder.browserProcessId();
    await this.recorder.disposeAndKill(reason);
    if (browserPid) this.onLog("PROCESS", formatProcessLog("CLOSE", "playwright-browser", { pid: browserPid, page: this.id, reason }));
    await this.rememberLogin().catch(() => {});
    for (const client of this.clients) {
      try { client.end(); } catch { /* already closed */ }
    }
    this.clients.clear();
    this.onLog("PROCESS", formatProcessLog("CLOSE", "workbench-page", { page: this.id, reason }));
    this.onDisposed?.(this, reason);
  }

  private async rememberLogin() {
    await syncLoginState(this.pageProfileDir, this.sharedProfileDir);
  }

  private attachPi() {
    this.pi.subscribe(event => {
      if (this.transcriptOpen) {
        for (const payload of this.transcript.handle(event)) this.broadcastSession(payload);
      }
      if (event.type === "agent_ready") {
        this.broadcast({ type: "agent_status", ready: true, streaming: false });
        this.onLog("READY", `Pi connected for page ${this.id}. Provider: ${process.env.PI_PROVIDER || "xiaomi-token-plan-cn"}; model: ${process.env.PI_MODEL || "provider default"}.`);
      }
      if (event.type === "agent_start") {
        if (!this.transcriptOpen) return;
        this.onLog("PI", `Natural-language task started on page ${this.id}.`);
        this.broadcast({ type: "agent_status", ready: true, streaming: true });
      }
      if (event.type === "agent_settled") {
        this.broadcast({ type: "agent_status", ready: true, streaming: false });
        this.onLog("PI", `Natural-language task completed on page ${this.id}.`);
        this.scheduleCoverageContinuation();
      }
      if (event.type === "extension_ui_request") {
        if (!this.transcriptOpen) {
          try { this.pi.respondToUi({ id: event.id, cancelled: true }); } catch { /* already cancelled */ }
          return;
        }
        if (event.method === "confirm") {
          this.onLog("PI", `Auto-approved confirmation: ${event.title || event.message || "operation"}.`);
          try {
            this.pi.respondToUi({ id: event.id, confirmed: true });
          } catch (error) {
            this.onLog("WARN", `Failed to auto-approve confirmation: ${error instanceof Error ? error.message : String(error)}`);
          }
          return;
        }
        this.onLog("WAIT", `Pi requested ${event.method || "user input"}.`);
        this.broadcast({
          type: "ui_request",
          id: event.id,
          method: event.method,
          title: event.title,
          message: event.message,
          options: event.options,
          placeholder: event.placeholder,
          prefill: event.prefill,
          notifyType: event.notifyType
        });
      }
      if (event.type === "agent_diagnostic") {
        this.onLog("WARN", event.message || "Pi reported a diagnostic message.");
        this.broadcast({ type: "agent_error", message: event.message || "Pi reported a diagnostic message" });
      }
      if (event.type === "agent_process_exit") {
        this.onLog("ERROR", event.message || `Pi process stopped with code ${event.code ?? "unknown"}.`);
        this.broadcast({ type: "agent_error", message: event.message || "Pi stopped unexpectedly" });
      }
    });
  }
}
