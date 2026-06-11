/**
 * Terminal log view for the bridge.
 *
 * Renders bridge URL, client count, recent log lines (circular buffer),
 * and Ctrl+C instructions. Works within Pi's ctx.ui.custom() pattern.
 * Read-only: handleInput handles only bridge-exit shortcuts.
 */

import { getLanIps, isTailscaleIp } from "@pi-web/bridge/network";
import type {
  BridgeConfig,
  BridgeEvent,
  BridgeState,
  WsClient,
} from "@pi-web/bridge/types";

interface LogEntry {
  timestamp: Date;
  message: string;
  type: "info" | "client" | "error" | "shutdown";
}

export interface TerminalLogView {
  render(): string[];
  handleInput(input: string): void;
  shouldExit(): boolean;
  requestExit(): void;
}

function isCtrlCInput(input: string): boolean {
  return input === "\u0003";
}

export function createTerminalLogView(
  config: BridgeConfig,
  getState: () => BridgeState,
  getClients: () => WsClient[],
): TerminalLogView {
  const maxLines = 100;
  const logs: LogEntry[] = [];
  let exitRequested = false;

  const _addLog = (message: string, type: LogEntry["type"] = "info"): void => {
    logs.push({ timestamp: new Date(), message, type });
    if (logs.length > maxLines) {
      logs.shift();
    }
  };

  const formatLogEntry = (entry: LogEntry): string => {
    const time = entry.timestamp.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    const prefix =
      entry.type === "client"
        ? "[C]"
        : entry.type === "error"
          ? "[E]"
          : entry.type === "shutdown"
            ? "[X]"
            : "[I]";
    return `${time} ${prefix} ${entry.message}`;
  };

  const formatHeader = (state: BridgeState): string[] => {
    const lines: string[] = [];
    lines.push(
      "╔══════════════════════════════════════════════════════════════╗",
    );
    lines.push(
      "║           🌉 Pi Web Bridge - Terminal View                   ║",
    );
    lines.push(
      "╚══════════════════════════════════════════════════════════════╝",
    );
    lines.push("");

    const status = state.status;
    if (status === "running") {
      const url = `http://${state.host}:${state.port}`;
      const wsUrl = `ws://${state.host}:${state.port}/ws`;
      lines.push(`📡 Bridge URL: ${url}`);
      lines.push(`🔌 WebSocket:  ${wsUrl}`);
    } else if (status === "starting") {
      lines.push(`⏳ Starting on port ${state.port}...`);
    } else if (status === "stopping") {
      lines.push("🛑 Shutting down...");
    } else {
      lines.push("⚪ Bridge stopped");
    }

    const clients = getClients();
    lines.push(`👥 Clients:     ${clients.length} connected`);
    lines.push("");
    lines.push("─".repeat(62));
    lines.push("");
    return lines;
  };

  const formatClients = (clients: WsClient[]): string[] => {
    if (clients.length === 0) {
      return ["No clients connected", ""];
    }

    const lines: string[] = [];
    lines.push("Connected clients:");
    for (const client of clients) {
      const time = new Date(client.connectedAt).toLocaleTimeString("en-US", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
      });
      lines.push(
        `  #${client.seq} ${client.id.slice(0, 12)}... (connected at ${time})`,
      );
    }
    lines.push("");
    return lines;
  };

  const formatLogs = (): string[] => {
    const lines: string[] = [];
    lines.push("Recent events:");
    lines.push("─".repeat(62));

    if (logs.length === 0) {
      lines.push("  (No events yet)");
    } else {
      for (const entry of logs.slice(-20)) {
        lines.push(formatLogEntry(entry));
      }
    }

    return lines;
  };

  const formatFooter = (): string[] => {
    return [
      "",
      "─".repeat(62),
      "",
      "Press Ctrl+C to stop the bridge and return to Pi",
    ];
  };

  return {
    render(): string[] {
      const state = getState();
      const clients = getClients();
      return [
        ...formatHeader(state),
        ...formatClients(clients),
        ...formatLogs(),
        ...formatFooter(),
      ];
    },

    handleInput(input: string): void {
      if (isCtrlCInput(input)) {
        exitRequested = true;
      }
    },

    shouldExit(): boolean {
      return exitRequested;
    },

    requestExit(): void {
      exitRequested = true;
    },
  };
}

