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
  private suppressEvents = false;

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
    const windows = process.platform === "win32";
    const args = [rpcEntry, "--approve", "--no-session", "--provider", provider];
    if (model) args.push("--model", model);
    if (windows) args.push("--exclude-tools", "bash");
    args.push(
      "--thinking",
      process.env.PI_THINKING || "medium",
      "--append-system-prompt",
      [
        `You are running inside the Pi Business Skill Studio web interface on ${windows ? "Windows" : process.platform}.`,
        "思考过程、阶段状态、工具使用说明和最终回答均使用简体中文；必要的代码、接口路径、字段名和工具名保持原样。",
        windows
          ? "Host OS is Windows. The bash tool is disabled. If a shell is unavoidable, use the powershell tool. Never call bash, WSL, or Unix-only commands such as dir via bash. If a tool fails, do not retry it with a different shell."
          : "Use the host shell as provided. If a tool fails, do not retry the same command through another shell.",
        "Page work only uses business_skill_record_start and business_browser_control. Do not use bash, powershell, read, grep, ls, or filesystem tools to inspect or operate the business page.",
        "For browser work, use business_skill_record_start and business_browser_control; the Playwright page is shown in the embedded browser panel.",
        "Ground selectors with snapshot only at the start and after navigation, dialog open/close, or submit. Do not snapshot after every field.",
        "Never open or control a separate local browser.",
        "snapshot.formFields/todoFields are the visible field checklist, including empty assignee wells in a process rail. The node title is not a filled value. When the user requires every field filled except upload, call exercise-form at most twice, then submit-form at most twice. The first exercise-form failure is not a stop: call it again so leftover fields, 新增一行 rows, and phone/format repairs can finish. Do not click, fill, or choose individual fields after a failure. The same click/choose/fill may fail only once. followManualSteps becomes true only after the second exercise-form/submit-form failure or stopped=true; then follow recordedManualSteps or ask 手动录制. Never loop snapshot-click-snapshot, never retry a failed selector, never a third exercise-form. Do not record_stop+analyze a planned 新增/修改 with no successful write response. Locate must be unique for that field; never the first input in the dialog. Choosers click that field's shell once and pick a visible option or dialog row, not a typed sample. submit-form.ok is false unless a form request is seen or the form closes. Do not export a planned write without a successful write response.",
        "If snapshot.recentUserActions or filled controls already show the user's manual operation, keep those values. Still fill remaining empty fields when complete coverage was requested. Manual clicks or typing in Pi automatic mode must leave the same field labels, final values, options and write request as exercise-form; a bare click without a form inventory cannot export a Skill. When the user says 手动录制完毕, record_stop and analyze this session; do not start a new recording just because Chinese labels do not match request keys. If review cannot uniquely bind 单位/条码/单价 but this session already has the write request and product/simple-list, do not record_start; analyze that write session. A picker-only rerecord will drop 新建 from the catalog.",
        "The user can send follow-up messages while you are working. Read the new instruction and continue; do not ignore already recorded manual input.",
        "Prefer snapshot selectors that start with placeholder=, label=, role=, or text=. Never use generated #el-id-* selectors or long CSS paths such as div.el-select__wrapper > div.el-select__selection. Table cells use the visible column header as label=.",
        "For dropdowns and comboboxes, use action=choose with selector plus the visible option text in one call. Do not click the inner input and wait for a 30s timeout.",
        "For dates, fill or choose the field with YYYY-MM-DD. Never click text=2 or a calendar CSS cell on the whole page, and never click the dim overlay or blank area outside a dialog.",
        "If a dialog is open, only click controls inside that dialog. Do not press Escape to recover; that closes the dialog. Do not click the page title or a heading to close a date picker or dropdown.",
        "If an action fails because the page is still loading, wait 400-800ms once and retry choose; do not retry the same blocked click and do not take extra snapshots.",
        "Only click, fill, select, choose, press, or navigate when the interface is in Pi automatic click mode; manual recording mode is controlled by the user.",
        "Execute browser actions and business operations immediately. Do not ask the user to confirm clicks, fills, submits, logins, or other page operations.",
        "When analyzing, pass the sessionId from record_stop. After analyze, call validate. Export only when validate returns 审核通过. If it returns 审核未通过, read every finding first, group by stage, then do one rerecord pass only when a planned write has no successful write response. If the write and its lookups are already recorded, analyze that session again; do not record_start. Never bounce after a single finding and never freeze a recorded sample. Report only this session's 主能力 and 字段候选接口; do not go back to another page's 新建 because the merged catalog still has it. User/product pickers are not 主能力. A recorded keyword/filter request that returns rows/list/records is this page's 查询, even if the path is getXxx and not /list or /search. A recorded conversational ask with sys_query/question/prompt is also 查询, not a chat-send action; save_*chat* and getappid are lookups. Do not ask the user to rerecord that search. Do not claim 编辑/删除 unless those writes were recorded and verified."
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
    this.suppressEvents = false;
    return this.request({
      type: "prompt",
      message,
      ...(this.streaming ? { streamingBehavior: "followUp" } : {})
    });
  }

  async abort() {
    return this.request({ type: "abort" });
  }

  async newSession() {
    if (this.streaming) await this.abort().catch(() => {});
    this.streaming = false;
    this.cancelPendingUi();
    return this.request({ type: "new_session" });
  }

  async beginFreshConversation() {
    this.suppressEvents = true;
    this.cancelPendingUi();
    if (this.streaming) await this.abort().catch(() => {});
    this.streaming = false;
    if (!this.ready || !this.child || this.child.exitCode !== null) return;
    return this.request({ type: "new_session" });
  }

  cancelPendingUi() {
    for (const id of [...this.pendingUiRequests]) {
      this.pendingUiRequests.delete(id);
      try {
        this.write({ type: "extension_ui_response", id, cancelled: true });
      } catch {
        /* process may already be gone */
      }
    }
  }

  respondToUi(input: { id: string; confirmed?: boolean; value?: string; cancelled?: boolean }) {
    if (!this.pendingUiRequests.has(input.id)) throw new Error("Unknown or completed confirmation request");
    this.pendingUiRequests.delete(input.id);
    this.write({ type: "extension_ui_response", ...input });
  }

  stop() {
    this.ready = false;
    const child = this.child;
    this.child = undefined;
    if (!child?.pid) return;
    if (process.platform === "win32") {
      spawn("taskkill", ["/F", "/T", "/PID", String(child.pid)], { stdio: "ignore", windowsHide: true });
      return;
    }
    child.kill("SIGKILL");
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
    if (this.suppressEvents) {
      if (event.type === "agent_settled") this.streaming = false;
      if (event.type === "extension_ui_request") {
        try {
          this.write({ type: "extension_ui_response", id: event.id, cancelled: true });
        } catch {
          /* process may already be gone */
        }
        return;
      }
      if (event.type === "agent_ready" || event.type === "agent_process_exit" || event.type === "agent_diagnostic") {
        this.emit(event);
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
