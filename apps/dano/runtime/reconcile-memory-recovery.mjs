#!/usr/bin/env node
import assert from "node:assert/strict";
import { createHash, createHmac, hkdfSync, randomUUID, timingSafeEqual } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, readdir, readFile, rename, rm } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { flockSync } from "fs-ext";
import { OpenVikingClient } from "@openviking/sdk";
import { FileStateStore, OwnerMemoryClient } from "@josephyoung/pi-openviking/host";
import { MemoryCredentialStore } from "../dist/server/bridge/memory-credential-store.js";
import { MemoryRecoveryJournal, isRecoveryDocumentUri } from "../dist/server/bridge/memory-recovery-journal.js";
import { outside, privateDirectory, privateFile, privateFileHandle, trustedDirectory } from "./private-recovery-path.mjs";

const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const validId = value => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const fail = code => { throw new Error(code); };
const maxCheckpointBytes = 1024 * 1024;
const maxReplayReceiptBytes = 64 * 1024 * 1024;
const liveSnapshotFilename = "replay-live-snapshot.json";
const recoveryLockFilename = ".recovery-operation.lock";

async function withRecoveryLocks(roots, action) {
  const handles = [];
  try {
    for (const root of [...new Set(roots)].sort()) {
      await privateDirectory(root);
      const handle = await open(join(root, recoveryLockFilename), constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
      handles.push(handle);
      await privateFileHandle(handle, 0);
      try { flockSync(handle.fd, "exnb"); }
      catch { fail("RECOVERY_OPERATION_BUSY"); }
    }
    return await action();
  } finally { for (const handle of handles.reverse()) await handle.close(); }
}

const sourcePresent = (value, id) => value && typeof value === "object" && (Array.isArray(value)
  ? value.some(item => sourcePresent(item, id))
  : Array.isArray(value.source_message_ids) && value.source_message_ids.includes(id)
    || Object.entries(value).some(([key, item]) => key !== "source_message_ids" && sourcePresent(item, id)));

const snapshotMac = (value, key) => createHmac("sha256",
  Buffer.from(hkdfSync("sha256", key, Buffer.alloc(0), "dano-memory-live-snapshot-v1", 32)))
  .update(JSON.stringify(value)).digest("hex");

function checkedLiveSnapshot(value, binding, key) {
  assert.ok(value && Object.keys(value).sort().join(",") === "binding,documents,mac,version"
    && value.version === 1 && typeof value.mac === "string" && /^[a-f0-9]{64}$/.test(value.mac),
  "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
  const { mac, ...snapshot } = value;
  assert.ok(timingSafeEqual(Buffer.from(mac, "hex"), Buffer.from(snapshotMac(snapshot, key), "hex")),
    "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
  assert.deepEqual(snapshot.binding, binding, "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
  assert.ok(Array.isArray(snapshot.documents), "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
  const seen = new Set();
  for (const document of snapshot.documents) {
    assert.ok(document && Object.keys(document).sort().join(",") === "content,uri"
      && isRecoveryDocumentUri(binding.owner, document.uri) && !seen.has(document.uri)
      && typeof document.content === "string" && document.content.trim()
      && Buffer.byteLength(document.content) <= 1024 * 1024, "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
    seen.add(document.uri);
  }
  return snapshot.documents;
}

// Completed commits move source messages from the working context into this
// task-bound archive. Its owner, source and diff are the durable evidence.
async function verifyArchivedSource(receipts, owner, operation) {
  const taskId = operation.taskId;
  assert.ok(validId(taskId), "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  const task = await receipts.getTask(taskId);
  assert.ok(task && task.status === "completed" && task.task_id === taskId
    && task.task_type === "session_commit" && task.resource_id === operation.remoteSessionId
    && task.result?.session_id === operation.remoteSessionId, "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  const root = `viking://user/${owner.userId}/sessions/${operation.remoteSessionId}/history/`;
  const archiveUri = task.result.archive_uri;
  assert.ok(typeof archiveUri === "string" && archiveUri.startsWith(root), "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  const archiveId = archiveUri.slice(root.length);
  assert.ok(validId(archiveId) && (!operation.archiveId || archiveId === operation.archiveId)
    && task.result.memory_diff_uri === `${archiveUri}/memory_diff.json`, "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  const archive = await receipts.getSessionArchive(operation.remoteSessionId, archiveId);
  assert.ok(archive?.archive_id === archiveId && sourcePresent(archive, operation.id),
    "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  const diff = JSON.parse(await receipts.read(task.result.memory_diff_uri));
  assert.ok(diff.archive_uri === archiveUri && Array.isArray(diff.operations?.adds)
    && Array.isArray(diff.operations?.updates), "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  const uris = [...diff.operations.adds, ...diff.operations.updates].map(change => change?.uri);
  assert.ok(uris.length && uris.every(uri => isRecoveryDocumentUri(owner, uri))
    && (operation.memoryUris ?? []).every(uri => uris.includes(uri)), "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
}

// A stopped service's actual final document set, rather than historical diff
// ordering, is the authority here. Check all known tasks again after restoring
// remote volumes: an old running queue must not overwrite the sealed result.
async function verifyLiveWriters(client, state, receipts) {
  for (const operation of Object.values(state.operations ?? {})) {
    assert.equal(operation.scope, null, "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    if (operation.taskId) {
      assert.equal(await client.writerSettled({ ...operation, phase: "processing" }), true,
        "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    } else if (operation.phase === "commit_unknown") {
      await verifyNewWriter(client, operation, true, receipts);
    } else {
      // A source session without a task receipt could hide a restored commit.
      // Absence is sufficient; otherwise retain the existing recovery path.
      assert.equal(await client.sessionExists(operation.remoteSessionId), false,
        "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    }
    if (["commit_unknown", "processing", "ready"].includes(operation.phase)) {
      const taskId = operation.taskId ?? (await client.findCommit(operation.remoteSessionId))?.taskId;
      await verifyArchivedSource(receipts, state.owner, { ...operation, taskId });
    }
  }
}

/** Run while Dano is stopped, before replacing any data volume. Keep this
 * private generation with the recovery journal until replay completes. The
 * destination still needs the original source/task receipts (normally from a
 * matched full OpenViking snapshot); this does not fabricate missing tasks. */
async function sealLiveSnapshotUnlocked(configDirectory, dataRoot, recoveryRoot, checkpointFile) {
  assert.ok([configDirectory, dataRoot, recoveryRoot, checkpointFile]
    .every(path => isAbsolute(path) && resolve(path) === path), "INVALID_ARGUMENTS");
  outside(checkpointFile, [dataRoot, recoveryRoot]);
  outside(dataRoot, [recoveryRoot]);
  await Promise.all([trustedDirectory(configDirectory), trustedDirectory(dataRoot),
    privateDirectory(join(configDirectory, "memory")), privateDirectory(recoveryRoot)]);
  const checkpointBytes = await privateFile(checkpointFile, maxCheckpointBytes);
  const entries = checkedManifest(JSON.parse(checkpointBytes.toString("utf8")));
  const states = await ownerStates(dataRoot);
  const expectedOwners = entries.map(item => item.owner).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  assert.deepEqual(await recoveryOwners(recoveryRoot), expectedOwners, "RECOVERY_OWNER_SET_MISMATCH");
  assert.deepEqual(states.map(item => item.owner).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    expectedOwners, "RECOVERY_OWNER_SET_MISMATCH");
  const config = JSON.parse((await privateFile(join(configDirectory, "memory", "memory-service.json"), 65536)).toString("utf8"));
  assert.ok(validId(config.accountId), "INVALID_CONFIG");
  const encryptionKey = Buffer.from(config.encryptionKey, "hex");
  const credentials = new MemoryCredentialStore({ directory: join(dataRoot, "host-state", "memory-service", "credentials"),
    encryptionKey, keyVersion: config.encryptionKeyVersion });
  const captures = [];
  let capturedBytes = 0;
  for (const entry of entries) {
    assert.equal(entry.owner.accountId, config.accountId, "OWNER_MISMATCH");
    const item = states.find(state => state.owner.accountId === entry.owner.accountId
      && state.owner.userId === entry.owner.userId);
    assert.ok(item && item.statePath === entry.statePath, "INVALID_RECOVERY_CHECKPOINT");
    const journal = await MemoryRecoveryJournal.inspect(recoveryRoot, entry.owner);
    try {
      await privateFile(join(recoveryRoot, entry.owner.accountId, entry.owner.userId, "replay-preservation.json"));
      fail("MEMORY_RECOVERY_REPLAY_PENDING");
    } catch (error) { if (error?.code !== "ENOENT") throw error; }
    assert.deepEqual(journal.state, item.state, "MEMORY_RECOVERY_STATE_MISMATCH");
    assert.ok(!journal.state.retirement
      && !Object.values(journal.state.governance?.jobs ?? {}).some(job => job.phase !== "complete"),
    "RECOVERY_PENDING_GOVERNANCE");
    assert.ok(entry.eventBytes <= journal.eventBytes.length
      && digest(journal.eventBytes.subarray(0, entry.eventBytes)) === entry.eventSha256,
    "RECOVERY_JOURNAL_CHECKPOINT_MISMATCH");
    const key = await credentials.read(entry.owner);
    assert.ok(key, "CREDENTIAL_MISSING");
    const client = new OwnerMemoryClient({ owner: entry.owner, baseUrl: config.baseUrl,
      apiKey: key, scope: null, timeoutMs: config.requestTimeoutMs });
    const receipts = new OpenVikingClient({ baseUrl: config.baseUrl, apiKey: key, timeout: config.requestTimeoutMs,
      fetch: (input, init) => fetch(input, { ...init, redirect: "error" }) });
    await client.verifyIdentity();
    await verifyLiveWriters(client, journal.state, receipts);
    const captureDocuments = async () => {
      const documents = [];
      let bytes = 0;
      for (const uri of await client.listMemoryDocuments()) {
        assert.ok(isRecoveryDocumentUri(entry.owner, uri), "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
        const document = { uri, content: await client.readMemoryLimited(uri, 1024 * 1024) };
        bytes += Buffer.byteLength(JSON.stringify(document)) + 1;
        assert.ok(bytes <= maxReplayReceiptBytes, "RECOVERY_PRESERVATION_TOO_LARGE");
        documents.push(document);
      }
      return documents;
    };
    const snapshot = { version: 1, binding: { checkpointSha256: digest(checkpointBytes), owner: entry.owner,
      statePath: entry.statePath, latestStateSha256: digest(Buffer.from(JSON.stringify(journal.state))),
      eventSha256: digest(journal.eventBytes) }, documents: await captureDocuments() };
    const effects = expectedEffects(journal.events);
    assert.ok(snapshot.documents.every(document => effects.documents.get(document.uri) !== null),
      "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
    for (const sessionId of effects.sources) {
      assert.equal(await client.sessionExists(sessionId), false, "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
    }
    assert.deepEqual(await captureDocuments(), snapshot.documents, "RECOVERY_LIVE_SNAPSHOT_CHANGED");
    await verifyLiveWriters(client, journal.state, receipts);
    const finalJournal = await MemoryRecoveryJournal.inspect(recoveryRoot, entry.owner);
    assert.deepEqual(finalJournal.state, journal.state, "RECOVERY_LIVE_SNAPSHOT_CHANGED");
    assert.deepEqual(finalJournal.eventBytes, journal.eventBytes, "RECOVERY_LIVE_SNAPSHOT_CHANGED");
    assert.deepEqual(await privateFile(join(dataRoot, entry.statePath)), item.bytes, "RECOVERY_LIVE_SNAPSHOT_CHANGED");
    const sealed = { ...snapshot, mac: snapshotMac(snapshot, encryptionKey) };
    checkedLiveSnapshot(sealed, snapshot.binding, encryptionKey);
    const bytes = JSON.stringify(sealed);
    capturedBytes += Buffer.byteLength(bytes);
    assert.ok(capturedBytes <= maxReplayReceiptBytes, "RECOVERY_PRESERVATION_TOO_LARGE");
    const path = join(recoveryRoot, entry.owner.accountId, entry.owner.userId, liveSnapshotFilename);
    try {
      assert.deepEqual(JSON.parse((await privateFile(path)).toString("utf8")), sealed, "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
    } catch (error) { if (error?.code !== "ENOENT") throw error; }
    captures.push({ path, bytes, count: snapshot.documents.length });
  }
  // No seal is published until all owners have passed read-only preflight.
  for (const capture of captures) await replacePrivate(capture.path, capture.bytes);
  return { owners: captures.length, documents: captures.reduce((sum, capture) => sum + capture.count, 0) };
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
    if (account.name === recoveryLockFilename) {
      await privateFile(join(recoveryRoot, account.name), 0);
      continue;
    }
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
async function migrateLegacyRecoveryUnlocked(configDirectory, dataRoot, sourceRoot, targetRoot, outputCheckpoint) {
  assert.ok([configDirectory, dataRoot, sourceRoot, targetRoot, outputCheckpoint]
    .every(path => isAbsolute(path) && resolve(path) === path), "INVALID_ARGUMENTS");
  outside(targetRoot, [configDirectory, dataRoot, sourceRoot]);
  outside(outputCheckpoint, [configDirectory, dataRoot, sourceRoot, targetRoot]);
  outside(configDirectory, [dataRoot, sourceRoot]);
  outside(dataRoot, [sourceRoot]);
  await Promise.all([trustedDirectory(configDirectory), trustedDirectory(dataRoot),
    privateDirectory(join(configDirectory, "memory")), privateDirectory(sourceRoot),
    privateDirectory(targetRoot), privateDirectory(dirname(outputCheckpoint))]);
  assert.equal((await readdir(targetRoot)).filter(name => name !== recoveryLockFilename).length, 0,
    "RECOVERY_MIGRATION_TARGET_NOT_EMPTY");
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
  const next = await checkpointUnlocked(dataRoot, targetRoot, outputCheckpoint);
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

async function checkpointUnlocked(dataRoot, recoveryRoot, outputFile) {
  assert.ok([dataRoot, recoveryRoot, outputFile].every(path => isAbsolute(path) && resolve(path) === path),
    "INVALID_ARGUMENTS");
  outside(outputFile, [dataRoot, recoveryRoot]);
  outside(dataRoot, [recoveryRoot]);
  const [states, remoteOwners] = await Promise.all([ownerStates(dataRoot), recoveryOwners(recoveryRoot)]);
  const localOwners = states.map(item => item.owner).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  assert.deepEqual(remoteOwners, localOwners, "RECOVERY_OWNER_SET_MISMATCH");
  const owners = [];
  for (const item of states) {
    for (const filename of ["replay-preservation.json", liveSnapshotFilename]) {
      try {
        await privateFile(join(recoveryRoot, item.owner.accountId, item.owner.userId, filename));
        fail("MEMORY_RECOVERY_REPLAY_PENDING");
      } catch (error) { if (error?.code !== "ENOENT") throw error; }
    }
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

function noUnreconciledWrites(oldState, latest, effects) {
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
  const stableOperation = (operation, revoked, addedReceipts = []) => Object.fromEntries(Object.entries(operation)
    .filter(([field]) => !["phase", "payload", "updatedAt", "errorCode",
      "reconciliationPhase", "deliveryAttempts", "nextAttemptAt", ...addedReceipts,
      ...(revoked ? ["memoryUris"] : [])].includes(field)));
  for (const id of oldIds) {
    const before = oldState.operations[id], after = latest.operations[id];
    // Governance can remove or relocate a ready document mapping. Certify the
    // transition with a newly completed barrier and the final replay effects;
    // a blocked phase alone must never authorize arbitrary URI changes.
    const governed = Object.values(latest.governance?.jobs ?? {}).some(job =>
      job.phase === "complete" && job.scope === before.scope && job.writerOperationIds.includes(id)
      && oldState.governance?.jobs?.[job.id]?.phase !== "complete");
    const beforeUris = before.memoryUris ?? [], afterUris = after.memoryUris ?? [];
    const governedMapping = governed
      && afterUris.filter(uri => !beforeUris.includes(uri)).every(uri =>
        typeof effects.documents.get(uri) === "string")
      && beforeUris.filter(uri => !afterUris.includes(uri)).every(uri =>
        effects.cleared || effects.documents.has(uri) && effects.documents.get(uri) === null
        || !effects.documents.has(uri) && effects.sources.has(before.remoteSessionId));
    const revokedWriter = governedMapping && after.phase === "blocked"
      && after.errorCode === "MEMORY_SOURCE_REVOKED";
    // A checkpointed queue can finish before governance revokes it. Added
    // receipts are delivery progress, not changes to its immutable source.
    // Verify the bound task is terminal before replaying the revocation.
    const addedReceipts = revokedWriter ? ["taskId", "archiveId"].filter(field =>
      before[field] === undefined && after[field] !== undefined && validId(after[field])) : [];
    assert.deepEqual(stableOperation(after, governedMapping, addedReceipts),
      stableOperation(before, governedMapping, addedReceipts),
      "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    if (revokedWriter && after.taskId) remoteWriters.push(after);
    if (governedMapping && !effects.cleared) for (const uri of beforeUris.filter(uri => !afterUris.includes(uri))) {
      if (!effects.documents.has(uri)) {
        assert.ok(isRecoveryDocumentUri(latest.owner, uri), "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
        effects.documents.set(uri, null);
        effects.derivedDeletions.add(uri);
      }
    }
    assert.ok((before.phase === after.phase || after.phase === "blocked")
      && (after.payload === before.payload || after.phase === "blocked" && after.payload === undefined),
    "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
  }
  return remoteWriters;
}

/** Read-only proofs must cover every post-checkpoint writer before deletion
 * replay. Unknown or live remote work stays fail-closed; neither append nor
 * commit is retried by this recovery command. */
async function verifyNewWriter(client, operation, preserved = false, receipts) {
  const failCode = "RECOVERY_WRITER_RECONCILIATION_REQUIRED";
  switch (operation.phase) {
    case "blocked":
      assert.equal(operation.errorCode, "MEMORY_SOURCE_REVOKED", failCode);
      assert.ok(validId(operation.taskId), failCode);
      assert.equal(await client.writerSettled({ ...operation, phase: "processing" }), true, failCode);
      return;
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
      const receipt = await client.findCommit(operation.remoteSessionId);
      assert.ok(receipt && (!operation.taskId || receipt.taskId === operation.taskId), failCode);
      assert.equal(await client.writerSettled(operation), true, failCode);
      if (!await client.hasSource(operation)) {
        await verifyArchivedSource(receipts, client.owner, { ...operation, taskId: receipt.taskId });
      }
      return receipt;
    }
    case "processing":
    case "ready": {
      assert.equal(await client.writerSettled(operation), true, failCode);
      if (!await client.hasSource(operation)) await verifyArchivedSource(receipts, client.owner, operation);
      if (operation.phase === "ready" && !preserved) {
        const result = await client.inspect(operation);
        assert.ok(result.status === "ready" && result.archiveId === operation.archiveId
          && new Set(operation.memoryUris).size === operation.memoryUris.length
          && new Set(result.memoryUris).size === result.memoryUris.length, failCode);
        assert.deepEqual([...result.memoryUris].sort(), [...operation.memoryUris].sort(), failCode);
        return result;
      }
      return;
    }
    default: fail(failCode);
  }
}

// The same write-ahead preservation pattern used by selective governance:
// bind an atomic private plan to the exact checkpoint and latest journal before
// clearing anything, then restore it after replay. A retry may find the remote
// documents already gone; it must use the durable plan rather than recapture.
async function preserveWriters(plan) {
  const writers = plan.remoteWriters.filter(operation =>
    ["commit_unknown", "processing", "ready"].includes(operation.phase));
  if (!plan.memoryCleared || !writers.length) {
    assert.ok(!plan.preserved, "RECOVERY_PRESERVATION_MISMATCH");
    return;
  }
  const binding = { ...plan.receipt, eventSha256: plan.eventSha256 };
  let saved;
  try { saved = JSON.parse((await privateFile(plan.preservationPath)).toString("utf8")); }
  catch (error) { if (error?.code !== "ENOENT") throw error; }
  if (saved !== undefined) {
    assert.ok(saved && typeof saved === "object" && !Array.isArray(saved), "RECOVERY_PRESERVATION_MISMATCH");
    assert.deepEqual(Object.keys(saved).sort(), ["binding", "documents", "version", "writerReceipts"], "RECOVERY_PRESERVATION_MISMATCH");
    assert.equal(saved.version, 1, "RECOVERY_PRESERVATION_MISMATCH");
    assert.deepEqual(saved.binding, binding, "RECOVERY_PRESERVATION_MISMATCH");
    assert.ok(Array.isArray(saved.documents) && saved.documents.length > 0, "RECOVERY_PRESERVATION_MISMATCH");
  } else {
    const documents = new Map();
    const writerReceipts = [];
    for (const operation of writers) {
      const proof = plan.writerResults.get(operation.id);
      const result = proof?.status === "ready" ? proof
        : await plan.client.inspect(proof?.taskId ? { ...operation, taskId: proof.taskId } : operation);
      assert.ok(result.status === "ready" && typeof result.archiveId === "string"
        && result.archiveId.length > 0
        && (!operation.archiveId || operation.archiveId === result.archiveId)
        && Array.isArray(result.memoryUris) && result.memoryUris.length > 0
        && new Set(result.memoryUris).size === result.memoryUris.length,
      "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
      writerReceipts.push({ operationId: operation.id, taskId: operation.taskId ?? proof?.taskId,
        archiveId: result.archiveId, memoryUris: result.memoryUris });
      for (const uri of result.memoryUris) {
        assert.ok(isRecoveryDocumentUri(plan.owner, uri), "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
        if (!documents.has(uri)) documents.set(uri, await plan.client.readMemoryLimited(uri, 1024 * 1024));
      }
    }
    saved = { version: 1, binding, writerReceipts,
      documents: [...documents].map(([uri, content]) => ({ uri, content })) };
  }
  assert.ok(Array.isArray(saved.writerReceipts) && saved.writerReceipts.length === writers.length,
    "RECOVERY_PRESERVATION_MISMATCH");
  const receiptIds = new Set(), expectedUris = new Set();
  for (const receipt of saved.writerReceipts) {
    const operation = writers.find(writer => writer.id === receipt?.operationId);
    assert.ok(operation && !receiptIds.has(receipt.operationId)
      && Object.keys(receipt).sort().join(",") === "archiveId,memoryUris,operationId,taskId"
      && receipt.taskId === (operation.taskId ?? plan.writerResults.get(operation.id)?.taskId)
      && typeof receipt.taskId === "string" && receipt.taskId.length > 0
      && typeof receipt.archiveId === "string" && receipt.archiveId.length > 0
      && (!operation.archiveId || operation.archiveId === receipt.archiveId)
      && Array.isArray(receipt.memoryUris) && receipt.memoryUris.length > 0
      && new Set(receipt.memoryUris).size === receipt.memoryUris.length
      && receipt.memoryUris.every(uri => isRecoveryDocumentUri(plan.owner, uri)),
    "RECOVERY_PRESERVATION_MISMATCH");
    if (operation.phase === "ready") assert.deepEqual([...receipt.memoryUris].sort(),
      [...operation.memoryUris].sort(), "RECOVERY_PRESERVATION_MISMATCH");
    receiptIds.add(receipt.operationId);
    for (const uri of receipt.memoryUris) expectedUris.add(uri);
  }
  const seen = new Set();
  for (const document of saved.documents) {
    assert.ok(document && Object.keys(document).sort().join(",") === "content,uri"
      && isRecoveryDocumentUri(plan.owner, document.uri) && !seen.has(document.uri)
      && typeof document.content === "string" && document.content.trim()
      && Buffer.byteLength(document.content) <= 1024 * 1024,
    "RECOVERY_PRESERVATION_MISMATCH");
    seen.add(document.uri);
    // A current deletion intent always wins; never resurrect a newer writer
    // merely because its older receipt still names the deleted document.
    assert.ok(!plan.effects.documents.has(document.uri)
      || plan.effects.documents.get(document.uri) === document.content,
    "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    plan.effects.documents.set(document.uri, document.content);
  }
  assert.deepEqual([...seen].sort(), [...expectedUris].sort(), "RECOVERY_PRESERVATION_MISMATCH");
  const bytes = JSON.stringify(saved);
  assert.ok(Buffer.byteLength(bytes) <= 64 * 1024 * 1024, "RECOVERY_PRESERVATION_TOO_LARGE");
  if (!plan.preserved) await replacePrivate(plan.preservationPath, bytes);
  plan.preserved = saved.documents;
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
  return { documents, sources, cleared, derivedDeletions: new Set() };
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
    assert.ok(entry.eventBytes <= journal.eventBytes.length
      && digest(journal.eventBytes.subarray(0, entry.eventBytes)) === entry.eventSha256
      && (entry.eventBytes === 0 || journal.eventBytes.at(entry.eventBytes - 1) === 10),
    "RECOVERY_JOURNAL_CHECKPOINT_MISMATCH");
    const prefixCount = journal.eventBytes.subarray(0, entry.eventBytes).toString("utf8").split("\n").length - 1;
    const events = journal.events.slice(prefixCount);
    const effects = expectedEffects(events);
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
      remoteWriters = noUnreconciledWrites(restored.state, journal.state, effects);
      receipt.remoteWriterIds = remoteWriters.map(operation => operation.id).sort();
      receipt.derivedDeleteUris = [...effects.derivedDeletions].sort();
    } else {
      let savedReceipt;
      try { savedReceipt = JSON.parse((await privateFile(receiptPath, maxReplayReceiptBytes)).toString("utf8")); }
      catch (error) {
        if (error?.code !== "ENOENT") throw error;
        fail("POST_SNAPSHOT_STATE_MISMATCH");
      }
      assert.ok(Array.isArray(savedReceipt.remoteWriterIds)
        && savedReceipt.remoteWriterIds.every(id => typeof id === "string" && /^[a-f0-9]{64}$/.test(id))
        && new Set(savedReceipt.remoteWriterIds).size === savedReceipt.remoteWriterIds.length,
      "POST_SNAPSHOT_STATE_MISMATCH");
      receipt.remoteWriterIds = savedReceipt.remoteWriterIds;
      assert.ok(Array.isArray(savedReceipt.derivedDeleteUris)
        && savedReceipt.derivedDeleteUris.every(uri => isRecoveryDocumentUri(entry.owner, uri))
        && new Set(savedReceipt.derivedDeleteUris).size === savedReceipt.derivedDeleteUris.length,
      "POST_SNAPSHOT_STATE_MISMATCH");
      receipt.derivedDeleteUris = savedReceipt.derivedDeleteUris;
      if (savedReceipt.liveSnapshotMac !== undefined || savedReceipt.liveDocumentUris !== undefined) {
        assert.ok(typeof savedReceipt.liveSnapshotMac === "string" && /^[a-f0-9]{64}$/.test(savedReceipt.liveSnapshotMac)
          && Array.isArray(savedReceipt.liveDocumentUris)
          && savedReceipt.liveDocumentUris.every(uri => isRecoveryDocumentUri(entry.owner, uri))
          && new Set(savedReceipt.liveDocumentUris).size === savedReceipt.liveDocumentUris.length,
        "POST_SNAPSHOT_STATE_MISMATCH");
        receipt.liveSnapshotMac = savedReceipt.liveSnapshotMac;
        receipt.liveDocumentUris = savedReceipt.liveDocumentUris;
      }
      assert.deepEqual(savedReceipt, receipt, "POST_SNAPSHOT_STATE_MISMATCH");
      for (const uri of receipt.derivedDeleteUris) {
        assert.ok(!effects.documents.has(uri) || effects.documents.get(uri) === null,
          "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
        effects.documents.set(uri, null);
        effects.derivedDeletions.add(uri);
      }
      remoteWriters = receipt.remoteWriterIds.map(id => journal.state.operations[id]);
      assert.ok(remoteWriters.every(operation => operation && operation.id
        && operation.scope === null), "POST_SNAPSHOT_STATE_MISMATCH");
    }
    // An owner clear also removes sessions. A memory-scope clear can preserve
    // a newer pre-commit session, but only after read-only proof that no commit
    // was accepted for it. A settled writer may already own new documents.
    const ownerCleared = events.some(event => event.mutation.kind === "clearOwnerData");
    const memoryCleared = events.some(event => event.mutation.kind === "clearMemoryScope");
    assert.ok(!remoteWriters.length || !ownerCleared, "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    const settledWriters = remoteWriters.filter(operation =>
      ["commit_unknown", "processing", "ready"].includes(operation.phase));
    if (memoryCleared && settledWriters.length) {
      // Governance captured all pre-barrier writers under its lock. Absence
      // from the final clear's writer set proves these belong after the clear;
      // wall-clock timestamps do not establish that ordering.
      const clear = Object.values(journal.state.governance?.jobs ?? {})
        .filter(job => job.kind === "clear" && job.scope === null && job.phase === "complete")
        .sort((a, b) => b.revision - a.revision)[0];
      assert.ok(clear && (!snapshot || restored.state.governance?.jobs?.[clear.id]?.phase !== "complete")
        && settledWriters.every(operation => !clear.writerOperationIds.includes(operation.id)),
      "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    }
    assert.ok(remoteWriters.every(operation => operation.phase === "blocked"
      && operation.errorCode === "MEMORY_SOURCE_REVOKED" || !effects.sources.has(operation.remoteSessionId)),
      "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    const key = await credentials.read(entry.owner);
    assert.ok(key, "CREDENTIAL_MISSING");
    assert.ok(Buffer.byteLength(JSON.stringify(receipt) + "\n") <= maxReplayReceiptBytes,
      "RECOVERY_RECEIPT_TOO_LARGE");
    plans.push({ owner: entry.owner, events, effects, remoteWriters, memoryCleared, statePath, receiptPath, receipt,
      preservationPath: join(recoveryRoot, entry.owner.accountId, entry.owner.userId, "replay-preservation.json"),
      liveSnapshotPath: join(recoveryRoot, entry.owner.accountId, entry.owner.userId, liveSnapshotFilename),
      liveSnapshotBinding: { checkpointSha256, owner: entry.owner, statePath: entry.statePath,
        latestStateSha256: digest(latestBytes), eventSha256: digest(journal.eventBytes) },
      encryptionKey: Buffer.from(config.encryptionKey, "hex"), latestState: journal.state,
      receipts: new OpenVikingClient({ baseUrl: config.baseUrl, apiKey: key, timeout: config.requestTimeoutMs,
        fetch: (input, init) => fetch(input, { ...init, redirect: "error" }) }),
      eventSha256: digest(journal.eventBytes), writerResults: new Map(),
      latestBytes, client: new OwnerMemoryClient({ owner: entry.owner, baseUrl: config.baseUrl,
        apiKey: key, scope: null, timeoutMs: config.requestTimeoutMs }) });
  }
  return plans;
}

async function reconcileUnlocked(configDirectory, dataRoot, recoveryRoot, checkpointFile, preflightOnly = false) {
  const plans = await prepare(configDirectory, dataRoot, recoveryRoot, checkpointFile);
  const summary = { owners: plans.length, events: plans.reduce((sum, plan) => sum + plan.events.length, 0) };
  const remoteWriterChecks = plans.reduce((sum, plan) => sum + plan.remoteWriters.length, 0);
  if (remoteWriterChecks) summary.remoteWriterChecks = remoteWriterChecks;
  if (preflightOnly) return summary;
  for (const plan of plans) await plan.client.verifyIdentity();
  for (const plan of plans) {
    let sealed;
    try {
      sealed = JSON.parse((await privateFile(plan.liveSnapshotPath)).toString("utf8"));
    } catch (error) { if (error?.code !== "ENOENT") throw error; }
    if (!sealed && plan.receipt.liveSnapshotMac) {
      const uris = await plan.client.listMemoryDocuments();
      assert.deepEqual(uris, plan.receipt.liveDocumentUris, "RECOVERY_LIVE_SNAPSHOT_MISMATCH");
      const documents = [];
      let bytes = 0;
      for (const uri of uris) {
        const document = { uri, content: await plan.client.readMemoryLimited(uri, 1024 * 1024) };
        bytes += Buffer.byteLength(JSON.stringify(document)) + 1;
        assert.ok(bytes <= maxReplayReceiptBytes, "RECOVERY_PRESERVATION_TOO_LARGE");
        documents.push(document);
      }
      sealed = { version: 1, binding: plan.liveSnapshotBinding, documents, mac: plan.receipt.liveSnapshotMac };
      plan.completedLive = true;
    }
    if (sealed !== undefined) {
      plan.liveDocuments = checkedLiveSnapshot(sealed, plan.liveSnapshotBinding, plan.encryptionKey);
      plan.receipt.liveSnapshotMac = sealed.mac;
      plan.receipt.liveDocumentUris = plan.liveDocuments.map(document => document.uri);
      // These bodies represent the quiescent generation after all journal
      // intents and writers. They supersede older corrections and deletions.
      plan.effects.documents = new Map(plan.liveDocuments.map(document => [document.uri, document.content]));
      plan.effects.cleared = true;
      await verifyLiveWriters(plan.client, plan.latestState, plan.receipts);
    }
    try {
      await privateFile(plan.preservationPath);
      assert.ok(!plan.liveDocuments, "RECOVERY_PRESERVATION_MISMATCH");
      plan.preserved = true;
    } catch (error) { if (error?.code !== "ENOENT") throw error; }
    assert.ok(Buffer.byteLength(JSON.stringify(plan.receipt) + "\n") <= maxReplayReceiptBytes,
      "RECOVERY_RECEIPT_TOO_LARGE");
  }
  // No replay mutation may run until every new writer is checked against the
  // restored service. Every check is read-only; an incomplete or mismatched
  // remote receipt cannot be followed by a deletion replay or state overlay.
  for (const plan of plans) for (const operation of plan.remoteWriters) {
    if (plan.completedLive) continue;
    const result = await verifyNewWriter(plan.client, operation, Boolean(plan.preserved || plan.liveDocuments), plan.receipts);
    if (result) plan.writerResults.set(operation.id, result);
    if (plan.memoryCleared && ["session_created", "message_unknown", "message_delivered"].includes(operation.phase)) {
      assert.equal(await plan.client.findCommit(operation.remoteSessionId), null,
        "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
    }
  }
  for (const plan of plans) if (!plan.liveDocuments && !plan.memoryCleared && plan.effects.documents.size) {
    for (const operation of plan.remoteWriters.filter(operation =>
      ["commit_unknown", "processing", "ready"].includes(operation.phase))) {
      const proof = plan.writerResults.get(operation.id);
      const result = proof?.status === "ready" ? proof
        : await plan.client.inspect(proof?.taskId ? { ...operation, taskId: proof.taskId } : operation);
      assert.equal(result.status, "ready", "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
      for (const uri of result.memoryUris) if (plan.effects.documents.has(uri)) {
        const intended = plan.effects.documents.get(uri);
        assert.ok(typeof intended === "string", "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
        assert.equal(await plan.client.readMemoryLimited(uri, 1024 * 1024), intended,
          "RECOVERY_WRITER_RECONCILIATION_REQUIRED");
      }
    }
  }
  // Every owner is verified and staged before the first destructive request.
  for (const plan of plans) if (!plan.liveDocuments) await preserveWriters(plan);
  for (const plan of plans) {
    if (plan.completedLive) continue;
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
    // Removing a source may already have removed its derivatives during live
    // governance. Explicitly enforce their recorded absence on an old restore,
    // including retries where the source was removed by the preceding attempt.
    for (const uri of plan.effects.derivedDeletions) await plan.client.removeMemory(uri);
    if (plan.liveDocuments) await plan.client.clearMemoryScope();
    const documents = plan.liveDocuments ?? plan.preserved;
    if (Array.isArray(documents)) for (const { uri, content } of documents) {
      await plan.client.replaceMemory(uri, content);
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
  for (const plan of plans) if (plan.preserved || plan.liveDocuments) {
    if (plan.preserved) await rm(plan.preservationPath);
    if (plan.liveDocuments && !plan.completedLive) await rm(plan.liveSnapshotPath);
    const directory = await open(dirname(plan.preservationPath), constants.O_RDONLY);
    try { await directory.sync(); } finally { await directory.close(); }
  }
  return summary;
}

export const checkpoint = (dataRoot, recoveryRoot, outputFile) =>
  withRecoveryLocks([recoveryRoot], () => checkpointUnlocked(dataRoot, recoveryRoot, outputFile));
export const sealLiveSnapshot = (configDirectory, dataRoot, recoveryRoot, checkpointFile) =>
  withRecoveryLocks([recoveryRoot], () => sealLiveSnapshotUnlocked(configDirectory, dataRoot, recoveryRoot, checkpointFile));
export const reconcile = (configDirectory, dataRoot, recoveryRoot, checkpointFile, preflightOnly = false) =>
  withRecoveryLocks([recoveryRoot], () => reconcileUnlocked(configDirectory, dataRoot, recoveryRoot, checkpointFile, preflightOnly));
export const migrateLegacyRecovery = (configDirectory, dataRoot, sourceRoot, targetRoot, outputCheckpoint) =>
  withRecoveryLocks([sourceRoot, targetRoot], () =>
    migrateLegacyRecoveryUnlocked(configDirectory, dataRoot, sourceRoot, targetRoot, outputCheckpoint));

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
    } else if (command === "seal-live" && args.length === 4) {
      stage = "seal-live";
      result = await sealLiveSnapshot(...args);
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
