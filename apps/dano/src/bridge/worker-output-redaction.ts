import type { IsolatedToolExecutor } from "@josephyoung/pi-openviking/worker-tools";

// Isolated Python ignores workspace modules, sitecustomize and PYTHONPATH.
// All filesystem operations run as the tool user, never the credential host.
const SCRIPT = `import os, stat, sys, tempfile
path, secret = sys.argv[1], sys.argv[2].encode()
if not secret:
    raise ValueError("empty redaction value")
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
staged = None
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ValueError("output is not a regular file")
    out, staged = tempfile.mkstemp(prefix=".dano-redact-", dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(out, "wb") as target, os.fdopen(fd, "rb") as source:
        fd = -1
        pending = b""
        while True:
            chunk = source.read(65536)
            if not chunk:
                target.write(pending.replace(secret, b"[redacted]"))
                break
            pending += chunk
            while secret in pending:
                before, pending = pending.split(secret, 1)
                target.write(before)
                target.write(b"[redacted]")
            safe = max(0, len(pending) - len(secret) + 1)
            target.write(pending[:safe])
            pending = pending[safe:]
        target.flush()
        os.fsync(target.fileno())
    os.replace(staged, path)
    staged = None
finally:
    if fd >= 0:
        os.close(fd)
    if staged is not None:
        os.unlink(staged)
`;

function quote(value: string): string {
  return "'" + value.replaceAll("'", "'\"'\"'") + "'";
}

export function createWorkerOutputRedactor(worker: IsolatedToolExecutor) {
  return async (path: string, capability: string, signal?: AbortSignal): Promise<void> => {
    signal?.throwIfAborted();
    await worker.assertIsolated();
    signal?.throwIfAborted();
    // The fixed operation goes through the existing guarded interactive Shell
    // channel; neither the returned path nor file contents enter host fs APIs.
    const result = await worker.execute("user_bash", {
      command: `python3 -I -S -c ${quote(SCRIPT)} ${quote(path)} ${quote(capability)}`,
      timeout: 30,
    }, signal);
    if (!result || typeof result !== "object" || (result as { exitCode?: unknown }).exitCode !== 0) {
      throw new Error("WORKER_OUTPUT_REDACTION_FAILED");
    }
    signal?.throwIfAborted();
    await worker.assertIsolated();
  };
}
