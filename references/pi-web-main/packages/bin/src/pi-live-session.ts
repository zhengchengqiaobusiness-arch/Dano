/**
 * Adapts Pi's ExtensionAPI + ExtensionCommandContext into the three
 * bridge live-session interfaces:
 *   - BridgeSessionEvents
 *   - BridgeSessionState
 *   - BridgeSessionActions
 */

import type {
  ExtensionAPI,
  ExtensionCommandContext,
} from "@earendil-works/pi-coding-agent";
import type { SessionManager } from "@earendil-works/pi-coding-agent";
import type {
  BridgeSessionActions,
  BridgeSessionEvents,
  BridgeSessionState,
} from "@pi-web/bridge/live-session";

// ============================================================================
// BridgeSessionEvents
// ============================================================================

export function createBridgeSessionEvents(
  pi: ExtensionAPI,
): BridgeSessionEvents {
  return {
    subscribe(handler) {
      pi.on("agent_start", () => {
        handler({ type: "agent_start" });
      });
      pi.on("agent_end", event => {
        handler({
          type: "agent_end",
          messages: (event as { messages?: unknown[] }).messages,
        });
      });
      pi.on("message_start", event => {
        handler({
          type: "message_start",
          ...(event as unknown as Record<string, unknown>),
        });
      });
      pi.on("message_update", event => {
        handler({
          type: "message_update",
          ...(event as unknown as Record<string, unknown>),
        });
      });
      pi.on("message_end", event => {
        handler({
          type: "message_end",
          ...(event as unknown as Record<string, unknown>),
        });
      });
      pi.on("session_compact", () => {
        handler({ type: "session_compact" });
      });
      pi.on("model_select", event => {
        const e = event as {
          model: { id: string; provider: string };
          previousModel?: { id: string; provider: string };
          source: "set" | "cycle" | "restore";
        };
        handler({
          type: "model_select",
          model: e.model,
          previousModel: e.previousModel,
          source: e.source,
        });
      });
      // Pi's on() doesn't return unsubscribe; events live until dispose().
      return () => {};
    },
  };
}

// ============================================================================
// BridgeSessionState
// ============================================================================

export function createBridgeSessionState(
  ctx: ExtensionCommandContext,
  pi: ExtensionAPI,
): BridgeSessionState {
  return {
    sessionManager: ctx.sessionManager as unknown as SessionManager,

    get cwd() {
      return ctx.cwd;
    },

    isIdle() {
      return ctx.isIdle();
    },

    hasPendingMessages() {
      return ctx.hasPendingMessages();
    },

    getAvailableModels() {
      return ctx.modelRegistry.getAvailable();
    },

    getCurrentModel() {
      return ctx.model;
    },

    getThinkingLevel() {
      return pi.getThinkingLevel();
    },

    getContextUsage() {
      const usage = ctx.getContextUsage();
      return usage ?? null;
    },
  };
}

// ============================================================================
// BridgeSessionActions
// ============================================================================

export function createBridgeSessionActions(
  pi: ExtensionAPI,
  ctx: ExtensionCommandContext,
): BridgeSessionActions {
  return {
    sendUserMessage(content, options) {
      pi.sendUserMessage(
        content as Parameters<ExtensionAPI["sendUserMessage"]>[0],
        {
          deliverAs: options.deliverAs,
        },
      );
    },

    abort() {
      ctx.abort();
    },

    async setModel(model) {
      await pi.setModel(model as Parameters<ExtensionAPI["setModel"]>[0]);
    },

    setThinkingLevel(level) {
      pi.setThinkingLevel(
        level as Parameters<ExtensionAPI["setThinkingLevel"]>[0],
      );
    },

    setSessionName(name) {
      pi.setSessionName(name);
    },

    getCommands() {
      const commands = pi.getCommands();
      return commands.map(c => ({
        name: c.name,
        description: c.description,
      }));
    },
  };
}