export function createEventLogger(view: {
  handleLog?: (
    message: string,
    type?: "info" | "client" | "error" | "shutdown",
  ) => void;
}): (event: BridgeEvent) => void {
  return (event: BridgeEvent) => {
    switch (event.type) {
      case "server_start": {
        view.handleLog?.(
          `Server started on ${event.host}:${event.port}`,
          "info",
        );
        break;
      }
      case "server_stop": {
        view.handleLog?.("Server stopped", "info");
        break;
      }
      case "client_connect": {
        view.handleLog?.(
          `Client #${event.client.seq} connected (${event.client.id.slice(0, 12)}...)`,
          "client",
        );
        break;
      }
      case "client_disconnect": {
        view.handleLog?.(
          `Client #${event.client.seq} disconnected: ${event.reason || "unknown"}`,
          "client",
        );
        break;
      }
      case "command_received": {
        view.handleLog?.(
          `Command [${event.commandType}] from #${event.client.seq}${event.correlationId ? ` (id: ${event.correlationId.slice(0, 8)}...)` : ""}`,
          "info",
        );
        break;
      }
      case "command_error": {
        view.handleLog?.(
          `Error [${event.commandType}] from #${event.client.seq}${event.correlationId ? ` (id: ${event.correlationId.slice(0, 8)}...)` : ""}: ${event.error}`,
          "error",
        );
        break;
      }
      case "sigint_received": {
        view.handleLog?.("SIGINT received, starting shutdown...", "shutdown");
        break;
      }
      case "shutdown_complete": {
        view.handleLog?.("Shutdown complete", "shutdown");
        break;
      }
    }
  };
}

export function createTerminalLogViewWithLogging(
  config: BridgeConfig,
  getState: () => BridgeState,
  getClients: () => WsClient[],
): TerminalLogView & {
  handleLog: (
    message: string,
    type?: "info" | "client" | "error" | "shutdown",
  ) => void;
} {
  const view = createTerminalLogView(config, getState, getClients);
  const originalRender = view.render.bind(view);

  return Object.assign(view, {
    handleLog(
      _message: string,
      _type: "info" | "client" | "error" | "shutdown" = "info",
    ) {
      // Legacy helper retained for compatibility.
    },
    render() {
      return originalRender();
    },
  });
}

