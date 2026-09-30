import { fileURLToPath } from "node:url";
import { createWorkerTools } from "./heimdall-worker-tools.js";
import { assertWorkerProcessPrivacy } from "./linux-process-privacy.js";
import { serveWorkerBroker } from "./worker-broker.js";

/** One trusted, per-User tool process. Only Heimdall may execute model commands;
 * its setuid Bubblewrap applies no_new_privs after namespace setup. */
export async function runWorkerBroker(): Promise<void> {
  if (process.platform !== "linux" || !process.getuid?.() || !process.send || !process.connected) {
    throw new Error("ISOLATED_TOOL_PROCESS_REQUIRED");
  }
  const profile = JSON.parse(process.argv[2] ?? "null");
  if (!profile || process.getuid() !== profile.workerUid || process.getgid?.() !== profile.workerGid) {
    throw new Error("WORKER_IDENTITY_MISMATCH");
  }
  await assertWorkerProcessPrivacy();
  const tools = await createWorkerTools({ workspace: profile.workspace });
  try {
    // Exercise the actual registered tool before announcing readiness. Reading
    // sysctls or bwrap --version cannot prove namespace creation or privilege drop.
    // -I excludes workspace modules and user Python configuration from this probe.
    const probe = await tools.execute("bash", { command: `python3 -I -c 'import ctypes,os,json
libc=ctypes.CDLL(None,use_errno=True)
header=(ctypes.c_uint32*2)(0x20080522,0)
caps=(ctypes.c_uint32*6)()
assert libc.capget(ctypes.byref(header),ctypes.byref(caps))==0
assert not any(caps)
assert libc.prctl(39,0,0,0,0)==1
assert not os.path.exists("/proc/1/status")
print(json.dumps([os.getuid(),os.getgid()]))'` },
    AbortSignal.timeout(10_000), () => {});
    const identity = (probe as { content: { type: string; text?: string }[] }).content
      .filter(part => part.type === "text").map(part => part.text).join("").trim();
    const expected = JSON.stringify([process.getuid?.(), process.getgid?.()]);
    if (JSON.stringify(JSON.parse(identity)) !== expected) throw new Error("WORKER_SANDBOX_UNAVAILABLE");
  } catch (error) { tools.close(); throw error; }
  const worker = { ...tools, workspace: profile.workspace, assertIsolated: assertWorkerProcessPrivacy };
  if (!process.connected) { worker.close(); return; }
  serveWorkerBroker(worker, {
    get connected() { return Boolean(process.connected); },
    on: process.on.bind(process), off: process.off.bind(process),
    send: (value, callback) => process.send!(value, callback),
    disconnect: () => process.disconnect?.(),
  }, profile);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  runWorkerBroker().catch(() => {
    process.exitCode = 1;
    process.disconnect?.();
  });
}
