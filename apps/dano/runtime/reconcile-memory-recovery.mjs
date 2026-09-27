#!/usr/bin/env node
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, readdir, readFile, rename, rm } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { FileStateStore, OwnerMemoryClient } from "@josephyoung/pi-openviking/host";
import { MemoryCredentialStore } from "../dist/server/bridge/memory-credential-store.js";
import { MemoryRecoveryJournal } from "../dist/server/bridge/memory-recovery-journal.js";
import { outside, privateDirectory, privateFile, trustedDirectory } from "./private-recovery-path.mjs";

const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const validId = value => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const fail = code => { throw new Error(code); };
const maxCheckpointBytes = 1024 * 1024;

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

/** Aggregate only: never expose owner IDs, document URIs or retained bodies. */
export async function auditRetention(recoveryRoot) {
  assert.ok(isAbsolute(recoveryRoot) && resolve(recoveryRoot) === recoveryRoot, "INVALID_ARGUMENTS");
  const owners = await recoveryOwners(recoveryRoot);
  let legacyInlineBodies = 0, activePayloads = 0, prunedPayloadReferences = 0;
  for (const owner of owners) {
    const { events } = await MemoryRecoveryJournal.inspect(recoveryRoot, owner);
    for (const { mutation } of events) {
      if (mutation.kind !== "replaceMemory") continue;
      if (!("payloadId" in mutation)) legacyInlineBodies++;
      else if (mutation.content !== undefined) activePayloads++;
      else prunedPayloadReferences++;
    }
  }
  return { owners: owners.length, legacyInlineBodies, activePayloads, prunedPayloadReferences,
    needsMigration: legacyInlineBodies > 0 };
}

/** Build a fresh recovery generation while Dano is stopped. The old volume and
 * its checkpoints are left untouched so an operator can retire that backup
 * generation only after its retention window. Never use an old checkpoint
 * against the rewritten journal: its byte-prefix binding must fail closed. */
