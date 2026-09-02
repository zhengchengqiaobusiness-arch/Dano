import { execSync, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const port = String(process.env.BSS_PORT || 4310);
const protectedPids = new Set([process.pid, process.ppid].filter(Boolean));

function killPid(pid) {
  const id = Number(pid);
  if (!Number.isInteger(id) || id <= 0 || protectedPids.has(id)) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/F", "/T", "/PID", String(id)], { stdio: "ignore", windowsHide: true });
    return;
  }
  try { process.kill(id, "SIGKILL"); } catch { /* already gone */ }
}

function pidsOnPort(targetPort) {
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

for (const pid of pidsOnPort(port)) killPid(pid);

if (process.platform === "win32") {
  const rootPattern = root.replace(/'/g, "''");
  spawnSync("powershell.exe", [
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    `$root='${rootPattern}'; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $root + '*') -and $_.CommandLine -match 'src[\\\\/]web[\\\\/]server\\.ts|rpc-entry|browser-profile|bss-ui-' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }`
  ], { stdio: "ignore", windowsHide: true });
}
