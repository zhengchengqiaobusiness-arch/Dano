/**
 * Bridge lifecycle management.
 *
 * Handles:
 * - startBridge() with port range fallback
 * - SIGINT handler registration and cleanup
 * - Wiring Pi's ExtensionAPI context into the RPC adapter
 * - stop() that closes everything and invokes done() callback
 */

import { BridgeEventBus } from "@pi-web/bridge/bridge-event-bus";
import {
  BridgeServer,
  type WsConnectionHandlerFactory,
} from "@pi-web/bridge/server";
import { DetachedSessionRegistry } from "@pi-web/bridge/session-registry";
import type {
  BridgeConfig,
  BridgeEvent,
  BridgeState,
  WsClient,
} from "@pi-web/bridge/types";
import {
  type WsRpcAdapterContext,
  WsRpcAdapter,
} from "@pi-web/bridge/ws-rpc-adapter";

/**
 * Callback invoked when the bridge shuts down
 */
export type BridgeDoneCallback = () => void;

/**
 * Bridge controller managing the full lifecycle
 */
export interface BridgeController {
  /** Get current bridge state */
  getState(): BridgeState;
  /** Get the bridge URL for display */
  getBridgeUrl(): string | undefined;
  /** Get list of connected clients */
  getClients(): WsClient[];
  /** Stop the bridge gracefully */
  stop(): Promise<void>;
  /** Subscribe to bridge events */
  subscribe(handler: (event: BridgeEvent) => void): () => void;
}

/**
 * Start the bridge with lifecycle management
 */
export interface StartBridgeOptions {
  /**
   * Register a process-level SIGINT handler.
   * Disable this when the caller already handles Ctrl+C inside a custom UI.
   */
  captureSigint?: boolean;
  /**
   * Reuse a detached-session registry across bridge restarts in dev mode.
   */
  sessionRegistry?: DetachedSessionRegistry;
}

export async function startBridge(
  config: BridgeConfig,
  context: WsRpcAdapterContext,
  done: BridgeDoneCallback,
  options: StartBridgeOptions = {},
): Promise<BridgeController> {
  // Create event bus for internal communication
  const eventBus = new BridgeEventBus(config);

  // Event handlers for terminal log view
  const eventHandlers: Array<(event: BridgeEvent) => void> = [];

  // Reuse the detached-session registry when the caller wants sessions to
  // survive a dev-mode bridge restart.
  const sessionRegistry =
    options.sessionRegistry ?? new DetachedSessionRegistry(context.state.cwd);
  const ownsSessionRegistry = !options.sessionRegistry;

  // Emit events to all handlers
  const emitEvent = (event: BridgeEvent): void => {
    // Emit to internal handlers (terminal log view)
    for (const handler of eventHandlers) {
      try {
        handler(event);
      } catch (err) {
        console.error("Bridge lifecycle: event handler error:", err);
      }
    }
    // Also emit to event bus for any subscribers
    eventBus.emit(event);
  };

  // Connection handler factory: creates a WsRpcAdapter per WebSocket client
  const handlerFactory: WsConnectionHandlerFactory = connCtx => {
    return new WsRpcAdapter(
      connCtx.client,
      connCtx.ws,
      context,
      connCtx.config,
      connCtx.eventBus,
      connCtx.emitEvent,
      sessionRegistry,
    );
  };

  // Create server with the factory
  const server = new BridgeServer(config, handlerFactory, eventBus, emitEvent);

  // State tracking
  let state: BridgeState = { status: "starting", port: config.port };

  // Start the server
  try {
    const address = await server.start();
    state = { status: "running", host: address.host, port: address.port };
  } catch (err) {
    state = { status: "stopped" };
    throw err;
  }

  // SIGINT handler
  let sigintHandler: (() => void) | undefined;

  // Reuse the same shutdown promise so concurrent stop() callers wait for the
  // in-flight shutdown instead of racing a restart against an open port.
  let shutdownPromise: Promise<void> | undefined;

  /**
   * Graceful shutdown
   */
  const shutdown = (): Promise<void> => {
    if (shutdownPromise) {
      return shutdownPromise;
    }

    shutdownPromise = (async () => {
      state = { status: "stopping" };

      // Emit SIGINT event
      emitEvent({ type: "sigint_received" });

      // Remove SIGINT handler
      if (sigintHandler) {
        process.off("SIGINT", sigintHandler);
      }

      try {
        // Stop server
        await server.stop();

        // Dispose event bus
        eventBus.dispose();

        if (ownsSessionRegistry) {
          sessionRegistry.dispose();
        }

        state = { status: "stopped" };

        // Emit shutdown complete
        emitEvent({ type: "shutdown_complete" });
      } catch (err) {
        console.error("Bridge shutdown error:", err);
        state = { status: "stopped" };
        throw err;
      } finally {
        // Notify that we're done
        done();
      }
    })();

    return shutdownPromise;
  };

  // Register SIGINT handler
  if (options.captureSigint !== false) {
    sigintHandler = () => {
      console.log("\n[Bridge] SIGINT received, shutting down...");
      void shutdown();
    };
    process.on("SIGINT", sigintHandler);
  }

  // Return controller
  return {
    getState() {
      return state;
    },

    getBridgeUrl() {
      if (state.status === "running") {
        return `http://${state.host}:${state.port}`;
      }
      return undefined;
    },

    getClients() {
      return server.getClients();
    },

    stop() {
      return shutdown();
    },

    subscribe(handler) {
      eventHandlers.push(handler);
      return () => {
        const idx = eventHandlers.indexOf(handler);
        if (idx !== -1) {
          eventHandlers.splice(idx, 1);
        }
      };
    },
  };
}
