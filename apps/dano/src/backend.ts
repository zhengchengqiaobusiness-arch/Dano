import {
  SessionManager,
  type AgentSession,
  type AgentSessionEvent,
} from "@earendil-works/pi-coding-agent";
import {
  DANO_DEFAULT_CONFIG,
  type DanoConfig,
  loadDanoConfig,
} from "./bridge/dano-config.js";
import {
  createAskUserQuestionRuntime,
  type AskUserQuestionRuntime,
} from "./bridge/ask-user-question.js";
import { createDetachedAgentSession } from "./bridge/detached-session.js";
import {
  createFieldAssistService,
  createPiSdkFieldAssistClient,
} from "./bridge/field-assist.js";
import {
  resolveAgentSessionDefaults,
  resolveAgentSessionModel,
  type DefaultSessionSettings,
} from "./bridge/default-model.js";
import { createHeadlessUIContext } from "./bridge/headless-ui-context.js";
import type {
  BridgeLiveEvent,
  BridgeSessionActions,
  BridgeSessionEvents,
  BridgeSessionState,
} from "./bridge/live-session.js";
import type { BridgeRpcAdapterContext } from "./bridge/bridge-rpc-adapter.js";
import type {
  RpcSlashCommand,
  RpcThinkingLevel,
} from "../types/protocol.js";

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

function listSessionCommands(session: AgentSession): RpcSlashCommand[] {
  const commands = new Map<string, RpcSlashCommand>();
  const addCommand = (
    name: string,
    description: string | undefined,
    source: RpcSlashCommand["source"],
  ) => {
    if (!name || commands.has(name)) return;
    commands.set(name, { name, description, source });
  };

  for (const command of session.extensionRunner.getRegisteredCommands()) {
    addCommand(command.invocationName, command.description, "extension");
  }

  for (const template of session.promptTemplates) {
    addCommand(template.name, template.description, "prompt");
  }

  for (const skill of session.resourceLoader.getSkills().skills) {
    addCommand(`skill:${skill.name}`, skill.description, "skill");
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
  askUserQuestion: AskUserQuestionRuntime = createAskUserQuestionRuntime(
    danoConfig.askUserQuestion?.maxRetries ??
      DANO_DEFAULT_CONFIG.askUserQuestion.maxRetries,
  ),
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
  };

  return {
    context: {
      events,
      state,
      actions,
      askUserQuestion,
      fieldAssist: createFieldAssistService({
        ai: createPiSdkFieldAssistClient({
          cwd: session.sessionManager.getCwd(),
          session,
        }),
        getCurrentModel: state.getCurrentModel,
        maxRetries: danoConfig.fieldAssist?.maxRetries,
      }),
    },
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
  const askUserQuestion = createAskUserQuestionRuntime(
    danoConfig.askUserQuestion?.maxRetries ??
      DANO_DEFAULT_CONFIG.askUserQuestion.maxRetries,
  );
  const result = await createDetachedAgentSession(
    sessionManager.getCwd() || cwd,
    sessionManager,
    {
      defaultModel: configuredDanoDefaultModel(danoConfig),
      defaultThinkingLevel: danoConfig.defaultThinkingLevel,
      askUserQuestionTool: askUserQuestion.tool,
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

  return createDanoBackendFromSession(
    result.session,
    danoConfig,
    askUserQuestion,
  );
}
