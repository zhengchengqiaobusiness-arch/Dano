import { spawnSync } from "node:child_process";

export type ProcessKind = "studio-server" | "workbench-page" | "pi-rpc" | "playwright-browser";

export function formatProcessLog(
  action: "OPEN" | "CLOSE" | "STOP",
  kind: ProcessKind | string,
  details: { pid?: number; page?: string; reason?: string } = {}
) {
  const parts = [
    `${action.padEnd(5)} ${kind}`,
    details.pid ? `pid=${details.pid}` : "",
    details.page ? `page=${details.page}` : "",
    details.reason ? `reason=${details.reason}` : ""
  ].filter(Boolean);
  return parts.join(" ");
}

export function pidIsAlive(pid: number) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export async function waitForPidExit(pid: number, timeoutMs = 4_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (!pidIsAlive(pid)) return true;
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  return !pidIsAlive(pid);
}

export async function killProcessTree(pid: number) {
  if (!Number.isInteger(pid) || pid <= 0) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/F", "/T", "/PID", String(pid)], { stdio: "ignore", windowsHide: true });
  } else {
    try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
  }
  await waitForPidExit(pid, 3_000);
}

type LedgerLog = (level: "PROCESS" | "WARN", message: string) => void;

export class ProcessLedger {
  private readonly entries = new Map<string, { kind: ProcessKind; pid?: number; page?: string }>();

  constructor(private readonly log: LedgerLog) {}

  opened(kind: ProcessKind, details: { pid?: number; page?: string } = {}) {
    const key = this.key(kind, details);
    this.entries.set(key, { kind, pid: details.pid, page: details.page });
    this.log("PROCESS", formatProcessLog("OPEN", kind, details));
  }

  async closed(kind: ProcessKind, details: { pid?: number; page?: string; reason: string }) {
    const key = this.key(kind, details);
    const pid = details.pid ?? this.entries.get(key)?.pid;
    if (pid && pidIsAlive(pid)) await killProcessTree(pid);
    this.entries.delete(key);
    this.log("PROCESS", formatProcessLog("CLOSE", kind, { ...details, pid }));
  }

  async stopAll(reason: string) {
    for (const entry of [...this.entries.values()]) {
      await this.closed(entry.kind, { pid: entry.pid, page: entry.page, reason });
    }
  }

  snapshot() {
    return [...this.entries.values()];
  }

  private key(kind: ProcessKind, details: { pid?: number; page?: string }) {
    return `${kind}:${details.page || ""}:${details.pid || 0}`;
  }
}

export async function killCommandLineMatches(marker: string, log?: LedgerLog) {
  if (process.platform !== "win32" || !marker) return 0;
  const escaped = marker.replace(/'/g, "''").toLowerCase().replaceAll("/", "\\");
  const result = spawnSync("powershell.exe", [
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Replace('/','\\\\').Contains('${escaped}') } | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress`
  ], { encoding: "utf8", windowsHide: true, maxBuffer: 20_000_000 });
  if (!result.stdout?.trim()) return 0;
  let parsed: Array<{ ProcessId?: number; Name?: string }> = [];
  try {
    const value = JSON.parse(result.stdout);
    parsed = Array.isArray(value) ? value : [value];
  } catch {
    return 0;
  }
  let closed = 0;
  for (const item of parsed) {
    const pid = Number(item.ProcessId);
    if (!pidIsAlive(pid) || pid === process.pid || pid === process.ppid) continue;
    log?.("PROCESS", formatProcessLog("CLOSE", "leftover", { pid, reason: item.Name || marker }));
    await killProcessTree(pid);
    closed += 1;
  }
  return closed;
}
