import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";

type EventListener = (event: any) => void;

export class PiRpcBridge {
  private child?: ChildProcessWithoutNullStreams;
  private nextId = 0;
  private ready = false;
  private streaming = false;
  private readonly pending = new Map<string, {
    resolve: (value: any) => void;
    reject: (error: Error) => void;
    timer: NodeJS.Timeout;
  }>();
  private readonly listeners = new Set<EventListener>();
  private readonly pendingUiRequests = new Set<string>();

  constructor(
    private readonly cwd: string,
    private readonly browserServiceUrl: string,
    private readonly browserServiceToken: string
  ) {}

  subscribe(listener: EventListener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  status() {
    return {
      ready: this.ready,
      streaming: this.streaming,
      running: Boolean(this.child && this.child.exitCode === null)
    };
  }

  async start() {
    if (this.child && this.child.exitCode === null) return;
    const rpcEntry = path.join(
      this.cwd,
      "node_modules",
      "@earendil-works",
      "pi-coding-agent",
      "dist",
      "bundle",
      "rpc-entry.js"
    );
    const provider = process.env.PI_PROVIDER || "xiaomi-token-plan-cn";
    const model = process.env.PI_MODEL;
    const args = [rpcEntry, "--approve", "--no-session", "--provider", provider];
    if (model) args.push("--model", model);
    args.push(
      "--thinking",
      process.env.PI_THINKING || "medium",
      "--append-system-prompt",
      [
        "You are running inside the Pi Business Skill Studio web interface.",
        "For browser work, use business_skill_record_start and business_browser_control; the Playwright page is shown in the embedded browser panel.",
        "Always call snapshot before choosing selectors. Never open or control a separate local browser.",
        "Only click, fill, select, press, or navigate when the interface is in Pi automatic click mode; manual recording mode is controlled by the user.",
        "Browser writes and business capability writes must wait for the existing explicit confirmation dialog."
      ].join(" ")
    );

    this.child = spawn(process.execPath, args, {
      cwd: this.cwd,
      env: {
        ...process.env,
        BSS_BROWSER_SERVICE_URL: this.browserServiceUrl,
        BSS_BROWSER_SERVICE_TOKEN: this.browserServiceToken
      },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true
    });
    this.attachJsonl(this.child.stdout);
    this.child.stderr.on("data", chunk => {
      const message = String(chunk).trim();
      if (message) this.emit({ type: "agent_diagnostic", message });
    });
    this.child.on("exit", code => {
      this.ready = false;
      this.streaming = false;
      const error = new Error(`Pi process exited with code ${code ?? "unknown"}`);
      for (const request of this.pending.values()) {
        clearTimeout(request.timer);
        request.reject(error);
      }
      this.pending.clear();
      this.emit({ type: "agent_process_exit", code });
    });

    await this.request({ type: "get_state" }, 20_000);
    this.ready = true;
    this.emit({ type: "agent_ready" });
  }

  async prompt(message: string) {
    if (!this.ready) throw new Error("Pi is still starting");
    return this.request({
      type: "prompt",
      message,
      ...(this.streaming ? { streamingBehavior: "followUp" } : {})
    });
  }

  async abort() {
    return this.request({ type: "abort" });
  }

  respondToUi(input: { id: string; confirmed?: boolean; value?: string; cancelled?: boolean }) {
    if (!this.pendingUiRequests.has(input.id)) throw new Error("Unknown or completed confirmation request");
    this.pendingUiRequests.delete(input.id);
    this.write({ type: "extension_ui_response", ...input });
  }

  stop() {
    this.ready = false;
    this.child?.kill();
    this.child = undefined;
  }

  private request(command: Record<string, unknown>, timeout = 15_000): Promise<any> {
    const id = `web-${++this.nextId}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Pi command timed out: ${String(command.type)}`));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.write({ id, ...command });
    });
  }

  private write(message: unknown) {
    if (!this.child || this.child.exitCode !== null) throw new Error("Pi process is not running");
    this.child.stdin.write(`${JSON.stringify(message)}\n`);
  }

  private attachJsonl(stream: NodeJS.ReadableStream) {
    const decoder = new StringDecoder("utf8");
    let buffer = "";
    stream.on("data", chunk => {
      buffer += decoder.write(chunk as Buffer);
      while (true) {
        const newline = buffer.indexOf("\n");
        if (newline < 0) break;
        const line = buffer.slice(0, newline).replace(/\r$/, "");
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        try {
          this.handle(JSON.parse(line));
        } catch {
          this.emit({ type: "agent_diagnostic", message: "Pi emitted an invalid protocol message" });
        }
      }
    });
  }

  private handle(event: any) {
    if (event.type === "response" && event.id) {
      const request = this.pending.get(event.id);
      if (request) {
        this.pending.delete(event.id);
        clearTimeout(request.timer);
        if (event.success) request.resolve(event.data);
        else request.reject(new Error(event.error || `Pi command failed: ${event.command}`));
      }
      return;
    }
    if (event.type === "agent_start") this.streaming = true;
    if (event.type === "agent_settled") this.streaming = false;
    if (event.type === "extension_ui_request" && ["confirm", "select", "input", "editor"].includes(event.method)) {
      this.pendingUiRequests.add(event.id);
    }
    this.emit(event);
  }

  private emit(event: any) {
    for (const listener of this.listeners) listener(event);
  }
}
