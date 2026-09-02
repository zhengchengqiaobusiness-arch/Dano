import { execSync, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const port = String(process.env.BSS_PORT || 4310);
const protectedPids = new Set([process.pid, process.ppid].filter(Boolean));
const userBrowsers = /^(chrome|msedge|msedgewebview2|firefox|iexplore|brave|opera)\.exe$/i;
const ourProfileMarker = path.join(root, ".business-skill-studio", "browser-profile").toLowerCase();

function killPid(pid, tree = false) {
  const id = Number(pid);
  if (!Number.isInteger(id) || id <= 0 || protectedPids.has(id)) return;
  if (process.platform === "win32") {
    const args = tree ? ["/F", "/T", "/PID", String(id)] : ["/F", "/PID", String(id)];
    spawnSync("taskkill", args, { stdio: "ignore", windowsHide: true });
    return;
  }
  try { process.kill(id, "SIGKILL"); } catch { /* already gone */ }
}

function pidsListeningOnPort(targetPort) {
  if (process.platform !== "win32") return [];
  let output = "";
  try {
    output = execSync("netstat -ano", { encoding: "utf8" });
  } catch {
    return [];
  }
  const pids = new Set();
  for (const line of output.split(/\r?\n/)) {
    if (!/LISTENING/i.test(line) || !new RegExp(`[:\\[]${targetPort}[\\s\\]]`).test(line)) continue;
    const match = line.trim().match(/(\d+)\s*$/);
    if (match) pids.add(Number(match[1]));
  }
  return [...pids];
}

function studioProcesses() {
  if (process.platform !== "win32") return [];
  const escapedRoot = root.replace(/'/g, "''");
  const result = spawnSync("powershell.exe", [
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + '${escapedRoot}' + '*') } | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress`
  ], { encoding: "utf8", windowsHide: true, maxBuffer: 20_000_000 });
  if (!result.stdout?.trim()) return [];
  try {
    const parsed = JSON.parse(result.stdout);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    return [];
  }
}

function isStudioServer(commandLine = "") {
  return /src[\\/]+web[\\/]+server\.ts|rpc-entry/i.test(commandLine);
}

function isOurRecorderBrowser(name = "", commandLine = "") {
  const cmd = commandLine.toLowerCase().replaceAll("/", "\\");
  const marker = ourProfileMarker.replaceAll("/", "\\");
  return userBrowsers.test(name) && cmd.includes(marker);
}

for (const pid of pidsListeningOnPort(port)) killPid(pid, true);

for (const proc of studioProcesses()) {
  const pid = Number(proc.ProcessId);
  const name = String(proc.Name || "");
  const commandLine = String(proc.CommandLine || "");
  if (userBrowsers.test(name) && !isOurRecorderBrowser(name, commandLine)) continue;
  if (isStudioServer(commandLine)) killPid(pid, true);
  else if (isOurRecorderBrowser(name, commandLine)) killPid(pid, false);
}
