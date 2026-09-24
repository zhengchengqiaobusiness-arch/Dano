#!/usr/bin/env node
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, readdir, readFile, realpath, rename, rm } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { FileStateStore, OwnerMemoryClient } from "@josephyoung/pi-openviking/host";
import { MemoryCredentialStore } from "../dist/server/bridge/memory-credential-store.js";
import { MemoryRecoveryJournal } from "../dist/server/bridge/memory-recovery-journal.js";

const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const validId = value => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const fail = code => { throw new Error(code); };

async function privateDirectory(path) {
  assert.ok(isAbsolute(path) && resolve(path) === path && await realpath(path) === path,
    "UNPROTECTED_RECOVERY_PATH");
  const stat = await lstat(path);
  assert.ok(stat.isDirectory() && !stat.isSymbolicLink() && stat.uid === process.getuid?.()
    && (stat.mode & 0o077) === 0, "UNPROTECTED_RECOVERY_PATH");
}

async function trustedDirectory(path) {
  assert.ok(isAbsolute(path) && resolve(path) === path && await realpath(path) === path,
    "UNPROTECTED_RECOVERY_PATH");
  const stat = await lstat(path);
  assert.ok(stat.isDirectory() && !stat.isSymbolicLink()
    && (stat.uid === 0 || stat.uid === process.getuid?.())
    && (stat.mode & 0o022) === 0, "UNPROTECTED_RECOVERY_PATH");
}

async function privateFile(path, maxBytes = 64 * 1024 * 1024) {
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await handle.stat();
    assert.ok(stat.isFile() && stat.nlink === 1 && stat.uid === process.getuid?.()
      && (stat.mode & 0o077) === 0 && stat.size <= maxBytes, "UNPROTECTED_RECOVERY_PATH");
    return await handle.readFile();
  } finally { await handle.close(); }
}

function outside(path, roots) {
  for (const root of roots) {
    assert.ok(path !== root && !path.startsWith(`${root}${sep}`) && !root.startsWith(`${path}${sep}`),
      "UNPROTECTED_RECOVERY_PATH");
  }
}

async function ownerStates(dataRoot) {
  const hostRoot = join(dataRoot, "host-state");
  await trustedDirectory(dataRoot);
  await privateDirectory(hostRoot);
  const found = [];
  const seen = new Set();
  for (const entry of await readdir(hostRoot, { withFileTypes: true })) {
    assert.ok(!entry.isSymbolicLink(), "UNPROTECTED_RECOVERY_PATH");
    if (!entry.isDirectory()) continue;
    const userRoot = join(hostRoot, entry.name);
    const stateRoot = join(userRoot, "state");
    const memoryRoot = join(stateRoot, "memory");
    const statePath = join(memoryRoot, "state.json");
    let bytes;
    try { bytes = await privateFile(statePath); }
    catch (error) {
      if (error?.code === "ENOENT") continue;
      throw error;
    }
    await Promise.all([privateDirectory(userRoot), privateDirectory(stateRoot), privateDirectory(memoryRoot)]);
    const raw = JSON.parse(bytes.toString("utf8"));
    const owner = raw.owner;
    assert.ok(validId(owner?.accountId) && validId(owner?.userId), "INVALID_OWNER_STATE");
    const key = JSON.stringify([owner.accountId, owner.userId]);
    assert.ok(!seen.has(key), "DUPLICATE_OWNER_STATE");
    seen.add(key);
    const store = new FileStateStore({ owner, directory: memoryRoot,
      policyVersion: raw.authorization?.policyVersion });
    const state = await store.read();
    assert.deepEqual(state, raw, "INVALID_OWNER_STATE");
    found.push({ owner, state, bytes, statePath: relative(dataRoot, statePath) });
  }
  return found.sort((a, b) => a.owner.userId.localeCompare(b.owner.userId));
}

async function recoveryOwners(recoveryRoot) {
  await privateDirectory(recoveryRoot);
  const owners = [];
  for (const account of await readdir(recoveryRoot, { withFileTypes: true })) {
    assert.ok(account.isDirectory() && validId(account.name), "RECOVERY_OWNER_SET_MISMATCH");
    await privateDirectory(join(recoveryRoot, account.name));
    for (const user of await readdir(join(recoveryRoot, account.name), { withFileTypes: true })) {
      assert.ok(user.isDirectory() && validId(user.name), "RECOVERY_OWNER_SET_MISMATCH");
      const owner = { accountId: account.name, userId: user.name };
      const journal = await MemoryRecoveryJournal.inspect(recoveryRoot, owner);
      // A newly opened owner with no persisted state or remote work has no
      // backup payload. A later save raises its revision and is then included.
      if (journal.state.revision > 0 || journal.events.length) owners.push(owner);
    }
  }
  return owners.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}

