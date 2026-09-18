import { chmod, mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { execFile, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { afterEach, expect, it } from "vitest";
import { WorkerIdentityRegistry } from "../worker-identity-registry.js";

const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
async function harness(count = 20, initialize = true) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "dano-worker-identities-")));
  roots.push(root);
  const options = { directory: join(root, "identities"), firstUid: 10001, firstGid: 20001,
    hostUid: 1000, hostGid: 1000, count, lockTimeoutMs: 5000 };
  const registry = new WorkerIdentityRegistry(options);
  if (initialize) {
    const data = join(root, "initial-data");
    await mkdir(data);
    await registry.initialize([data]);
  }
  return { root, options, registry };
}

it("serializes independent callers, keeps owners distinct, and preserves identities after restart", async () => {
  const h = await harness();
  const identities = await Promise.all(Array.from({ length: 20 }, (_, i) =>
    new WorkerIdentityRegistry(h.options).get(`owner-${i % 10}`)));
  expect(new Set(identities.map(value => value.uid)).size).toBe(10);
  expect(new Set(identities.map(value => value.gid)).size).toBe(10);
  for (let i = 0; i < 10; i++) {
    expect(identities[i]).toEqual(identities[i + 10]);
    expect(await new WorkerIdentityRegistry(h.options).get(`owner-${i}`)).toEqual(identities[i]);
  }
  expect(await readFile(join(h.options.directory, "allocations.json"), "utf8")).not.toContain("owner-");
});

it("never reassigns exhausted identities and refuses changed ranges", async () => {
  const h = await harness(1);
  const alice = await h.registry.get("alice");
  await expect(h.registry.get("bob")).rejects.toThrow("RANGE_EXHAUSTED");
  expect(await new WorkerIdentityRegistry(h.options).get("alice")).toEqual(alice);
  await expect(new WorkerIdentityRegistry({ ...h.options, firstUid: 30001 }).get("alice"))
    .rejects.toThrow("IDENTITIES_UNAVAILABLE");
  expect(() => new WorkerIdentityRegistry({ ...h.options, firstGid: 1000 })).toThrow("INVALID_WORKER_IDENTITY_RANGE");
});

it("fails closed on lost, corrupt or linked records without allocating replacement identities", async () => {
  const h = await harness();
  await h.registry.get("alice");
  const file = join(h.options.directory, "allocations.json");
  const original = await readFile(file, "utf8");
  await rm(file);
  await expect(h.registry.get("bob")).rejects.toThrow("IDENTITIES_UNAVAILABLE");
  await writeFile(file, "{}", { mode: 0o600 });
  await expect(h.registry.get("bob")).rejects.toThrow("IDENTITIES_UNAVAILABLE");
  const external = join(h.root, "external");
  await writeFile(external, original, { mode: 0o600 });
  await rm(file);
  await symlink(external, file);
  await expect(h.registry.get("bob")).rejects.toThrow("IDENTITIES_UNAVAILABLE");
  expect(await readFile(external, "utf8")).toBe(original);
});

it("rejects public metadata directories and cancelled allocation without changing state", async () => {
  const h = await harness();
  await h.registry.get("alice");
  const before = await readFile(join(h.options.directory, "allocations.json"), "utf8");
  await expect(h.registry.get("bob", AbortSignal.abort())).rejects.toThrow();
  expect(await readFile(join(h.options.directory, "allocations.json"), "utf8")).toBe(before);
  await chmod(h.options.directory, 0o755);
  await expect(h.registry.get("bob")).rejects.toThrow("IDENTITIES_UNAVAILABLE");
});

it("initializes without consuming an identity and refuses a lost pool over retained user state", async () => {
  const h = await harness(20, false);
  const users = join(h.root, "users"), privateState = join(h.root, "host-state");
  await mkdir(users); await mkdir(privateState);
  await expect(h.registry.assertInitialized()).rejects.toThrow();
  await expect(h.registry.get("alice")).rejects.toThrow("IDENTITIES_UNAVAILABLE");
  await h.registry.initialize([users, privateState]);
  await h.registry.assertInitialized();
  expect((await h.registry.get("alice")).uid).toBe(h.options.firstUid);
  await writeFile(join(privateState, "retained-state"), "synthetic");
  // A normal restart preserves the established pool even with stored data.
  await new WorkerIdentityRegistry(h.options).initialize([users, privateState]);
  await rm(h.options.directory, { recursive: true });
  await expect(h.registry.assertInitialized()).rejects.toThrow();
  await expect(h.registry.initialize([users, privateState])).rejects.toThrow("IDENTITIES_UNAVAILABLE");
  await expect(readFile(join(h.options.directory, "allocations.json"))).rejects.toMatchObject({ code: "ENOENT" });
});

it("allocates consistently across real competing processes", async () => {
  const h = await harness();
  const source = new URL("../worker-identity-registry.ts", import.meta.url).href;
  const script = `const { WorkerIdentityRegistry } = await import(process.argv[1]);
    console.log(JSON.stringify(await new WorkerIdentityRegistry(JSON.parse(process.argv[2])).get(process.argv[3])));`;
  const results = await Promise.allSettled(Array.from({ length: 6 }, async (_, i) => {
    const result = await promisify(execFile)(process.execPath,
      ["--input-type=module", "-e", script, source, JSON.stringify(h.options), `owner-${i % 3}`],
      { cwd: fileURLToPath(new URL("../../../", import.meta.url)), timeout: 15000 });
    return JSON.parse(result.stdout);
  }));
  const identities = results.map(result => {
    if (result.status === "rejected") throw result.reason;
    return result.value;
  });
  expect(new Set(identities.map(value => value.uid)).size).toBe(3);
  for (let i = 0; i < 3; i++) expect(identities[i]).toEqual(identities[i + 3]);
}, 20000);

it("times out on a live holder and recovers the OS lock after that process dies", async () => {
  const h = await harness();
  const alice = await h.registry.get("alice");
  const child = spawn(process.execPath, ["-e", `const fs=require('node:fs');
    const fd=fs.openSync(process.argv[1],'r+'); require('fs-ext').flockSync(fd,'ex');
    console.log('locked'); setInterval(()=>{},1000);`, join(h.options.directory, "allocation.lock")],
    { cwd: fileURLToPath(new URL("../../../", import.meta.url)), stdio: ["ignore", "pipe", "pipe"] });
  const exited = new Promise<void>(resolve => child.once("exit", () => resolve()));
  try {
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("lock holder startup timed out")), 5000);
      child.once("error", error => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(new Error("lock holder exited early")); });
      child.stdout.once("data", data => {
        clearTimeout(timeout);
        if (data.toString().trim() === "locked") resolve(); else reject(new Error("unexpected holder output"));
      });
    });
    await expect(new WorkerIdentityRegistry({ ...h.options, lockTimeoutMs: 30 }).get("bob"))
      .rejects.toThrow("WORKER_IDENTITY_LOCK_TIMEOUT");
    child.kill("SIGKILL");
    await exited;
    expect(await h.registry.get("alice")).toEqual(alice);
    expect((await h.registry.get("bob")).uid).not.toBe(alice.uid);
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    await exited;
  }
}, 15000);
