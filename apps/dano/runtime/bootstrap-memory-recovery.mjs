#!/usr/bin/env node
import assert from "node:assert/strict";
import { constants } from "node:fs";
import { open, readdir } from "node:fs/promises";
import { join, sep } from "node:path";
import { FileStateStore } from "@josephyoung/pi-openviking/host";
import { MemoryRecoveryJournal } from "./dist/server/bridge/memory-recovery-journal.js";
import { privateDirectory, privateFileHandle } from "./private-recovery-path.mjs";

// Run only with Dano stopped. The recovery volume is deliberately outside
// every older protected-data and OpenViking snapshot restored during rollback.
let stage = "arguments";
try {
  const [hostStateRoot, recoveryRoot, ...extra] = process.argv.slice(2);
  assert.ok(hostStateRoot && recoveryRoot && extra.length === 0, "INVALID_ARGUMENTS");
  stage = "inspect";
  await privateDirectory(hostStateRoot);
  await privateDirectory(recoveryRoot);
  assert.ok(!hostStateRoot.startsWith(`${recoveryRoot}${sep}`)
    && !recoveryRoot.startsWith(`${hostStateRoot}${sep}`), "UNPROTECTED_RECOVERY_PATH");
  const owners = [];
  const seenOwners = new Set();
  for (const entry of await readdir(hostStateRoot, { withFileTypes: true })) {
    assert.ok(!entry.isSymbolicLink(), "UNPROTECTED_RECOVERY_PATH");
    if (!entry.isDirectory()) continue;
    const userRoot = join(hostStateRoot, entry.name);
    const stateRoot = join(userRoot, "state");
    const memoryRoot = join(stateRoot, "memory");
    const statePath = join(memoryRoot, "state.json");
    let file;
    try { file = await open(statePath, constants.O_RDONLY | constants.O_NOFOLLOW); }
    catch (error) {
      if (error.code === "ENOENT") continue;
      throw error;
    }
    try {
      await Promise.all([privateDirectory(userRoot), privateDirectory(stateRoot), privateDirectory(memoryRoot)]);
      const raw = JSON.parse((await privateFileHandle(file)).toString("utf8"));
      const owner = raw.owner;
      assert.ok([owner?.accountId, owner?.userId].every(id => typeof id === "string"
        && /^[A-Za-z0-9_-]{1,128}$/.test(id)), "INVALID_OWNER_STATE");
      const key = JSON.stringify([owner.accountId, owner.userId]);
      assert.ok(!seenOwners.has(key), "DUPLICATE_OWNER_STATE");
      seenOwners.add(key);
      const store = new FileStateStore({ owner, directory: memoryRoot, policyVersion: raw.authorization?.policyVersion });
      owners.push({ owner, state: await store.read() });
    } finally { await file.close(); }
  }
  stage = "bootstrap";
  for (const { owner, state } of owners) await MemoryRecoveryJournal.bootstrap(recoveryRoot, owner, state);
  process.stdout.write(JSON.stringify({ result: "passed", owners: owners.length }) + "\n");
} catch (error) {
  const firstLine = error instanceof Error ? error.message.split("\n", 1)[0] : "";
  const code = /^[A-Z][A-Z0-9_]*$/.test(firstLine) ? firstLine : "BOOTSTRAP_FAILED";
  process.stderr.write(JSON.stringify({ result: "failed", stage, code }) + "\n");
  process.exitCode = 1;
}