async function writePrivateNew(path, bytes) {
  await privateDirectory(dirname(path));
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(bytes); await handle.sync(); }
  finally { await handle.close(); }
  const directory = await open(dirname(path), constants.O_RDONLY);
  try { await directory.sync(); } finally { await directory.close(); }
}

async function replacePrivate(path, bytes) {
  const temporary = join(dirname(path), `.${randomUUID()}.recovery`);
  try {
    await writePrivateNew(temporary, bytes);
    await rename(temporary, path);
    const directory = await open(dirname(path), constants.O_RDONLY);
    try { await directory.sync(); } finally { await directory.close(); }
  } finally { await rm(temporary, { force: true }); }
}

export async function checkpoint(dataRoot, recoveryRoot, outputFile) {
  assert.ok([dataRoot, recoveryRoot, outputFile].every(path => isAbsolute(path) && resolve(path) === path),
    "INVALID_ARGUMENTS");
  outside(outputFile, [dataRoot, recoveryRoot]);
  outside(dataRoot, [recoveryRoot]);
  const [states, remoteOwners] = await Promise.all([ownerStates(dataRoot), recoveryOwners(recoveryRoot)]);
  const localOwners = states.map(item => item.owner).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  assert.deepEqual(remoteOwners, localOwners, "RECOVERY_OWNER_SET_MISMATCH");
  const owners = [];
  for (const item of states) {
    const journal = await MemoryRecoveryJournal.inspect(recoveryRoot, item.owner);
    assert.deepEqual(journal.state, item.state, "MEMORY_RECOVERY_STATE_MISMATCH");
    owners.push({ owner: item.owner, statePath: item.statePath,
      stateRevision: item.state.revision, stateSha256: digest(item.bytes),
      eventBytes: journal.eventBytes.length, eventSha256: digest(journal.eventBytes) });
  }
  const manifest = { version: 1, owners };
  await writePrivateNew(outputFile, JSON.stringify(manifest) + "\n");
  return { owners: owners.length, journalBytes: owners.reduce((sum, owner) => sum + owner.eventBytes, 0) };
}

function checkedManifest(value) {
  assert.ok(value?.version === 1 && Array.isArray(value.owners) && value.owners.length <= 256,
    "INVALID_RECOVERY_CHECKPOINT");
  const seen = new Set();
  for (const item of value.owners) {
    assert.ok(validId(item?.owner?.accountId) && validId(item.owner.userId)
      && typeof item.statePath === "string" && item.statePath.startsWith("host-state/")
      && item.statePath.split("/").every(part => part && part !== "." && part !== "..")
      && Number.isSafeInteger(item.stateRevision) && item.stateRevision >= 0
      && /^[a-f0-9]{64}$/.test(item.stateSha256)
      && Number.isSafeInteger(item.eventBytes) && item.eventBytes >= 0 && item.eventBytes <= 64 * 1024 * 1024
      && /^[a-f0-9]{64}$/.test(item.eventSha256), "INVALID_RECOVERY_CHECKPOINT");
    const key = JSON.stringify([item.owner.accountId, item.owner.userId]);
    assert.ok(!seen.has(key), "INVALID_RECOVERY_CHECKPOINT");
    seen.add(key);
  }
  return value.owners;
}

