import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { startManagedSearch } from "../managed-search.js";

const cleanup: Array<() => Promise<unknown>> = [];
afterEach(async () => { for (const close of cleanup.splice(0).reverse()) await close(); });
async function fixture(mode = "normal") {
  const root = await mkdtemp(join(tmpdir(), "dano-search-"));
  cleanup.push(() => rm(root, { recursive: true, force: true }));
  const script = join(root, "search.mjs"), pidPath = join(root, "pid");
  await writeFile(script, `
    import { writeFileSync, existsSync } from 'node:fs';
    const [path, mode, command] = process.argv.slice(2);
    if (command === 'status') process.exit(existsSync(path) && mode !== 'unready' ? 0 : 1);
    if (mode === 'fail') process.exit(2);
    if (mode === 'stubborn') process.on('SIGTERM', () => {});
    writeFileSync(path, String(process.pid));
    setInterval(() => {}, 1000);
  `);
  const controller = new AbortController(), failure = vi.fn();
  const options = { executable: process.execPath, prefixArgs: [script, pidPath, mode],
    signal: controller.signal, onFailure: failure, startupTimeoutMs: 3000, shutdownTimeoutMs: 50 };
  const start = async () => {
    const service = await startManagedSearch(options);
    cleanup.push(service.close);
    return service;
  };
  return { start, options, controller, failure, pid: async () => Number(await readFile(pidPath, "utf8")) };
}

it("waits for readiness and reaps the owned daemon on repeated close", async () => {
  const f = await fixture();
  const service = await f.start(), pid = await f.pid();
  expect(() => process.kill(pid, 0)).not.toThrow();
  await Promise.all([service.close(), service.close()]);
  expect(() => process.kill(pid, 0)).toThrow();
  expect(f.failure).not.toHaveBeenCalled();
});

it("reports unexpected daemon exit to the host exactly once", async () => {
  const f = await fixture();
  await f.start();
  process.kill(await f.pid(), "SIGKILL");
  await vi.waitFor(() => expect(f.failure).toHaveBeenCalledTimes(1));
});

it("stops a daemon that ignores graceful termination when the host aborts", async () => {
  const f = await fixture("stubborn");
  const service = await f.start(), pid = await f.pid();
  f.controller.abort();
  await service.close();
  expect(() => process.kill(pid, 0)).toThrow();
  expect(f.failure).not.toHaveBeenCalled();
});

it("fails startup when the daemon exits before readiness", async () => {
  const f = await fixture("fail");
  await expect(f.start()).rejects.toThrow("SEARCH_STARTUP_FAILED");
  expect(f.failure).not.toHaveBeenCalled();
});

it("bounds readiness waits and cleans up an unready daemon", async () => {
  const f = await fixture("unready");
  await expect(startManagedSearch({ ...f.options, startupTimeoutMs: 400 })).rejects.toThrow();
  const pid = await f.pid();
  expect(() => process.kill(pid, 0)).toThrow();
});
