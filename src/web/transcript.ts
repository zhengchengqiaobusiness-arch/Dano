export type TranscriptItem =
  | { id: string; kind: "message"; role: "user" | "assistant"; text: string; at: string; complete: boolean }
  | { id: string; kind: "thinking"; text: string; at: string; complete: boolean }
  | { id: string; kind: "tool"; toolCallId: string; toolName: string; args: unknown; result?: unknown; at: string; phase: "running" | "complete" | "error" };

function messageText(message: any): string {
  if (typeof message?.content === "string") return message.content;
  if (!Array.isArray(message?.content)) return "";
  return message.content.filter((block: any) => block?.type === "text" && typeof block.text === "string")
    .map((block: any) => block.text).join("\n");
}

function thinkingText(message: any): string {
  if (!Array.isArray(message?.content)) return "";
  return message.content.filter((block: any) => block?.type === "thinking" && typeof block.thinking === "string")
    .map((block: any) => block.thinking).join("\n");
}

export class PiTranscript {
  readonly items: TranscriptItem[] = [];
  private sequence = 0;
  private activeMessageId?: string;
  private activeThinkingId?: string;

  constructor(private readonly sanitize: (value: unknown) => unknown = value => value) {}

  addUser(text: string) {
    const item: TranscriptItem = { id: this.nextId("user"), kind: "message", role: "user", text, at: new Date().toISOString(), complete: true };
    this.push(item);
    return { type: "session_item", item };
  }

  addManual(observation: { eventType?: string; label?: string; name?: string; text?: string; value?: unknown; selector?: string }) {
    const clicked = observation.eventType === "click" || observation.eventType === "submit";
    const item: TranscriptItem = {
      id: this.nextId("manual"),
      kind: "tool",
      toolCallId: this.nextId("manual-call"),
      toolName: clicked ? "manual_page_click" : "manual_page_input",
      args: observation,
      result: { recorded: true },
      at: new Date().toISOString(),
      phase: "complete"
    };
    this.push(item);
    return { type: "session_item", item };
  }

  clear() {
    this.items.length = 0;
    this.sequence = 0;
    this.activeMessageId = undefined;
    this.activeThinkingId = undefined;
  }

  handle(event: any): any[] {
    const emitted: any[] = [];
    if (event.type === "agent_start") {
      this.activeMessageId = undefined;
      this.activeThinkingId = undefined;
    }
    if (event.type === "message_update") {
      const update = event.assistantMessageEvent || {};
      if (update.type === "thinking_delta") {
        const existed = Boolean(this.item(this.activeThinkingId, "thinking"));
        const item = this.activeThinking();
        item.text += String(update.delta || "");
        emitted.push(existed
          ? { type: "session_patch", id: item.id, appendText: String(update.delta || "") }
          : { type: "session_item", item });
      }
      if (update.type === "thinking_end") {
        const item = this.item(this.activeThinkingId, "thinking");
        if (item) {
          item.complete = true;
          emitted.push({ type: "session_patch", id: item.id, complete: true });
        }
        this.activeThinkingId = undefined;
      }
      if (update.type === "text_delta") {
        const existed = Boolean(this.item(this.activeMessageId, "message"));
        const item = this.activeMessage();
        item.text += String(update.delta || "");
        emitted.push(existed
          ? { type: "session_patch", id: item.id, appendText: String(update.delta || "") }
          : { type: "session_item", item });
      }
    }
    if (event.type === "tool_execution_start") {
      const item: TranscriptItem = {
        id: this.nextId("tool"), kind: "tool", toolCallId: String(event.toolCallId || this.nextId("call")),
        toolName: String(event.toolName || "unknown"), args: this.sanitize(event.args), at: new Date().toISOString(), phase: "running"
      };
      this.push(item);
      emitted.push({ type: "session_item", item });
    }
    if (event.type === "tool_execution_update") {
      const item = this.tool(event.toolCallId);
      if (item) {
        item.result = this.sanitize(event.partialResult);
        emitted.push({ type: "session_replace", item });
      }
    }
    if (event.type === "tool_execution_end") {
      const item = this.tool(event.toolCallId);
      if (item) {
        item.result = this.sanitize(event.result);
        item.phase = event.isError ? "error" : "complete";
        emitted.push({ type: "session_replace", item });
      }
    }
    if (event.type === "message_end" && event.message?.role === "assistant") {
      const finalText = messageText(event.message);
      const finalThinking = thinkingText(event.message);
      if (finalThinking) {
        const item = this.item(this.activeThinkingId, "thinking") || this.findLast("thinking", candidate => !candidate.complete);
        if (item) {
          item.text = finalThinking;
          item.complete = true;
          emitted.push({ type: "session_replace", item });
        } else {
          const created: TranscriptItem = { id: this.nextId("thinking"), kind: "thinking", text: finalThinking, at: new Date().toISOString(), complete: true };
          this.push(created); emitted.push({ type: "session_item", item: created });
        }
      }
      if (finalText) {
        const item = this.item(this.activeMessageId, "message");
        if (item) {
          item.text = finalText;
          item.complete = true;
          emitted.push({ type: "session_replace", item });
        } else {
          const created: TranscriptItem = { id: this.nextId("assistant"), kind: "message", role: "assistant", text: finalText, at: new Date().toISOString(), complete: true };
          this.push(created); emitted.push({ type: "session_item", item: created });
        }
      }
      this.activeMessageId = undefined;
      this.activeThinkingId = undefined;
    }
    return emitted;
  }

  private activeThinking() {
    const existing = this.item(this.activeThinkingId, "thinking");
    if (existing) return existing;
    const item: TranscriptItem = { id: this.nextId("thinking"), kind: "thinking", text: "", at: new Date().toISOString(), complete: false };
    this.activeThinkingId = item.id;
    this.push(item);
    return item;
  }

  private activeMessage() {
    const existing = this.item(this.activeMessageId, "message");
    if (existing) return existing;
    const item: TranscriptItem = { id: this.nextId("assistant"), kind: "message", role: "assistant", text: "", at: new Date().toISOString(), complete: false };
    this.activeMessageId = item.id;
    this.push(item);
    return item;
  }

  private item<TKind extends TranscriptItem["kind"]>(id: string | undefined, kind: TKind) {
    return this.items.find(item => item.id === id && item.kind === kind) as Extract<TranscriptItem, { kind: TKind }> | undefined;
  }

  private tool(callId: string | undefined) {
    return this.items.find(item => item.kind === "tool" && item.toolCallId === callId) as Extract<TranscriptItem, { kind: "tool" }> | undefined;
  }

  private findLast<TKind extends TranscriptItem["kind"]>(kind: TKind, predicate: (item: Extract<TranscriptItem, { kind: TKind }>) => boolean) {
    return [...this.items].reverse().find(item => item.kind === kind && predicate(item as any)) as Extract<TranscriptItem, { kind: TKind }> | undefined;
  }

  private nextId(prefix: string) {
    return `${prefix}-${Date.now()}-${++this.sequence}`;
  }

  private push(item: TranscriptItem) {
    this.items.push(item);
    if (this.items.length > 500) this.items.shift();
  }
}
