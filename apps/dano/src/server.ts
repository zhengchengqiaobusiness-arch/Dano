import { BridgeEventBus } from "./bridge/bridge-event-bus.js";
import { BridgeRpcAdapter } from "./bridge/bridge-rpc-adapter.js";
import {
  BridgeServer,
  type AuthHttpHandler,
  type RpcConnectionHandlerFactory,
} from "./bridge/server.js";
import { DetachedSessionRegistry } from "./bridge/session-registry.js";
import type {
  ClientUserResolution,
  UserContextResolver,
} from "./bridge/user-context.js";
import { UserRuntimeRegistry, type UserRuntimeRegistryOptions } from "./bridge/user-runtime-registry.js";
import type {
  BridgeClient,
  BridgeConfig,
  BridgeEvent,
  BridgeState,
} from "./bridge/types.js";
import {
  createDanoBackend,
  type CreateDanoBackendOptions,
  type DanoBackend,
} from "./backend.js";
import type { DanoConfig } from "./bridge/dano-config.js";
import type { CredentialBroker } from "./bridge/credential-broker.js";
import type { AnonymousUserContextResolver } from "./bridge/anonymous-user-context.js";

export interface StartDanoServerOptions {
  cwd?: string;
  sessionPath?: string;
  sessionDir?: string;
  sessionsRootPath?: string;
  captureSigint?: boolean;
  backend?: DanoBackend;
  sessionRegistry?: DetachedSessionRegistry;
  onShutdown?: () => void;
  userContextResolver?: UserContextResolver;
  authHttpHandler?: AuthHttpHandler;
  danoConfig?: DanoConfig;
  credentialBroker?: CredentialBroker;
  anonymousUsers?: AnonymousUserContextResolver;
  anonymousUserCleanup?: { idleTtlMs: number; intervalMs: number };
  /** Trusted launcher composition; identities come only from the server resolver. */
  protectedToolsForUser?: UserRuntimeRegistryOptions["protectedToolsForUser"];
}

export interface DanoServerController {
  getState(): BridgeState;
  getBridgeUrl(): string | undefined;
  getClients(): BridgeClient[];
  getClientUserResolution(clientId: string): ClientUserResolution | undefined;
  requireReauthentication(loginSessionId: string): void;
  stop(): Promise<void>;
  subscribe(handler: (event: BridgeEvent) => void): () => void;
}

export async function startDanoServer(
  config: BridgeConfig,
  options: StartDanoServerOptions = {},
): Promise<DanoServerController> {
  if (options.protectedToolsForUser && !options.userContextResolver) {
    throw new Error("PROTECTED_TOOLS_USER_CONTEXT_REQUIRED");
  }
  const eventBus = new BridgeEventBus(config);
  const eventHandlers: Array<(event: BridgeEvent) => void> = [];

  const userRuntimeRegistry = options.userContextResolver
    ? new UserRuntimeRegistry(
        (runtimeOptions: CreateDanoBackendOptions) =>
          createDanoBackend({
            ...runtimeOptions,
            danoConfig: options.danoConfig,
            credentialBroker: options.credentialBroker,
          }),
        { sessionsRootPath: options.sessionsRootPath, protectedToolsForUser: options.protectedToolsForUser },
      )
    : undefined;
  const backend = userRuntimeRegistry
    ? undefined
    : options.backend ??
      (await createDanoBackend({
        cwd: options.cwd,
        sessionPath: options.sessionPath,
        sessionDir: options.sessionDir,
        credentialBroker: options.credentialBroker,
      }));
  const ownsBackend = !userRuntimeRegistry && !options.backend;

  const sessionRegistry = backend
    ? options.sessionRegistry ??
      backend.sessionRegistry ??
      new DetachedSessionRegistry(
        backend.context.state.cwd,
        backend.context.askUserQuestion.tool,
        {
          modelRuntime: backend.session.modelRuntime,
          settingsManager: backend.session.settingsManager,
          credentialBroker: options.credentialBroker,
        },
      )
    : undefined;
  const ownsSessionRegistry = Boolean(
    backend && !options.sessionRegistry && !backend.sessionRegistry,
  );

  const emitEvent = (event: BridgeEvent): void => {
    for (const handler of eventHandlers) {
      try {
        handler(event);
      } catch (error) {
        console.error(
          "Dano server lifecycle event handler error:",
          error,
        );
      }
    }
    eventBus.emit(event);
  };

  const handlerFactory: RpcConnectionHandlerFactory = async connCtx => {
    const userRuntime =
      connCtx.user && userRuntimeRegistry
        ? await userRuntimeRegistry.get(connCtx.user)
        : undefined;
    const connectionBackend = userRuntime?.backend ?? backend;
    const connectionSessionRegistry =
      userRuntime?.backend.sessionRegistry ?? sessionRegistry;
    if (!connectionBackend || !connectionSessionRegistry) {
      throw new Error("Bridge runtime is unavailable");
    }
    const connectionConfig = userRuntime
      ? {
          ...connCtx.config,
          defaultWorkspacePath: userRuntime.defaultWorkspacePath,
        }
      : connCtx.config;
    return new BridgeRpcAdapter(
      connCtx.client,
      connCtx.send,
      connectionBackend.context,
      connectionConfig,
      connCtx.eventBus,
      connCtx.emitEvent,
      connCtx.uploadRegistry,
      connectionSessionRegistry,
      userRuntime,
      options.credentialBroker,
      connCtx.loginSessionId,
      connCtx.beginUserOperation,
    );
  };

  const server = new BridgeServer(
    config,
    handlerFactory,
    eventBus,
    emitEvent,
    options.userContextResolver,
    options.authHttpHandler,
    userRuntimeRegistry,
    options.anonymousUsers,
    options.anonymousUserCleanup,
  );
  let state: BridgeState = { status: "starting", port: config.port };

  try {
    const address = await server.start();
    state = { status: "running", host: address.host, port: address.port };
  } catch (error) {
    state = { status: "stopped" };
    if (ownsSessionRegistry) {
      await sessionRegistry?.dispose();
    }
    if (ownsBackend) {
      await backend?.dispose();
    }
    await userRuntimeRegistry?.dispose();
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
          await sessionRegistry?.dispose();
        }
        if (ownsBackend) {
          await backend?.dispose();
        }
        await userRuntimeRegistry?.dispose();
        state = { status: "stopped" };
        emitEvent({ type: "shutdown_complete" });
      } catch (error) {
        console.error("Dano server shutdown error:", error);
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
      console.log("\n[dano] SIGINT received, shutting down...");
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

    getClientUserResolution(clientId) {
      return server.getClientUserResolution(clientId);
    },

    requireReauthentication(loginSessionId) {
      server.requireReauthentication(loginSessionId);
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
