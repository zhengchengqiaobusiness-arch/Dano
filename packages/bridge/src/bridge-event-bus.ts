import type {
  BridgeConfig,
  BridgeEvent,
  RpcBridgeEvent,
  ServerMessage,
  WsClient,
} from "./types.js";

/**
 * Callback for bridge events.
 */
export type BridgeEventHandler = (event: BridgeEvent) => void;

/**
 * Per-client send buffer entry.
 */
interface ClientBufferEntry {
  payload: ServerMessage;
  timestamp: number;
}

/**
 * Per-client state for event fan-out.
 */
interface ClientState {
  client: WsClient;
  send: ((message: ServerMessage) => void) | null;
  buffer: ClientBufferEntry[];
  closed: boolean;
}

/**
 * Bridge event bus handles lifecycle subscription and fan-out to HTTP/SSE clients.
 *
 * Features:
 * - Subscribe to bridge lifecycle events for terminal log view
 * - Fan-out Pi events to connected SSE clients
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

  registerClient(
    client: WsClient,
    send?: (message: ServerMessage) => void,
  ): () => void {
    const existing = this.clients.get(client.id);
    const state: ClientState = existing ?? {
      client,
      send: null,
      buffer: [],
      closed: false,
    };

    state.closed = false;
    state.send = send ?? state.send;
    this.clients.set(client.id, state);

    if (send) {
      this.flushClient(state);
    }

    return () => {
      if (send) {
        this.disconnectClient(client.id, send);
      } else {
        this.unregisterClient(client.id);
      }
    };
  }

  connectClient(
    clientId: string,
    send: (message: ServerMessage) => void,
  ): () => void {
    const state = this.clients.get(clientId);
    if (!state || state.closed) {
      throw new Error(`Unknown bridge client: ${clientId}`);
    }

    state.send = send;
    this.flushClient(state);

    return () => {
      this.disconnectClient(clientId, send);
    };
  }

  /**
   * Disconnect an SSE stream while keeping the logical client and buffer alive.
   */
  disconnectClient(
    clientId: string,
    send?: (message: ServerMessage) => void,
  ): void {
    const state = this.clients.get(clientId);
    if (!state) return;
    if (!send || state.send === send) {
      state.send = null;
    }
  }

  /**
   * Unregister a logical client and discard its buffer.
   */
  unregisterClient(clientId: string): void {
    const state = this.clients.get(clientId);
    if (state) {
      state.closed = true;
      this.clients.delete(clientId);
    }
  }

  /**
   * Broadcast an event to all registered clients.
   * Events are wrapped in the ServerMessage envelope.
   */
  broadcast(event: unknown): void {
    this.eventSeq++;
    const envelope: ServerMessage = {
      type: "event",
      payload: event as RpcBridgeEvent,
    };

    for (const state of this.clients.values()) {
      if (state.closed) continue;
      this.sendToState(state, envelope);
    }
  }

  sendToClient(clientId: string, message: ServerMessage): void {
    const state = this.clients.get(clientId);
    if (!state || state.closed) return;
    this.sendToState(state, message);
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
  private sendToState(state: ClientState, message: ServerMessage): void {
    if (state.closed) return;

    if (state.send && state.buffer.length === 0) {
      try {
        state.send(message);
        return;
      } catch {
        state.send = null;
      }
    }

    this.bufferForClient(state, message);
  }

  private bufferForClient(state: ClientState, message: ServerMessage): void {
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
      payload: message,
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
      if (!state.send) return;
      try {
        state.send(entry.payload);
        state.buffer.shift();
      } catch {
        state.send = null;
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
