import {
  SessionManager,
  type AgentSession,
  type AgentSessionEvent,
} from "@earendil-works/pi-coding-agent";
import type { DanoConfig } from "./bridge/dano-config.js";
import { loadDanoConfig } from "./bridge/dano-config.js";
import { createDetachedAgentSession } from "./bridge/detached-session.js";
import {
  resolveAgentSessionDefaults,
  resolveAgentSessionModel,
  type DefaultSessionSettings,
} from "./bridge/default-model.js";
import { createFieldAssistHandler } from "./bridge/field-assist.js";
import { createHeadlessUIContext } from "./bridge/headless-ui-context.js";
import type {
  BridgeLiveEvent,
  BridgeSessionActions,
  BridgeSessionEvents,
  BridgeSessionState,
} from "./bridge/live-session.js";
import type { BridgeRpcAdapterContext } from "./bridge/bridge-rpc-adapter.js";
import type { RpcThinkingLevel } from "../types/protocol.js";

export interface DanoBackend {
  readonly context: BridgeRpcAdapterContext;
  readonly session: AgentSession;
  dispose(): Promise<void>;
}

export interface CreateDanoBackendOptions {
  cwd?: string;
  sessionPath?: string;
  sessionDir?: string;
  danoConfig?: DanoConfig;
}

type SessionCommandLike = {
  name?: string;
  command?: string;
  description?: string;
};

function normalizeCommandName(name: string): string {
  return name.startsWith("/") ? name : `/${name}`;
}

function listSessionCommands(session: AgentSession): Array<{
  name: string;
  description?: string;
}> {
  const commands = new Map<string, { name: string; description?: string }>();

  for (const command of session.extensionRunner.getRegisteredCommands()) {
    const name = normalizeCommandName(command.name);
    commands.set(name, {
      name,
      description: command.description,
    });
  }

  for (const template of session.promptTemplates as unknown as readonly SessionCommandLike[]) {
    const rawName = template.command ?? template.name;
    if (!rawName) {
      continue;
    }

    const name = normalizeCommandName(rawName);
    if (!commands.has(name)) {
      commands.set(name, {
        name,
        description: template.description,
      });
    }
  }

  return [...commands.values()].sort((left, right) =>
    left.name.localeCompare(right.name),
  );
}

function normalizeDefaultThinkingLevel(
  value: string | undefined,
): RpcThinkingLevel | undefined {
  switch (value) {
    case "off":
    case "minimal":
    case "low":
    case "medium":
    case "high":
    case "xhigh":
      return value;
    default:
      return undefined;
  }
}

function configuredDanoDefaultModel(
  danoConfig: DanoConfig,
): { provider: string; modelId: string } | undefined {
  return danoConfig.defaultProvider && danoConfig.defaultModel
    ? {
        provider: danoConfig.defaultProvider,
        modelId: danoConfig.defaultModel,
      }
    : undefined;
}

function getSettingsManagerDefaults(session: AgentSession): {
  provider?: string;
  modelId?: string;
  thinkingLevel?: RpcThinkingLevel;
} {
  const settingsManager = session.settingsManager as
    | {
        getDefaultProvider?: () => string | undefined;
        getDefaultModel?: () => string | undefined;
        getDefaultThinkingLevel?: () => string | undefined;
      }
    | undefined;

  return {
    provider: settingsManager?.getDefaultProvider?.(),
    modelId: settingsManager?.getDefaultModel?.(),
    thinkingLevel: normalizeDefaultThinkingLevel(
      settingsManager?.getDefaultThinkingLevel?.(),
    ),
  };
}

function createDefaultSessionSettings(
  session: AgentSession,
  danoConfig: DanoConfig,
): DefaultSessionSettings {
  const settingsDefaults = getSettingsManagerDefaults(session);
  const models = [
    configuredDanoDefaultModel(danoConfig),
    {
      provider: settingsDefaults.provider,
      modelId: settingsDefaults.modelId,
    },
  ].filter(
    (model): model is { provider: string; modelId: string } =>
      Boolean(model?.provider && model.modelId),
  );

  return {
    models,
    thinkingLevel:
      danoConfig.defaultThinkingLevel ?? settingsDefaults.thinkingLevel,
  };
}

function toBridgeLiveEvent(event: AgentSessionEvent): BridgeLiveEvent | null {
  switch (event.type) {
    case "agent_start":
      return { type: "agent_start" };
    case "agent_end":
      return { type: "agent_end", messages: event.messages };
    case "message_start":
    case "message_update":
    case "message_end":
      return event as BridgeLiveEvent;
    case "compaction_end":
      return { type: "session_compact" };
    default:
      return null;
  }
}

