import type { BridgeConfig, BridgeEvent, WsClient } from "./types.js";

/**
 * Callback for bridge events.
 */
export type BridgeEventHandler = (event: BridgeEvent) => void;

/**
 * Per-client send buffer entry.
 */
interface ClientBufferEntry {
  payload: string;
  timestamp: number;
}

/**
 * Per-client state for event fan-out.
 */
interface ClientState {
  client: WsClient;
  send: (data: string) => void;
  buffer: ClientBufferEntry[];
  closed: boolean;
}

/**
 * Bridge event bus handles event subscription and fan-out to WebSocket clients.
 *
 * Features:
 * - Subscribe to bridge lifecycle events for terminal log view
 * - Fan-out Pi events to all connected WS clients
 * - Per-client send buffering with configurable backpressure (drop oldest)
 */
export class BridgeEventBus {
  private handlers: BridgeEventHandler[] = [];
  private clients = new Map<string, ClientState>();
  private config: BridgeConfig;
  private eventSeq = 0;

  constructor(config: BridgeConfig) {
    this.config = config;
  }

  /**
   * Subscribe to bridge events.
   * @param handler Callback invoked for each event
   * @returns Unsubscribe function
   */
  subscribe(handler: BridgeEventHandler): () => void {
    this.handlers.push(handler);
    return () => {
      const idx = this.handlers.indexOf(handler);
      if (idx !== -1) {
        this.handlers.splice(idx, 1);
      }
    };
  }

  /**
   * Emit a bridge event to all subscribers.
   */
  emit(event: BridgeEvent): void {
    for (const handler of this.handlers) {
      try {
        handler(event);
      } catch (err) {
        // Don't let subscriber errors break the bus
        console.error("BridgeEventBus: handler error:", err);
      }
    }
  }

  /**
   * Register a WebSocket client for event fan-out.
   * @param client Client metadata
   * @param send Function to send data to the client
   * @returns Unregister function
   */
  registerClient(client: WsClient, send: (data: string) => void): () => void {
    const state: ClientState = {
      client,
      send,
      buffer: [],
      closed: false,
    };
    this.clients.set(client.id, state);

    // Flush any buffered events (should be empty for new clients)
    this.flushClient(state);

    return () => {
      this.unregisterClient(client.id);
    };
  }

  /**
   * Unregister a client and discard its buffer.
   */
  unregisterClient(clientId: string): void {
    const state = this.clients.get(clientId);
    if (state) {
      state.closed = true;
      this.clients.delete(clientId);
    }
  }

  /**
   * Broadcast an event to all connected WS clients.
   * Events are wrapped in the ServerMessage envelope.
   */
  broadcast(event: unknown): void {
    this.eventSeq++;
    const envelope = { type: "event" as const, payload: event };
    const data = JSON.stringify(envelope);

    for (const state of this.clients.values()) {
      if (state.closed) continue;

      // Try immediate send first
      if (state.buffer.length === 0) {
        try {
          state.send(data);
          continue;
        } catch {
          // Fall through to buffering on send error
        }
      }

      // Buffer the message
      this.bufferForClient(state, data);
    }
  }

  /**
   * Get the current send queue depth for a client.
   */
  getClientQueueDepth(clientId: string): number {
    return this.clients.get(clientId)?.buffer.length ?? 0;
  }

  /**
   * Get queue depth statistics for all clients.
   */
  getQueueStats(): { clientId: string; depth: number; maxDepth: number }[] {
    const result: { clientId: string; depth: number; maxDepth: number }[] = [];
    for (const [clientId, state] of this.clients) {
      result.push({
        clientId,
        depth: state.buffer.length,
        maxDepth: this.config.clientBufferSize,
      });
    }
    return result;
  }

  /**
   * Buffer a message for a client, dropping oldest on backpressure.
   */
  private bufferForClient(state: ClientState, data: string): void {
    if (state.closed) return;

    // Drop oldest if at capacity
    while (state.buffer.length >= this.config.clientBufferSize) {
      const dropped = state.buffer.shift();
      if (dropped) {
        console.warn(
          `BridgeEventBus: dropping old message for client ${state.client.id} (buffer full)`,
        );
      }
    }

    state.buffer.push({
      payload: data,
      timestamp: Date.now(),
    });

    // Attempt flush
    this.flushClient(state);
  }

  /**
   * Flush buffered messages for a client.
   */
  private flushClient(state: ClientState): void {
    if (state.closed) return;

    while (state.buffer.length > 0) {
      const entry = state.buffer[0];
      try {
        state.send(entry.payload);
        state.buffer.shift();
      } catch {
        // Stop flushing on error; will retry on next broadcast
        break;
      }
    }
  }

  /**
   * Dispose the event bus, unregistering all clients.
   */
  dispose(): void {
    for (const clientId of this.clients.keys()) {
      this.unregisterClient(clientId);
    }
    this.clients.clear();
    this.handlers.length = 0;
  }
}