export function createBridgeTerminalView(
  subscribe: (handler: (event: BridgeEvent) => void) => () => void,
  getState: () => BridgeState,
  getClients: () => WsClient[],
  _config: BridgeConfig,
  onUpdate?: (force?: boolean) => void,
): TerminalLogView & { dispose: () => void } {
  const maxLines = 100;
  const logs: Array<{
    timestamp: Date;
    message: string;
    type: "info" | "client" | "error" | "shutdown";
  }> = [];
  let exitRequested = false;

  const addLog = (
    message: string,
    type: "info" | "client" | "error" | "shutdown" = "info",
    forceUpdate = false,
  ): void => {
    logs.push({ timestamp: new Date(), message, type });
    if (logs.length > maxLines) {
      logs.shift();
    }
    onUpdate?.(forceUpdate);
  };

  const requestExit = (): void => {
    exitRequested = true;
    onUpdate?.(true);
  };

  const unsubscribe = subscribe(event => {
    switch (event.type) {
      case "server_start": {
        const lanIps = getLanIps();
        const lanInfo =
          lanIps.length > 0
            ? ` (LAN: ${lanIps
                .map(ip => {
                  const label = isTailscaleIp(ip) ? " [Tailscale]" : "";
                  return `http://${ip}:${event.port}${label}`;
                })
                .join(", ")})`
            : "";
        addLog(
          `Server started on ${event.host}:${event.port}${lanInfo}`,
          "info",
          true,
        );
        break;
      }
      case "server_stop":
        addLog("Server stopped", "info", true);
        break;
      case "client_connect":
        addLog(
          `Client #${event.client.seq} connected (${event.client.id.slice(0, 12)}...)`,
          "client",
          true,
        );
        break;
      case "client_disconnect":
        addLog(
          `Client #${event.client.seq} disconnected: ${event.reason || "unknown"}`,
          "client",
          true,
        );
        break;
      case "command_received":
        addLog(
          `Command [${event.commandType}] from #${event.client.seq}${event.correlationId ? ` (id: ${event.correlationId.slice(0, 8)}...)` : ""}`,
          "info",
        );
        break;
      case "command_error":
        addLog(
          `Error [${event.commandType}] from #${event.client.seq}${event.correlationId ? ` (id: ${event.correlationId.slice(0, 8)}...)` : ""}: ${event.error}`,
          "error",
        );
        break;
      case "sigint_received":
        addLog("SIGINT received, starting shutdown...", "shutdown", true);
        break;
      case "shutdown_complete":
        addLog("Shutdown complete", "shutdown", true);
        break;
      case "auth_rejected":
        addLog(
          `Auth rejected (${event.protocol}) from ${event.clientIp}`,
          "error",
        );
        break;
    }
  });

  const formatTime = (date: Date): string => {
    return date.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  const getStatusIndicator = (status: BridgeState["status"]): string => {
    switch (status) {
      case "running":
        return "🟢";
      case "starting":
        return "🟡";
      case "stopping":
        return "🟠";
      case "stopped":
      default:
        return "⚪";
    }
  };

  return {
    render(): string[] {
      const state = getState();
      const clients = getClients();
      const lines: string[] = [];

      lines.push(
        "╔══════════════════════════════════════════════════════════════╗",
      );
      lines.push(
        "║              🌉 Pi Web Bridge - Terminal View                ║",
      );
      lines.push(
        "╚══════════════════════════════════════════════════════════════╝",
      );
      lines.push("");

      const statusIndicator = getStatusIndicator(state.status);
      if (state.status === "running") {
        lines.push(`${statusIndicator} Bridge: http://localhost:${state.port}`);
        for (const ip of getLanIps()) {
          const tailscaleLabel = isTailscaleIp(ip) ? " (Tailscale)" : "";
          lines.push(`  📡 LAN: http://${ip}:${state.port}${tailscaleLabel}`);
        }
        lines.push(`  WebSocket: ws://localhost:${state.port}/ws`);
      } else if (state.status === "starting") {
        lines.push(`${statusIndicator} Starting on port ${state.port}...`);
      } else if (state.status === "stopping") {
        lines.push(`${statusIndicator} Shutting down...`);
      } else {
        lines.push(`${statusIndicator} Bridge stopped`);
      }
      lines.push(`  Clients: ${clients.length}`);
      lines.push("");

      if (clients.length > 0) {
        lines.push("Connected clients:");
        for (const client of clients.slice(-3)) {
          const time = formatTime(new Date(client.connectedAt));
          lines.push(`  #${client.seq} ${client.id.slice(0, 16)}... @ ${time}`);
        }
        if (clients.length > 3) {
          lines.push(`  ... and ${clients.length - 3} more`);
        }
        lines.push("");
      }

      lines.push("─".repeat(62));
      lines.push("Event log:");
      lines.push("─".repeat(62));

      if (logs.length === 0) {
        lines.push("  (No events yet - waiting for activity)");
      } else {
        for (const entry of logs.slice(-15)) {
          const prefix =
            entry.type === "client"
              ? "[C]"
              : entry.type === "error"
                ? "[E]"
                : entry.type === "shutdown"
                  ? "[X]"
                  : "[I]";
          lines.push(
            `${formatTime(entry.timestamp)} ${prefix} ${entry.message}`,
          );
        }
      }

      lines.push("");
      lines.push("─".repeat(62));
      lines.push("Press Ctrl+C to stop the bridge");

      return lines;
    },

    handleInput(input: string): void {
      if (isCtrlCInput(input)) {
        requestExit();
      }
    },

    shouldExit(): boolean {
      return exitRequested;
    },

    requestExit(): void {
      requestExit();
    },

    dispose(): void {
      unsubscribe();
    },
  };
}
