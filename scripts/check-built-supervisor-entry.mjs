import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const installation = resolve(import.meta.dirname, "../apps/dano");
const original = { realpath: fs.realpath, lstat: fs.lstat, readdir: fs.readdir };
const originalPlatform = Object.getOwnPropertyDescriptor(process, "platform");
const originalGetuid = process.getuid;
const stop = new Error("ENTRY_RESOLVED");
let resolvedEntry;
let entryName = "protected-host-entry.js";

// Exercise the shipped shared chunk, without launching a privileged process or
// touching runtime data. Only the executable lookup uses the real filesystem.
fs.realpath = async path => {
  if (path === process.execPath) throw stop;
  if (String(path).endsWith(entryName)) {
    resolvedEntry = await original.realpath(path);
    return resolvedEntry;
  }
  return resolve(path);
};
fs.lstat = async path => ({
  uid: ["/fixture/agent", "/fixture/state"].includes(path) ? 1000 : path === "/fixture/workspace" ? 20000 : 0,
  gid: path === "/fixture/workspace" ? 20000 : 0,
  mode: ["/fixture/agent", "/fixture/state", "/fixture/workspace"].includes(path) ? 0o700 : 0o755,
  isFile: () => path === resolvedEntry,
  isDirectory: () => path !== resolvedEntry });
fs.readdir = async () => [];
Object.defineProperty(process, "platform", { value: "linux" });
process.getuid = () => 0;
syncBuiltinESMExports();

try {
  const { runProtectedSupervisor } = await import(pathToFileURL(join(installation, "dist/server/bridge/protected-supervisor.js")));
  await assert.rejects(runProtectedSupervisor({
    runtimeRoot: "/fixture/runtime", sessionsRoot: "/fixture/sessions", hostStateRoot: "/fixture/state",
    identities: { directory: "/fixture/identities", firstUid: 20000, firstGid: 20000, count: 10, lockTimeoutMs: 1000 },
    maxWorkers: 1,
    broker: { installationDir: installation, hostUid: 1000, hostGid: 1000 },
    host: { hostUid: 1000, hostGid: 1000, startupTimeoutMs: 1000, operationTimeoutMs: 1000,
      maxConcurrentOperations: 1, maxMessageBytes: 1024, trustedSkillPaths: [],
      providerPythonModuleDirectory: join(installation, "dist/server/python") },
  }, {}), error => error === stop);
  assert.equal(resolvedEntry, await original.realpath(join(installation, "dist/server/bridge", entryName)));
  entryName = "worker-broker-entry.js";
  resolvedEntry = undefined;
  const { startWorkerBroker } = await import(pathToFileURL(join(installation, "dist/server/bridge/start-worker-broker.js")));
  await assert.rejects(startWorkerBroker({
    installationDir: installation, workspace: "/fixture/workspace", agentDir: "/fixture/agent", stateDir: "/fixture/state",
    hostUid: 1000, hostGid: 1000, workerUid: 20000, workerGid: 20000,
    startupTimeoutMs: 1000, operationTimeoutMs: 1000, shutdownTimeoutMs: 1000,
    maxConcurrentOperations: 1, maxResultBytes: 1024,
  }), error => error === stop);
  assert.equal(resolvedEntry, await original.realpath(join(installation, "dist/server/bridge", entryName)));
  console.log("Built supervisor and worker executable lookup passed.");
} finally {
  Object.assign(fs, original);
  Object.defineProperty(process, "platform", originalPlatform);
  process.getuid = originalGetuid;
  syncBuiltinESMExports();
}
