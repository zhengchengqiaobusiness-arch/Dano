/**
 * Abstractions over Pi's live-session runtime.
 *
 * These interfaces allow ws-rpc-adapter.ts to remain agnostic to
 * ExtensionAPI / ExtensionCommandContext while still consuming events,
 * reading state, and issuing actions against the live Pi session.
 *
 * A single implementation (in @pi-web/bin) adapts the concrete pi + ctx
 * objects. A future backend would provide its own implementation.
 */

import type { SessionManager } from "@earendil-works/pi-coding-agent";

// ============================================================================
// 1. BridgeSessionEvents  — subscribe to live-session lifecycle events
// ============================================================================

/** Discriminated-union event type for Pi agent session events. */
export type BridgeLiveEvent =
  | { type: "agent_start" }
  | { type: "agent_end"; messages?: unknown[] }
  | {
      type: "message_start" | "message_update" | "message_end";
      [key: string]: unknown;
    }
  | { type: "session_compact" }
  | {
      type: "model_select";
      model: { id: string; provider: string };
      previousModel?: { id: string; provider: string };
      source: "set" | "cycle" | "restore";
    };

export type BridgeLiveEventHandler = (event: BridgeLiveEvent) => void;

export interface BridgeSessionEvents {
  /** Register a handler for any live-session event. Returns unsubscribe. */
  subscribe(handler: BridgeLiveEventHandler): () => void;
}

// ============================================================================
// 2. BridgeSessionState  — read-only access to the live session
// ============================================================================

export interface BridgeSessionState {
  /** The session manager for the live (TUI-attached) session. */
  readonly sessionManager: SessionManager;

  /** Current working directory. */
  cwd: string;

  /** True when the live agent is idle (not streaming). */
  isIdle(): boolean;

  /** True when the live session has queued / pending messages. */
  hasPendingMessages(): boolean;

  /** Available model registry. */
  getAvailableModels(): Array<{
    id: string;
    provider: string;
    name?: string;
    api?: string;
    reasoning?: boolean;
    contextWindow?: number;
    maxTokens?: number;
  }>;

  /** The currently-selected model (from the live session). */
  getCurrentModel: () =>
    | { id: string; provider: string; name?: string }
    | undefined;

  /** Current thinking level. */
  getThinkingLevel(): string;

  /** Context-usage stats (tokens, contextWindow, percent). */
  getContextUsage(): {
    tokens: number | null;
    contextWindow: number;
    percent: number | null;
  } | null;
}

// ============================================================================
// 3. BridgeSessionActions  — write / action operations on the live session
// ============================================================================

/** Content for a user message sent through the live session. */
export type BridgeUserMessageContent =
  | string
  | Array<
      | { type: "text"; text: string }
      | { type: "image"; data: string; mimeType: string }
    >;

export interface BridgeSessionActions {
  /** Send a user message to the live session (steer or follow-up). */
  sendUserMessage(
    content: BridgeUserMessageContent,
    options: { deliverAs: "steer" | "followUp" },
  ): void;

  /** Abort the current agent turn. */
  abort(): void;

  /** Set the active model. */
  setModel(model: { id: string; provider: string }): Promise<void>;

  /** Set the thinking level. */
  setThinkingLevel(level: string): void;

  /** Set the session display name. */
  setSessionName(name: string): void;

  /** List registered slash commands. */
  getCommands(): Array<{ name: string; description?: string }>;
}
