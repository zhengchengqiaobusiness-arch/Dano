import {
  SessionManager,
  type AgentSession,
  type AgentSessionEvent,
} from "@earendil-works/pi-coding-agent";
import {
  type DanoConfig,
  loadDanoConfig,
} from "./bridge/dano-config.js";
import {
  createAskUserQuestionRuntime,
  type AskUserQuestionRuntime,
} from "./bridge/ask-user-question.js";
import { createDetachedAgentSessionRuntime } from "./bridge/detached-session.js";
import {
  createFieldAssistService,
  createPiSdkFieldAssistClient,
} from "./bridge/field-assist.js";
import { interruptOpenFormInteractions } from "./bridge/form-interaction.js";
import { DetachedSessionRegistry } from "./bridge/session-registry.js";
import type {
  BridgeLiveEvent,
  BridgeSessionActions,
  BridgeSessionEvents,
  BridgeSessionState,
} from "./bridge/live-session.js";
import type { BridgeRpcAdapterContext } from "./bridge/bridge-rpc-adapter.js";
import type {
  RpcSlashCommand,
} from "../types/protocol.js";
import type { CredentialBroker } from "./bridge/credential-broker.js";
import type { ProtectedSessionTools } from "./bridge/protected-session-tools.js";

export interface DanoBackend {
  readonly context: BridgeRpcAdapterContext;
  readonly session: AgentSession;
  readonly sessionRegistry?: DetachedSessionRegistry;
  dispose(): Promise<void>;
}

export interface CreateDanoBackendOptions {
  cwd?: string;
  sessionPath?: string;
  sessionDir?: string;
  danoConfig?: DanoConfig;
  credentialBroker?: CredentialBroker;
  credentialBrokerScope?: string;
  protectedTools?: ProtectedSessionTools;
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

function toBridgeLiveEvent(event: AgentSessionEvent): BridgeLiveEvent | null {
  switch (event.type) {
    case "agent_start":
      return { type: "agent_start" };
    case "agent_end":
      return {
        type: "agent_end",
        messages: event.messages,
        willRetry: event.willRetry,
      };
    case "auto_retry_start":
      return {
        type: "auto_retry_start",
        attempt: event.attempt,
        maxAttempts: event.maxAttempts,
        delayMs: event.delayMs,
      };
    case "auto_retry_end":
      return {
        type: "auto_retry_end",
        success: event.success,
      };
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
  askUserQuestion: AskUserQuestionRuntime = createAskUserQuestionRuntime({
    maxRetries: danoConfig.askUserQuestion?.maxRetries,
    defaultTitle: danoConfig.askUserQuestion?.defaultTitle,
  }),
  disposeDanoLlmResilience: () => void = () => {},
  disposeSession: () => void | Promise<void> = () => session.dispose(),
  sessionRegistry?: DetachedSessionRegistry,
): DanoBackend {
  interruptOpenFormInteractions(session.sessionManager);
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

  const unsubscribeSession = sessionRegistry
    ? () => {}
    : session.subscribe(event => {
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

    isCompacting() {
      return session.isCompacting;
    },

    getPendingMessageCount() {
      return session.pendingMessageCount;
    },

    getAvailableModels() {
      return [...session.modelRuntime.getAvailableSnapshot()];
    },

    getCurrentModel() {
      return session.model;
    },

    getConfiguredDefaultModel() {
      const provider = session.settingsManager.getDefaultProvider();
      const modelId = session.settingsManager.getDefaultModel();
      return provider && modelId
        ? session.modelRuntime.getModel(provider, modelId)
        : undefined;
    },

    getConfiguredDefaultThinkingLevel() {
      return session.settingsManager.getDefaultThinkingLevel();
    },

    getThinkingLevel() {
      return session.thinkingLevel;
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

    abortCompaction() {
      session.abortCompaction();
    },

    async setModel(model) {
      const previousModel = session.model;
      await session.setModel(
        model as Parameters<typeof session.setModel>[0],
      );
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
      session.setThinkingLevel(level);
    },

    setSessionName(name) {
      session.setSessionName(name);
    },

    getCommands(selectedSession = session) {
      return listSessionCommands(selectedSession);
    },
  };

  const createFieldAssist = (selectedSession: AgentSession) =>
    createFieldAssistService({
      ai: createPiSdkFieldAssistClient({
        modelRuntime: selectedSession.modelRuntime,
      }),
      getCurrentModel: () => selectedSession.model,
      maxRetries: danoConfig.fieldAssist?.maxRetries,
    });

  return {
    context: {
      events,
      state,
      actions,
      askUserQuestion,
      fieldAssist: createFieldAssist(session),
      createFieldAssist,
    },
    session,
    sessionRegistry,
    async dispose() {
      unsubscribeSession();
      disposeDanoLlmResilience();
      await disposeSession();
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
  const askUserQuestion = createAskUserQuestionRuntime({
    maxRetries: danoConfig.askUserQuestion?.maxRetries,
    defaultTitle: danoConfig.askUserQuestion?.defaultTitle,
  });
  const result = await createDetachedAgentSessionRuntime(
    sessionManager.getCwd() || cwd,
    sessionManager,
    {
      askUserQuestionTool: askUserQuestion.tool,
      credentialBroker: options.credentialBroker,
      credentialBrokerScope: options.credentialBrokerScope,
      protectedTools: options.protectedTools,
    },
  );

  const sessionRegistry = new DetachedSessionRegistry(
    result.runtime.cwd,
    askUserQuestion.tool,
    {
      modelRuntime: result.runtime.session.modelRuntime,
      settingsManager: result.runtime.session.settingsManager,
      credentialBroker: options.credentialBroker,
      credentialBrokerScope: options.credentialBrokerScope,
      protectedTools: options.protectedTools,
    },
  );
  await sessionRegistry.adoptRuntime(
    result.runtime,
    result.disposeDanoLlmResilience,
  );

  const backend = createDanoBackendFromSession(
    result.runtime.session,
    danoConfig,
    askUserQuestion,
    () => {},
    () => sessionRegistry.dispose(),
    sessionRegistry,
  );
  backend.context.captureMemoryInput = options.protectedTools?.captureMemoryInput;
  return backend;
}
