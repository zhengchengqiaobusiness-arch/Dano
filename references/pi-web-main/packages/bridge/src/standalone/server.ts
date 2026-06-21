import { BridgeEventBus } from "../bridge-event-bus.js";
import { BridgeServer, type WsConnectionHandlerFactory } from "../server.js";
import { DetachedSessionRegistry } from "../session-registry.js";
import type {
  BridgeConfig,
  BridgeEvent,
  BridgeState,
  WsClient,
} from "../types.js";
import { WsRpcAdapter } from "../ws-rpc-adapter.js";
import {
  createStandaloneBridgeContext,
  type StandaloneBridgeBackend,
} from "./backend.js";

export interface StartStandaloneBridgeOptions {
  cwd?: string;
  sessionPath?: string;
  sessionDir?: string;
  captureSigint?: boolean;
  backend?: StandaloneBridgeBackend;
  sessionRegistry?: DetachedSessionRegistry;
  onShutdown?: () => void;
}

export interface StandaloneBridgeController {
  getState(): BridgeState;
  getBridgeUrl(): string | undefined;
  getClients(): WsClient[];
  stop(): Promise<void>;
  subscribe(handler: (event: BridgeEvent) => void): () => void;
}

export async function startStandaloneBridge(
  config: BridgeConfig,
  options: StartStandaloneBridgeOptions = {},
): Promise<StandaloneBridgeController> {
  const eventBus = new BridgeEventBus(config);
  const eventHandlers: Array<(event: BridgeEvent) => void> = [];

  const backend =
    options.backend ??
    (await createStandaloneBridgeContext({
      cwd: options.cwd,
      sessionPath: options.sessionPath,
      sessionDir: options.sessionDir,
    }));
  const ownsBackend = !options.backend;

  const sessionRegistry =
    options.sessionRegistry ??
    new DetachedSessionRegistry(backend.context.state.cwd);
  const ownsSessionRegistry = !options.sessionRegistry;

  const emitEvent = (event: BridgeEvent): void => {
    for (const handler of eventHandlers) {
      try {
        handler(event);
      } catch (error) {
        console.error(
          "Standalone bridge lifecycle event handler error:",
          error,
        );
      }
    }
    eventBus.emit(event);
  };

  const handlerFactory: WsConnectionHandlerFactory = connCtx => {
    return new WsRpcAdapter(
      connCtx.client,
      connCtx.ws,
      backend.context,
      connCtx.config,
      connCtx.eventBus,
      connCtx.emitEvent,
      sessionRegistry,
    );
  };

  const server = new BridgeServer(config, handlerFactory, eventBus, emitEvent);
  let state: BridgeState = { status: "starting", port: config.port };

  try {
    const address = await server.start();
    state = { status: "running", host: address.host, port: address.port };
  } catch (error) {
    state = { status: "stopped" };
    if (ownsSessionRegistry) {
      sessionRegistry.dispose();
    }
    if (ownsBackend) {
      await backend.dispose();
    }
    eventBus.dispose();
    throw error;
  }

  let sigintHandler: (() => void) | undefined;
  let shutdownPromise: Promise<void> | undefined;

  const shutdown = (): Promise<void> => {
    if (shutdownPromise) {
      return shutdownPromise;
    }

    shutdownPromise = (async () => {
      state = { status: "stopping" };
      emitEvent({ type: "sigint_received" });

      if (sigintHandler) {
        process.off("SIGINT", sigintHandler);
      }

      try {
        await server.stop();
        eventBus.dispose();
        if (ownsSessionRegistry) {
          sessionRegistry.dispose();
        }
        if (ownsBackend) {
          await backend.dispose();
        }
        state = { status: "stopped" };
        emitEvent({ type: "shutdown_complete" });
      } catch (error) {
        console.error("Standalone bridge shutdown error:", error);
        state = { status: "stopped" };
        throw error;
      } finally {
        options.onShutdown?.();
      }
    })();

    return shutdownPromise;
  };

  if (options.captureSigint !== false) {
    sigintHandler = () => {
      console.log("\n[Bridge] SIGINT received, shutting down...");
      void shutdown();
    };
    process.on("SIGINT", sigintHandler);
  }

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
        const index = eventHandlers.indexOf(handler);
        if (index !== -1) {
          eventHandlers.splice(index, 1);
        }
      };
    },
  };
}