export async function migrateLegacyRecovery(configDirectory, dataRoot, sourceRoot, targetRoot, outputCheckpoint) {
  assert.ok([configDirectory, dataRoot, sourceRoot, targetRoot, outputCheckpoint]
    .every(path => isAbsolute(path) && resolve(path) === path), "INVALID_ARGUMENTS");
  outside(targetRoot, [configDirectory, dataRoot, sourceRoot]);
  outside(outputCheckpoint, [configDirectory, dataRoot, sourceRoot, targetRoot]);
  outside(configDirectory, [dataRoot, sourceRoot]);
  outside(dataRoot, [sourceRoot]);
  await Promise.all([trustedDirectory(configDirectory), trustedDirectory(dataRoot),
    privateDirectory(join(configDirectory, "memory")), privateDirectory(sourceRoot),
    privateDirectory(targetRoot), privateDirectory(dirname(outputCheckpoint))]);
  assert.equal((await readdir(targetRoot)).length, 0, "RECOVERY_MIGRATION_TARGET_NOT_EMPTY");
  try { await privateFile(outputCheckpoint, maxCheckpointBytes); fail("RECOVERY_CHECKPOINT_EXISTS"); }
  catch (error) { if (error?.code !== "ENOENT") throw error; }
  const [states, owners] = await Promise.all([ownerStates(dataRoot), recoveryOwners(sourceRoot)]);
  assert.deepEqual(owners, states.map(item => item.owner)
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))), "RECOVERY_OWNER_SET_MISMATCH");
  const config = JSON.parse((await privateFile(join(configDirectory, "memory", "memory-service.json"), 65536)).toString("utf8"));
  assert.ok(validId(config.accountId), "INVALID_CONFIG");
  const credentials = new MemoryCredentialStore({ directory: join(dataRoot, "host-state", "memory-service", "credentials"),
    encryptionKey: Buffer.from(config.encryptionKey, "hex"), keyVersion: config.encryptionKeyVersion });

  // Preflight every owner and every live payload before writing the new root.
  const plans = [];
  let legacyInlineBodies = 0, retiredBodies = 0;
  for (const item of states) {
    const journal = await MemoryRecoveryJournal.inspect(sourceRoot, item.owner);
    assert.deepEqual(journal.state, item.state, "MEMORY_RECOVERY_STATE_MISMATCH");
    assert.ok(item.owner.accountId === config.accountId, "OWNER_MISMATCH");
    assert.ok(!Object.values(journal.state.operations ?? {}).some(operation =>
      !["ready", "failed"].includes(operation.phase))
      && !Object.values(journal.state.collectionRequests ?? {}).some(request =>
        !["processed", "discarded"].includes(request.phase))
      && !Object.values(journal.state.governance?.jobs ?? {}).some(job => job.phase !== "complete")
      && !journal.state.retirement, "RECOVERY_MIGRATION_NOT_QUIESCENT");
    const key = await credentials.read(item.owner);
    assert.ok(key, "CREDENTIAL_MISSING");
    const live = new Set(), superseded = new Set();
    let cleared = false;
    for (let index = journal.events.length - 1; index >= 0; index--) {
      const mutation = journal.events[index].mutation;
      if (mutation.kind === "clearMemoryScope" || mutation.kind === "clearOwnerData") cleared = true;
      else if (mutation.kind === "removeMemory") superseded.add(mutation.uri);
      else if (mutation.kind === "replaceMemory") {
        if (!cleared && !superseded.has(mutation.uri)) live.add(index);
        superseded.add(mutation.uri);
      }
    }
    const events = [];
    const payloads = [];
    for (const [index, event] of journal.events.entries()) {
      const mutation = event.mutation;
      if (mutation.kind !== "replaceMemory") { events.push(event); continue; }
      const inline = !("payloadId" in mutation);
      if (inline) legacyInlineBodies++;
      const content = mutation.content;
      if (live.has(index)) {
        assert.ok(typeof content === "string" && content.trim(), "RECOVERY_LIVE_PAYLOAD_MISSING");
      }
      // A retired event has no payload to verify. Replace even its old digest
      // so low-entropy deleted text cannot be guessed from the new journal.
      const contentSha256 = live.has(index) ? digest(Buffer.from(content, "utf8"))
        : digest(Buffer.from(event.id, "utf8"));
      events.push({ ...event, mutation: { kind: "replaceMemory", uri: mutation.uri,
        payloadId: event.id, contentSha256 } });
      if (live.has(index)) {
        payloads.push({ id: event.id, content });
      } else if (inline) retiredBodies++;
    }
    plans.push({ owner: item.owner, state: journal.state, events, payloads,
      effects: expectedEffects(journal.events), client: new OwnerMemoryClient({ owner: item.owner,
        baseUrl: config.baseUrl, apiKey: key, scope: null, timeoutMs: config.requestTimeoutMs }) });
  }
  assert.ok(legacyInlineBodies > 0, "RECOVERY_MIGRATION_NOT_NEEDED");
  // The new checkpoint absorbs the entire old prefix. Verify remote effects
  // first or a pre-crash deletion intent could be lost across the re-anchor.
  for (const plan of plans) await plan.client.verifyIdentity();
  for (const plan of plans) await verifyEffects(plan.client, plan.effects);

  for (const plan of plans) {
    const account = join(targetRoot, plan.owner.accountId);
    const directory = join(account, plan.owner.userId);
    for (const path of [account, directory]) {
      await mkdir(path, { mode: 0o700 }).catch(error => {
        if (error?.code !== "EEXIST") throw error;
      });
      await privateDirectory(path);
    }
    if (plan.payloads.length) {
      const payloadDirectory = join(directory, "payloads");
      await mkdir(payloadDirectory, { mode: 0o700 });
      await privateDirectory(payloadDirectory);
      for (const payload of plan.payloads) {
        await writePrivateNew(join(payloadDirectory, `${payload.id}.txt`), payload.content);
      }
    }
    await writePrivateNew(join(directory, "state.json"), JSON.stringify(plan.state));
    await writePrivateNew(join(directory, "events.jsonl"),
      plan.events.map(event => JSON.stringify(event) + "\n").join(""));
    await MemoryRecoveryJournal.inspect(targetRoot, plan.owner);
  }
  const retention = await auditRetention(targetRoot);
  assert.equal(retention.legacyInlineBodies, 0, "RECOVERY_MIGRATION_INCOMPLETE");
  const next = await checkpoint(dataRoot, targetRoot, outputCheckpoint);
  return { ...next, legacyInlineBodies, retiredBodies, activePayloads: retention.activePayloads };
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
  const bytes = JSON.stringify(manifest) + "\n";
  assert.ok(Buffer.byteLength(bytes) <= maxCheckpointBytes, "RECOVERY_CHECKPOINT_TOO_LARGE");
  await writePrivateNew(outputFile, bytes);
  return { owners: owners.length, journalBytes: owners.reduce((sum, owner) => sum + owner.eventBytes, 0) };
}