export function createDanoBackendFromSession(
  session: AgentSession,
  danoConfig: DanoConfig = {},
): DanoBackend {
  let pendingMessageCount = 0;
  const liveEventHandlers = new Set<(event: BridgeLiveEvent) => void>();

  const emitLiveEvent = (event: BridgeLiveEvent): void => {
    for (const handler of liveEventHandlers) {
      try {
        handler(event);
      } catch (error) {
        console.error("Dano server event handler error:", error);
      }
    }
  };

  const unsubscribeSession = session.subscribe(event => {
    if (event.type === "queue_update") {
      pendingMessageCount = event.steering.length + event.followUp.length;
      return;
    }

    const liveEvent = toBridgeLiveEvent(event);
    if (!liveEvent) {
      return;
    }

    emitLiveEvent(liveEvent);
  });

  const events: BridgeSessionEvents = {
    subscribe(handler) {
      liveEventHandlers.add(handler);
      return () => {
        liveEventHandlers.delete(handler);
      };
    },
  };

  const state: BridgeSessionState = {
    get sessionManager() {
      return session.sessionManager;
    },

    get cwd() {
      return session.sessionManager.getCwd();
    },

    isIdle() {
      return !session.isStreaming;
    },

    hasPendingMessages() {
      return pendingMessageCount > 0;
    },

    getAvailableModels() {
      return session.modelRegistry.getAvailable();
    },

    getCurrentModel() {
      return resolveAgentSessionModel(
        session,
        createDefaultSessionSettings(session, danoConfig),
      );
    },

    getDefaultModel() {
      const settingsDefaults = getSettingsManagerDefaults(session);
      return configuredDanoDefaultModel(danoConfig) ?? {
        provider: settingsDefaults.provider,
        modelId: settingsDefaults.modelId,
      };
    },

    getDefaultModels() {
      return [
        ...(createDefaultSessionSettings(session, danoConfig).models ?? []),
      ];
    },

    getDefaultThinkingLevel() {
      return createDefaultSessionSettings(session, danoConfig).thinkingLevel;
    },

    getThinkingLevel() {
      return resolveAgentSessionDefaults(
        session,
        createDefaultSessionSettings(session, danoConfig),
      ).thinkingLevel;
    },

    getContextUsage() {
      const usage = session.getContextUsage();
      return usage ?? null;
    },
  };

  const actions: BridgeSessionActions = {
    sendUserMessage(content, options) {
      void session.sendUserMessage(content, {
        deliverAs: options.deliverAs,
      });
    },

    abort() {
      void session.abort();
    },

    async setModel(model) {
      const previousModel = session.model;
      await session.setModel(model as Parameters<typeof session.setModel>[0]);
      if (!session.model) {
        return;
      }

      emitLiveEvent({
        type: "model_select",
        model: session.model,
        previousModel,
        source: "set",
      });
    },

    setThinkingLevel(level) {
      session.setThinkingLevel(
        level as Parameters<typeof session.setThinkingLevel>[0],
      );
    },

    setSessionName(name) {
      session.setSessionName(name);
    },

    getCommands() {
      return listSessionCommands(session);
    },

    runFieldAssist: createFieldAssistHandler({
      getCurrentModel: () =>
        resolveAgentSessionModel(
          session,
          createDefaultSessionSettings(session, danoConfig),
        ),
    }),
  };

  return {
    context: { events, state, actions },
    session,
    async dispose() {
      unsubscribeSession();
      session.dispose();
    },
  };
}

export async function createDanoBackend(
  options: CreateDanoBackendOptions = {},
): Promise<DanoBackend> {
  const cwd = options.cwd?.trim() || process.cwd();
  const danoConfig =
    options.danoConfig ??
    loadDanoConfig({
      cwd: process.cwd(),
    });
  const sessionManager = options.sessionPath
    ? SessionManager.open(options.sessionPath)
    : SessionManager.create(cwd, options.sessionDir);
  const result = await createDetachedAgentSession(
    sessionManager.getCwd() || cwd,
    sessionManager,
    {
      defaultModel: configuredDanoDefaultModel(danoConfig),
      defaultThinkingLevel: danoConfig.defaultThinkingLevel,
    },
  );

  await result.session.bindExtensions({
      uiContext: createHeadlessUIContext(),
      onError: error => {
        console.error(
          `Dano server extension error (${error.extensionPath}):`,
          error.error,
        );
    },
    shutdownHandler: () => {},
  });

  return createDanoBackendFromSession(result.session, danoConfig);
}