function noUnreconciledWrites(oldState, latest) {
  assert.ok(latest.revision >= oldState.revision, "RECOVERY_STATE_ROLLBACK");
  if (latest.revision === oldState.revision) {
    assert.deepEqual(latest, oldState, "RECOVERY_STATE_ROLLBACK");
  }
  const oldIds = Object.keys(oldState.operations ?? {}).sort();
  assert.deepEqual(Object.keys(latest.operations ?? {}).sort(), oldIds,
    "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  for (const id of oldIds) {
    const before = oldState.operations[id], after = latest.operations[id];
    assert.ok(before.remoteSessionId === after.remoteSessionId
      && before.scope === after.scope
      && JSON.stringify(before.memoryUris ?? []) === JSON.stringify(after.memoryUris ?? [])
      && before.taskId === after.taskId
      && (before.phase === after.phase || after.phase === "blocked")
      && (after.payload === before.payload || after.phase === "blocked" && after.payload === undefined),
    "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  }
}

function expectedEffects(events) {
  const documents = new Map(), sources = new Set();
  let cleared = false, retired = false;
  for (const event of events) {
    const mutation = event.mutation;
    if (retired) fail("RECOVERY_PENDING_RETIREMENT");
    switch (mutation.kind) {
      case "removeSource": sources.add(mutation.remoteSessionId); break;
      case "removeMemory": documents.set(mutation.uri, null); break;
      case "replaceMemory": documents.set(mutation.uri, mutation.content); break;
      case "clearMemoryScope": documents.clear(); cleared = true; break;
      case "clearOwnerData": documents.clear(); sources.clear(); cleared = true; retired = true; break;
    }
  }
  return { documents, sources, cleared };
}

async function prepare(configDirectory, dataRoot, recoveryRoot, checkpointFile) {
  assert.ok([configDirectory, dataRoot, recoveryRoot, checkpointFile]
    .every(path => isAbsolute(path) && resolve(path) === path), "INVALID_ARGUMENTS");
  outside(checkpointFile, [dataRoot, recoveryRoot]);
  outside(dataRoot, [recoveryRoot]);
  await Promise.all([trustedDirectory(configDirectory), trustedDirectory(dataRoot),
    privateDirectory(join(configDirectory, "memory")), privateDirectory(recoveryRoot),
    privateDirectory(dirname(checkpointFile))]);
  const checkpointBytes = await privateFile(checkpointFile, 1024 * 1024);
  const manifest = JSON.parse(checkpointBytes.toString("utf8"));
  const checkpointSha256 = digest(checkpointBytes);
  const entries = checkedManifest(manifest);
  const [states, remoteOwners] = await Promise.all([ownerStates(dataRoot), recoveryOwners(recoveryRoot)]);
  const expectedOwners = entries.map(item => item.owner).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  assert.deepEqual(remoteOwners, expectedOwners, "RECOVERY_OWNER_SET_MISMATCH");
  assert.deepEqual(states.map(item => item.owner).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    expectedOwners, "RECOVERY_OWNER_SET_MISMATCH");
  const config = JSON.parse((await privateFile(join(configDirectory, "memory", "memory-service.json"), 65536)).toString("utf8"));
  assert.ok(validId(config.accountId), "INVALID_CONFIG");
  const credentials = new MemoryCredentialStore({ directory: join(dataRoot, "host-state", "memory-service", "credentials"),
    encryptionKey: Buffer.from(config.encryptionKey, "hex"), keyVersion: config.encryptionKeyVersion });
  const plans = [];
  for (const entry of entries) {
    assert.equal(entry.owner.accountId, config.accountId, "OWNER_MISMATCH");
    const restored = states.find(item => item.owner.accountId === entry.owner.accountId
      && item.owner.userId === entry.owner.userId);
    assert.ok(restored && restored.statePath === entry.statePath, "INVALID_RECOVERY_CHECKPOINT");
    const journal = await MemoryRecoveryJournal.inspect(recoveryRoot, entry.owner);
    assert.ok(!Object.values(journal.state.governance?.jobs ?? {}).some(job => job.phase !== "complete")
      && !journal.state.retirement, "RECOVERY_PENDING_GOVERNANCE");
    const latestBytes = Buffer.from(JSON.stringify(journal.state));
    const snapshot = digest(restored.bytes) === entry.stateSha256;
    const alreadyOverlaid = restored.state.revision === journal.state.revision
      && JSON.stringify(restored.state) === JSON.stringify(journal.state);
    assert.ok(snapshot || alreadyOverlaid, "POST_SNAPSHOT_STATE_MISMATCH");
    const statePath = join(dataRoot, entry.statePath);
    const receiptPath = `${statePath}.replay-receipt.json`;
    const receipt = { version: 1, checkpointSha256, owner: entry.owner,
      statePath: entry.statePath, latestStateSha256: digest(latestBytes) };
    if (snapshot) {
      assert.equal(restored.state.revision, entry.stateRevision, "POST_SNAPSHOT_STATE_MISMATCH");
      noUnreconciledWrites(restored.state, journal.state);
    } else {
      let savedReceipt;
      try { savedReceipt = JSON.parse((await privateFile(receiptPath, 4096)).toString("utf8")); }
      catch (error) {
        if (error?.code !== "ENOENT") throw error;
        fail("POST_SNAPSHOT_STATE_MISMATCH");
      }
      assert.deepEqual(savedReceipt, receipt, "POST_SNAPSHOT_STATE_MISMATCH");
    }
    assert.ok(entry.eventBytes <= journal.eventBytes.length
      && digest(journal.eventBytes.subarray(0, entry.eventBytes)) === entry.eventSha256
      && (entry.eventBytes === 0 || journal.eventBytes.at(entry.eventBytes - 1) === 10),
    "RECOVERY_JOURNAL_CHECKPOINT_MISMATCH");
    const prefixCount = journal.eventBytes.subarray(0, entry.eventBytes).toString("utf8").split("\n").length - 1;
    const events = journal.events.slice(prefixCount);
    const effects = expectedEffects(events);
    const key = await credentials.read(entry.owner);
    assert.ok(key, "CREDENTIAL_MISSING");
    plans.push({ owner: entry.owner, events, effects, statePath, receiptPath, receipt,
      latestBytes, client: new OwnerMemoryClient({ owner: entry.owner, baseUrl: config.baseUrl,
        apiKey: key, scope: null, timeoutMs: config.requestTimeoutMs }) });
  }
  return plans;
}

export async function reconcile(configDirectory, dataRoot, recoveryRoot, checkpointFile, preflightOnly = false) {
  const plans = await prepare(configDirectory, dataRoot, recoveryRoot, checkpointFile);
  const summary = { owners: plans.length, events: plans.reduce((sum, plan) => sum + plan.events.length, 0) };
  if (preflightOnly) return summary;
  for (const plan of plans) await plan.client.verifyIdentity();
  for (const plan of plans) {
    for (const event of plan.events) {
      const mutation = event.mutation;
      switch (mutation.kind) {
        case "removeSource":
          await plan.client.removeSource({ owner: plan.owner, scope: null,
            remoteSessionId: mutation.remoteSessionId });
          break;
        case "removeMemory": await plan.client.removeMemory(mutation.uri); break;
        case "replaceMemory": await plan.client.replaceMemory(mutation.uri, mutation.content); break;
        case "clearMemoryScope": await plan.client.clearMemoryScope(); break;
        case "clearOwnerData": await plan.client.clearOwnerData(); break;
      }
    }
  }
  for (const plan of plans) {
    const documents = await plan.client.listMemoryDocuments();
    for (const source of plan.effects.sources) {
      assert.equal(await plan.client.sessionExists(source), false, "SOURCE_RESTORED");
    }
    for (const [uri, content] of plan.effects.documents) {
      if (content === null) assert.ok(!documents.includes(uri), "MEMORY_DELETION_UNCONFIRMED");
      else assert.equal(await plan.client.readMemory(uri), content, "RETAINED_DOCUMENT_MISMATCH");
    }
    if (plan.effects.cleared) {
      const expected = [...plan.effects.documents].filter(([, content]) => content !== null).map(([uri]) => uri).sort();
      assert.deepEqual(documents, expected, "DOCUMENT_SET_MISMATCH");
    }
  }
  for (const plan of plans) {
    // Persist the checkpoint binding before overlaying the old state. A retry
    // may skip the old snapshot hash only when this exact replay wrote it.
    await replacePrivate(plan.receiptPath, JSON.stringify(plan.receipt) + "\n");
    await replacePrivate(plan.statePath, plan.latestBytes);
  }
  return summary;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let stage = "arguments";
  try {
    const [command, ...args] = process.argv.slice(2);
    let result;
    if (command === "checkpoint" && args.length === 3) {
      stage = "checkpoint";
      result = await checkpoint(...args);
    } else if ((command === "preflight" || command === "replay") && args.length === 4) {
      stage = command;
      result = await reconcile(...args, command === "preflight");
    } else fail("INVALID_ARGUMENTS");
    process.stdout.write(JSON.stringify({ result: "passed", stage, ...result }) + "\n");
  } catch (error) {
    const firstLine = error instanceof Error ? error.message.split("\n", 1)[0] : "";
    const code = /^[A-Z][A-Z0-9_]*$/.test(firstLine) ? firstLine : "RECOVERY_FAILED";
    process.stderr.write(JSON.stringify({ result: "failed", stage, code }) + "\n");
    process.exitCode = 1;
  }
}
