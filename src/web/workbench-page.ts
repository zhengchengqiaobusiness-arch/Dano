import { randomBytes } from "node:crypto";
import type { ServerResponse } from "node:http";
import path from "node:path";
import { seedPageProfile, syncLoginState } from "../browser/login-profile.js";
import { BrowserRecorder } from "../browser/recorder.js";
import type { StudioConfig } from "../config.js";
import { PiRpcBridge } from "./pi-rpc.js";
import { PiTranscript } from "./transcript.js";

export type BrowserMode = "manual" | "automatic";
export type PageLogLevel = "PLAIN" | "CHECK" | "START" | "INFO" | "READY" | "BROWSER" | "PI" | "TOOL" | "WAIT" | "WARN" | "ERROR";

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
  private readonly sharedProfileDir: string;
  private readonly pageProfileDir: string;
  private starting?: Promise<void>;

  constructor(
    id: string,
    config: StudioConfig,
    origin: string,
    sanitize: (value: unknown) => unknown,
    private readonly onLog: (level: PageLogLevel, message: unknown) => void
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
    if (this.pi.status().ready || this.pi.status().running) return;
    if (!this.starting) {
      this.starting = this.pi.start().catch(error => {
        this.starting = undefined;
        throw error;
      });
    }
    await this.starting;
  }

  async browserState() {
    return { ...(await this.recorder.state()), mode: this.mode };
  }

  setMode(mode: BrowserMode) {
    if (this.mode === mode) return;
    this.mode = mode;
    this.onLog("BROWSER", mode === "manual" ? "Switched to manual recording mode." : "Switched to Pi automatic click mode.");
    this.broadcast({ type: "browser_mode", mode });
  }

  async startRecording(url: string, name?: string, viewport?: { width?: number; height?: number }) {
    if (this.recorder.isActive()) {
      await this.recorder.stop().catch(() => this.recorder.disposeImmediate());
      await this.rememberLogin();
    }
    await seedPageProfile(this.sharedProfileDir, this.pageProfileDir);
    return this.recorder.start(url, name || "web-session", viewport);
  }

  async stopRecording() {
    const session = await this.recorder.stop();
    await this.rememberLogin();
    return session;
  }

  async reset() {
    this.epoch += 1;
    this.transcriptOpen = false;
    if (this.recorder.isActive()) {
      const closed = this.recorder.disposeImmediate();
      this.onLog("BROWSER", "Active recording was discarded so the next conversation starts clean.");
      void Promise.resolve(closed).then(() => this.rememberLogin());
    }
    await this.pi.beginFreshConversation().catch(error => {
      this.onLog("WARN", `Failed to start a new Pi session: ${error instanceof Error ? error.message : String(error)}`);
    });
    this.transcript.clear();
    this.broadcast({ type: "agent_status", ready: this.pi.status().ready, streaming: false });
    this.broadcast({ type: "browser_changed" });
    this.broadcast({ type: "session_reset", epoch: this.epoch, pageSession: this.id });
  }

  dispose() {
    this.pi.stop();
    void Promise.resolve(this.recorder.disposeImmediate()).then(() => this.rememberLogin());
    for (const client of this.clients) {
      try { client.end(); } catch { /* already closed */ }
    }
    this.clients.clear();
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
