import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";

const execute = promisify(execFile);
const unavailable = () => new Error("MEMORY_PROCESS_PRIVACY_REQUIRED");
const validId = (value: number) => Number.isSafeInteger(value) && value > 0 && value < 2 ** 32 - 1;

/** Inspect every procfs alias. The returned kernel GID may be in an outer
 * user namespace; never compare it directly with a process-local GID. */
export function procPrivacyGroup(mountInfo: string): number {
  let group: number | undefined;
  let canonical = false;
  for (const line of mountInfo.trim().split("\n")) {
    const halves = line.split(" - ");
    const mount = halves[0]?.split(" ");
    const filesystem = halves[1]?.split(" ");
    if (halves.length !== 2 || mount.length < 6 || !filesystem || filesystem.length < 3) throw unavailable();
    if (filesystem[0] !== "proc") {
      if (mount[4] === "/proc") throw unavailable();
      continue;
    }
    const options = new Map<string, string | undefined>();
    for (const option of filesystem[2].split(",")) {
      const [name, value] = option.split("=");
      if (options.has(name)) throw unavailable();
      options.set(name, value);
    }
    if (!["2", "invisible"].includes(options.get("hidepid") ?? "")) throw unavailable();
    const rawGroup = options.get("gid");
    if (!rawGroup || !/^\d+$/.test(rawGroup)) throw unavailable();
    const current = Number(rawGroup);
    if (!validId(current) || (group !== undefined && group !== current)) throw unavailable();
    group = current;
    canonical ||= mount[4] === "/proc";
  }
  if (!canonical || group === undefined) throw unavailable();
  return group;
}

/** A worker must not be able to join the exempt host group or remount procfs. */
export function assertWorkerPrivacyEvidence(status: string): void {
  const identity = (field: string) => {
    const match = new RegExp(`^${field}:\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)$`, "m").exec(status);
    const values = match?.slice(1).map(Number);
    if (!values || values.some(value => !validId(value) || value !== values[0])) throw unavailable();
    return values[0];
  };
  identity("Uid");
  const gid = identity("Gid");
  const groups = /^Groups:[\t ]*([^\n]*)$/m.exec(status);
  if (!groups) throw unavailable();
  const supplementary = groups[1].trim() ? groups[1].trim().split(/\s+/).map(Number) : [];
  if (supplementary.some(value => value !== gid)) throw unavailable();
  if (!/^NoNewPrivs:\s+1$/m.test(status)) throw unavailable();
  for (const name of ["CapPrm", "CapEff", "CapAmb"]) {
    if (!new RegExp(`^${name}:\\s+0+$`, "m").test(status)) throw unavailable();
  }
}

export async function assertWorkerProcessPrivacy(): Promise<void> {
  if (process.platform !== "linux") throw unavailable();
  try {
    const [mountInfo, status] = await Promise.all([
      readFile("/proc/self/mountinfo", "utf8"), readFile("/proc/self/status", "utf8"),
    ]);
    procPrivacyGroup(mountInfo);
    assertWorkerPrivacyEvidence(status);
    if (process.ppid <= 0) throw unavailable();
    // procfs can report its exemption GID in an outer user namespace. Prove
    // exclusion using the actual kernel access decision, not numeric equality.
    try {
      await readFile(`/proc/${process.ppid}/status`, "utf8");
    } catch (error) {
      if (["ENOENT", "EACCES", "EPERM"].includes((error as NodeJS.ErrnoException).code ?? "")) return;
      throw error;
    }
    throw unavailable();
  } catch { throw unavailable(); }
}

/** Called only by the trusted root launcher in its dedicated mount namespace. */
export async function prepareLinuxProcessPrivacy(hostUid: number, hostGroup: number): Promise<void> {
  if (process.platform !== "linux" || process.getuid?.() !== 0 || !validId(hostUid) || !validId(hostGroup)) throw unavailable();
  const inspectAsHost = async () => {
    await execute("/usr/bin/setpriv", ["--reuid", String(hostUid), "--regid", String(hostGroup),
      "--clear-groups", "--no-new-privs", "--", process.execPath, "-e",
      `require('node:fs').readFileSync('/proc/${process.pid}/status')`],
    { timeout: 10_000, maxBuffer: 4096 });
  };
  try {
    const current = await readFile("/proc/self/mountinfo", "utf8");
    let ready = false;
    try { procPrivacyGroup(current); await inspectAsHost(); ready = true; } catch { /* Configure then recheck. */ }
    if (!ready) {
      await execute("/usr/bin/mount", ["-o", `remount,hidepid=2,gid=${hostGroup}`, "/proc"],
        { timeout: 10_000, maxBuffer: 4096 });
    }
    procPrivacyGroup(await readFile("/proc/self/mountinfo", "utf8"));
    await inspectAsHost();
  } catch { throw unavailable(); }
}