function checkedManifest(value) {
  assert.ok(value?.version === 1 && Array.isArray(value.owners),
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
  const newIds = Object.keys(latest.operations ?? {}).filter(id => !oldState.operations?.[id]);
  const remoteWriters = [];
  // Delivery persists session_unknown before its first remote request. A new
  // queued operation still has its payload and has made no remote write, so
  // overlaying the recovery state can resume it safely after rollback. A new
  // later phase needs a matching owner-bound remote receipt before any replay.
  for (const id of newIds) {
    const operation = latest.operations[id];
    assert.ok(operation.id === id && ["queued", "session_unknown", "session_created",
      "message_unknown", "message_delivered", "commit_unknown", "processing", "ready"].includes(operation.phase)
      && (operation.phase === "queued" || operation.scope === null)
      && typeof operation.remoteSessionId === "string"
      && operation.remoteSessionId.length > 0
      && (operation.phase === "ready" ? operation.payload === undefined
        && typeof operation.taskId === "string" && typeof operation.archiveId === "string"
        && Array.isArray(operation.memoryUris) && operation.memoryUris.length > 0
        : typeof operation.payload === "string" && operation.payload.trim())
      && (operation.phase !== "processing" || typeof operation.taskId === "string"),
      "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    if (operation.phase !== "queued") remoteWriters.push(operation);
  }
  assert.ok(oldIds.every(id => latest.operations?.[id]), "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  const stableOperation = operation => Object.fromEntries(Object.entries(operation)
    .filter(([field]) => !["phase", "payload", "updatedAt", "errorCode",
      "reconciliationPhase", "deliveryAttempts", "nextAttemptAt"].includes(field)));
  for (const id of oldIds) {
    const before = oldState.operations[id], after = latest.operations[id];
    assert.deepEqual(stableOperation(after), stableOperation(before),
      "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    assert.ok((before.phase === after.phase || after.phase === "blocked")
      && (after.payload === before.payload || after.phase === "blocked" && after.payload === undefined),
    "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  }
  return remoteWriters;
}

/** Read-only proofs must cover every post-checkpoint writer before deletion
 * replay. Unknown or live remote work stays fail-closed; neither append nor
 * commit is retried by this recovery command. */
async function verifyNewWriter(client, operation) {
  const failCode = "RECOVERY_WRITER_RECONCILIATION_REQUIRED";
  switch (operation.phase) {
    case "session_unknown":
      assert.equal(await client.sessionExists(operation.remoteSessionId), false, failCode);
      return;
    case "session_created":
      assert.equal(await client.sessionExists(operation.remoteSessionId), true, failCode);
      assert.equal(await client.hasSource(operation), false, failCode);
      return;
    case "message_unknown":
    case "message_delivered":
      assert.equal(await client.hasSource(operation), true, failCode);
      return;
    case "commit_unknown": {
      assert.equal(await client.hasSource(operation), true, failCode);
      const receipt = await client.findCommit(operation.remoteSessionId);
      assert.ok(receipt && (!operation.taskId || receipt.taskId === operation.taskId), failCode);
      assert.equal(await client.writerSettled(operation), true, failCode);
      return;
    }
    case "processing":
    case "ready": {
      assert.equal(await client.hasSource(operation), true, failCode);
      assert.equal(await client.writerSettled(operation), true, failCode);
      if (operation.phase === "ready") {
        const result = await client.inspect(operation);
        assert.ok(result.status === "ready" && result.archiveId === operation.archiveId
          && new Set(operation.memoryUris).size === operation.memoryUris.length
          && new Set(result.memoryUris).size === result.memoryUris.length, failCode);
        assert.deepEqual([...result.memoryUris].sort(), [...operation.memoryUris].sort(), failCode);
      }
      return;
    }
    default: fail(failCode);
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
      case "replaceMemory":
        // A later replacement, removal or clear makes an earlier payload
        // unnecessary. Its private file may already have been pruned.
        if (mutation.content !== undefined) documents.set(mutation.uri, mutation.content);
        break;
      case "clearMemoryScope": documents.clear(); cleared = true; break;
      case "clearOwnerData": documents.clear(); sources.clear(); cleared = true; retired = true; break;
    }
  }
  return { documents, sources, cleared };
}

async function verifyEffects(client, effects) {
  const documents = await client.listMemoryDocuments();
  for (const source of effects.sources) {
    assert.equal(await client.sessionExists(source), false, "SOURCE_RESTORED");
  }
  for (const [uri, content] of effects.documents) {
    if (content === null) assert.ok(!documents.includes(uri), "MEMORY_DELETION_UNCONFIRMED");
    else assert.equal(await client.readMemory(uri), content, "RETAINED_DOCUMENT_MISMATCH");
  }
  if (effects.cleared) {
    const expected = [...effects.documents].filter(([, content]) => content !== null).map(([uri]) => uri).sort();
    assert.deepEqual(documents, expected, "DOCUMENT_SET_MISMATCH");
  }
}

function replayableCorrections(events) {
  const keep = new Set(), superseded = new Set();
  let cleared = false;
  for (let index = events.length - 1; index >= 0; index--) {
    const mutation = events[index].mutation;
    if (mutation.kind === "clearMemoryScope" || mutation.kind === "clearOwnerData") {
      cleared = true;
    } else if (mutation.kind === "removeMemory") {
      superseded.add(mutation.uri);
    } else if (mutation.kind === "replaceMemory") {
      if (!cleared && !superseded.has(mutation.uri) && mutation.content !== undefined) keep.add(index);
      superseded.add(mutation.uri);
    }
  }
  return keep;
}

async function prepare(configDirectory, dataRoot, recoveryRoot, checkpointFile) {
  assert.ok([configDirectory, dataRoot, recoveryRoot, checkpointFile]
    .every(path => isAbsolute(path) && resolve(path) === path), "INVALID_ARGUMENTS");
  outside(checkpointFile, [dataRoot, recoveryRoot]);
  outside(dataRoot, [recoveryRoot]);
  await Promise.all([trustedDirectory(configDirectory), trustedDirectory(dataRoot),
    privateDirectory(join(configDirectory, "memory")), privateDirectory(recoveryRoot),
    privateDirectory(dirname(checkpointFile))]);
  const checkpointBytes = await privateFile(checkpointFile, maxCheckpointBytes);
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
    let remoteWriters = [];
    if (snapshot) {
      assert.equal(restored.state.revision, entry.stateRevision, "POST_SNAPSHOT_STATE_MISMATCH");
      remoteWriters = noUnreconciledWrites(restored.state, journal.state);
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
    // A scope/owner clear removes the entire remote tree. A newer writer may
    // already have produced documents there, so verifying its receipt alone
    // cannot make replay safe: the clear would delete those documents again.
    assert.ok(!remoteWriters.length || !events.some(event =>
      event.mutation.kind === "clearMemoryScope" || event.mutation.kind === "clearOwnerData"),
    "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    const effects = expectedEffects(events);
    const key = await credentials.read(entry.owner);
    assert.ok(key, "CREDENTIAL_MISSING");
    plans.push({ owner: entry.owner, events, effects, remoteWriters, statePath, receiptPath, receipt,
      latestBytes, client: new OwnerMemoryClient({ owner: entry.owner, baseUrl: config.baseUrl,
        apiKey: key, scope: null, timeoutMs: config.requestTimeoutMs }) });
  }
  return plans;
}

export async function reconcile(configDirectory, dataRoot, recoveryRoot, checkpointFile, preflightOnly = false) {
  const plans = await prepare(configDirectory, dataRoot, recoveryRoot, checkpointFile);
  const summary = { owners: plans.length, events: plans.reduce((sum, plan) => sum + plan.events.length, 0) };
  const remoteWriterChecks = plans.reduce((sum, plan) => sum + plan.remoteWriters.length, 0);
  if (remoteWriterChecks) summary.remoteWriterChecks = remoteWriterChecks;
  if (preflightOnly) return summary;
  for (const plan of plans) await plan.client.verifyIdentity();
  // No replay mutation may run until every new writer is checked against the
  // restored service. Every check is read-only; an incomplete or mismatched
  // remote receipt cannot be followed by a deletion replay or state overlay.
  for (const plan of plans) for (const operation of plan.remoteWriters) {
    await verifyNewWriter(plan.client, operation);
  }
  for (const plan of plans) {
    const corrections = replayableCorrections(plan.events);
    for (const [index, event] of plan.events.entries()) {
      const mutation = event.mutation;
      switch (mutation.kind) {
        case "removeSource":
          await plan.client.removeSource({ owner: plan.owner, scope: null,
            remoteSessionId: mutation.remoteSessionId });
          break;
        case "removeMemory": await plan.client.removeMemory(mutation.uri); break;
        case "replaceMemory":
          if (corrections.has(index)) await plan.client.replaceMemory(mutation.uri, mutation.content);
          break;
        case "clearMemoryScope": await plan.client.clearMemoryScope(); break;
        case "clearOwnerData": await plan.client.clearOwnerData(); break;
      }
    }
  }
  for (const plan of plans) {
    await verifyEffects(plan.client, plan.effects);
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
    if (command === "audit-retention" && args.length === 1) {
      stage = "audit-retention";
      result = await auditRetention(...args);
    } else if (command === "migrate-legacy" && args.length === 5) {
      stage = "migrate-legacy";
      result = await migrateLegacyRecovery(...args);
    } else if (command === "checkpoint" && args.length === 3) {
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
