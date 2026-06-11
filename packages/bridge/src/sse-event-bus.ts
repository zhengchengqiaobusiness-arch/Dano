import type { SseEvent, SseEventName } from "./types.js";

export type SseEventHandler = (event: SseEvent) => void;

export function formatSseEvent(event: SseEvent): string {
  const lines: string[] = [];

  if (typeof event.id === "number") {
    lines.push(`id: ${event.id}`);
  }

  lines.push(`event: ${event.event}`);

  const data = JSON.stringify(event.data ?? {});
  for (const line of data.split(/\r?\n/)) {
    lines.push(`data: ${line}`);
  }

  return `${lines.join("\n")}\n\n`;
}

export class SseEventBus {
  private eventSeq = 0;
  private history = new Map<string, SseEvent[]>();
  private subscribers = new Map<string, Set<SseEventHandler>>();

  emit<TData>(
    conversationId: string,
    event: SseEventName,
    data: TData,
  ): SseEvent<TData> {
    this.eventSeq += 1;
    const payload: SseEvent<TData> = {
      id: this.eventSeq,
      event,
      data,
    };

    const history = this.history.get(conversationId) ?? [];
    history.push(payload);
    this.history.set(conversationId, history);

    for (const handler of this.subscribers.get(conversationId) ?? []) {
      handler(payload);
    }

    return payload;
  }

  getHistory(conversationId: string): SseEvent[] {
    return [...(this.history.get(conversationId) ?? [])];
  }

  subscribe(conversationId: string, handler: SseEventHandler): () => void {
    const handlers = this.subscribers.get(conversationId) ?? new Set();
    handlers.add(handler);
    this.subscribers.set(conversationId, handlers);

    return () => {
      handlers.delete(handler);
      if (handlers.size === 0) {
        this.subscribers.delete(conversationId);
      }
    };
  }

  disposeConversation(conversationId: string): void {
    this.history.delete(conversationId);
    this.subscribers.delete(conversationId);
  }
}
