import type {
  BridgeEmptyStateConfig,
  BridgeQuickActionConfig,
} from "../../types/protocol.js";

export type * from "../../types/protocol.js";
export { ASK_USER_QUESTION_TOOL_NAME } from "../../types/protocol.js";

// ============================================================================
// Bridge Configuration
// ============================================================================

/** Configuration for the bridge server, sourced from extension config or defaults. */
export interface BridgeConfig {
  /** Host to bind the HTTP/SSE server to. Default: "localhost" */
  readonly host: string;
  /** Preferred port; 0 means OS-assigned. Default: 8080 */
  readonly port: number;
  /** Upper bound for port-range fallback when the preferred port is in use. Default: 0 (no fallback) */
  readonly portMax: number;
  /** Directory containing static files to serve (for the web UI bundle). Default: undefined */
  readonly staticDir?: string;
  /** Workspace path used by browser clients when opening a fresh page. Default: "/tmp/dano" */
  readonly defaultWorkspacePath?: string;
  /** Browser product name used in branded UI. Default: "Dano" */
  readonly productName: string;
  /** Empty transcript content shown before the first message. */
  readonly emptyState: BridgeEmptyStateConfig;
  /** Prompt shortcuts shown below the composer before the first message. */
  readonly quickActions: readonly BridgeQuickActionConfig[];
  /** Timeout in ms for extension UI dialog requests routed to browser clients. Default: 60_000 */
  readonly uiRequestTimeout: number;
  /** Maximum number of SSE messages to buffer per client before dropping oldest. Default: 256 */
  readonly clientBufferSize: number;
  /** Heartbeat interval for SSE streams. Default: 15_000 */
  readonly heartbeatInterval: number;
}

/** Sensible defaults for bridge configuration. */
export const DEFAULT_BRIDGE_CONFIG: BridgeConfig = {
  host: "0.0.0.0",
  port: 7036,
  portMax: 0,
  defaultWorkspacePath: "/tmp/dano",
  productName: "Dano",
  emptyState: {
    mode: "text",
    content: "给 {产品名称} 发消息",
  },
  quickActions: [],
  uiRequestTimeout: 60_000,
  clientBufferSize: 256,
  heartbeatInterval: 15_000,
};

// ============================================================================
// Bridge Runtime State
// ============================================================================

/** The lifecycle state of the bridge server. */
export type BridgeState =
  | { status: "stopped" }
  | { status: "starting"; port: number }
  | { status: "running"; host: string; port: number }
  | { status: "stopping" };

// ============================================================================
// Browser Client
// ============================================================================

/** Metadata for a connected browser client. */
export interface BridgeClient {
  /** Unique identifier assigned on connection. */
  readonly id: string;
  /** Monotonic connection sequence number (1-based). */
  readonly seq: number;
  /** ISO-8601 timestamp of when the client connected. */
  readonly connectedAt: string;
}

// ============================================================================
// Bridge Events (internal event bus)
// ============================================================================

/** Events emitted by the bridge runtime for terminal log view and internal wiring. */
export type BridgeEvent =
  | { type: "server_start"; host: string; port: number }
  | { type: "server_stop" }
  | { type: "client_connect"; client: BridgeClient }
  | { type: "client_disconnect"; client: BridgeClient; reason?: string }
  | {
      type: "command_received";
      client: BridgeClient;
      commandType: string;
      correlationId?: string;
    }
  | {
      type: "command_error";
      client: BridgeClient;
      commandType: string;
      correlationId?: string;
      error: string;
    }
  | { type: "auth_rejected"; clientIp: string; protocol: "http" | "sse" }
  | { type: "sigint_received" }
  | { type: "shutdown_complete" };
