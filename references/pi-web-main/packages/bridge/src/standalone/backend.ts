import {
  SessionManager,
  type AgentSession,
  type AgentSessionEvent,
} from "@earendil-works/pi-coding-agent";
import { createDetachedAgentSession } from "../detached-session.js";
import { createHeadlessUIContext } from "../headless-ui-context.js";
import type {
  BridgeLiveEvent,
  BridgeSessionActions,
  BridgeSessionEvents,
  BridgeSessionState,
} from "../live-session.js";
import type { WsRpcAdapterContext } from "../ws-rpc-adapter.js";

export interface StandaloneBridgeBackend {
  readonly context: WsRpcAdapterContext;
  readonly session: AgentSession;
  dispose(): Promise<void>;
}

export interface CreateStandaloneBridgeContextOptions {
  cwd?: string;
  sessionPath?: string;
  sessionDir?: string;
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

export function createStandaloneBridgeContextFromSession(
  session: AgentSession,
): StandaloneBridgeBackend {
  let pendingMessageCount = 0;
  const liveEventHandlers = new Set<(event: BridgeLiveEvent) => void>();

  const emitLiveEvent = (event: BridgeLiveEvent): void => {
    for (const handler of liveEventHandlers) {
      try {
        handler(event);
      } catch (error) {
        console.error("Standalone bridge event handler error:", error);
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
      return session.model;
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
    context: { events, state, actions },
    session,
    async dispose() {
      unsubscribeSession();
      session.dispose();
    },
  };
}

export async function createStandaloneBridgeContext(
  options: CreateStandaloneBridgeContextOptions = {},
): Promise<StandaloneBridgeBackend> {
  const cwd = options.cwd?.trim() || process.cwd();
  const sessionManager = options.sessionPath
    ? SessionManager.open(options.sessionPath)
    : SessionManager.create(cwd, options.sessionDir);
  const result = await createDetachedAgentSession(
    sessionManager.getCwd() || cwd,
    sessionManager,
  );

  await result.session.bindExtensions({
    uiContext: createHeadlessUIContext(),
    onError: error => {
      console.error(
        `Standalone bridge extension error (${error.extensionPath}):`,
        error.error,
      );
    },
    shutdownHandler: () => {},
  });

  return createStandaloneBridgeContextFromSession(result.session);
}
