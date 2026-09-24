#!/usr/bin/env node
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { lstat, readFile, realpath } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { OwnerMemoryClient } from "@josephyoung/pi-openviking/host";
import { MemoryCredentialStore } from "./dist/server/bridge/memory-credential-store.js";

// Run with Dano stopped. The post-snapshot ledger and owner state must come
// from outside the older backup being restored. Never print either secret.
let stage = "arguments";
try {
  const [configDirectory, dataDirectory, ledgerFile, ...extra] = process.argv.slice(2);
  assert.ok(configDirectory && dataDirectory && ledgerFile && extra.length === 0, "INVALID_ARGUMENTS");
  stage = "load";
  const ledger = JSON.parse(await readFile(ledgerFile, "utf8"));
  const config = JSON.parse(await readFile(resolve(configDirectory, "memory", "memory-service.json"), "utf8"));
  assert.ok(typeof config.accountId === "string"
    && /^[A-Za-z0-9_-]{1,128}$/.test(config.accountId), "INVALID_CONFIG");
  const entries = ledger.version === 1 ? [ledger] : ledger.version === 2 ? ledger.owners : undefined;
  assert.ok(Array.isArray(entries) && entries.length > 0 && entries.length <= 256, "INVALID_LEDGER");
  const seenOwners = new Set();
  const seenStatePaths = new Set();
  // Validate every owner and post-snapshot state before touching any remote
  // memory. A partial replay can be retried because each public operation and
  // final document-set check is idempotent.
  for (const entry of entries) {
    assert.equal(config.accountId, entry?.owner?.accountId, "OWNER_MISMATCH");
    assert.ok(typeof entry.owner.userId === "string"
      && /^[A-Za-z0-9_-]{1,128}$/.test(entry.owner.userId)
      && !seenOwners.has(entry.owner.userId), "INVALID_LEDGER");
    seenOwners.add(entry.owner.userId);
    assert.ok(Array.isArray(entry.deleteUris) && Array.isArray(entry.sourceSessionIds)
      && Array.isArray(entry.expectedDocumentUris) && entry.retainedDocuments
      && typeof entry.retainedDocuments === "object" && !Array.isArray(entry.retainedDocuments), "INVALID_LEDGER");
    assert.ok(typeof entry.statePath === "string"
      && entry.statePath.split("/").every(part => part && part !== "." && part !== ".."), "INVALID_LEDGER");
    const root = `viking://user/${entry.owner.userId}/memories/`;
    const retainedUris = Object.keys(entry.retainedDocuments);
    const uris = [...entry.deleteUris, ...entry.expectedDocumentUris, ...retainedUris];
    assert.ok(uris.every(uri => typeof uri === "string" && uri.startsWith(root) && uri.endsWith(".md")), "INVALID_LEDGER");
    assert.ok(entry.sourceSessionIds.every(id => typeof id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(id)), "INVALID_LEDGER");
    assert.ok(Object.values(entry.retainedDocuments).every(value => typeof value === "string" && value.trim()), "INVALID_LEDGER");
    assert.ok(entry.expectedDocumentUris.every(uri => !entry.deleteUris.includes(uri))
      && retainedUris.every(uri => entry.expectedDocumentUris.includes(uri)), "INVALID_LEDGER");
    assert.ok(entry.deleteUris.length === new Set(entry.deleteUris).size
      && entry.expectedDocumentUris.length === new Set(entry.expectedDocumentUris).size
      && entry.sourceSessionIds.length === new Set(entry.sourceSessionIds).size, "INVALID_LEDGER");
    const statePath = resolve(dataDirectory, entry.statePath);
    assert.ok(statePath.startsWith(`${resolve(dataDirectory)}${sep}`)
      && !seenStatePaths.has(statePath), "INVALID_LEDGER");
    seenStatePaths.add(statePath);
    const stat = await lstat(statePath);
    assert.ok(stat.isFile() && stat.uid === process.getuid?.()
      && (stat.mode & 0o077) === 0
      && (await realpath(statePath)).startsWith(`${await realpath(dataDirectory)}${sep}`), "INVALID_LEDGER");
    const stateBytes = await readFile(statePath);
    assert.equal(createHash("sha256").update(stateBytes).digest("hex"), entry.postStateSha256,
      "POST_SNAPSHOT_STATE_MISMATCH");
    const state = JSON.parse(stateBytes);
    assert.ok(state.owner?.accountId === entry.owner.accountId
      && state.owner?.userId === entry.owner.userId, "OWNER_MISMATCH");
  }

  stage = "credential";
  const credentials = new MemoryCredentialStore({
    directory: resolve(dataDirectory, "host-state", "memory-service", "credentials"),
    encryptionKey: Buffer.from(config.encryptionKey, "hex"),
    keyVersion: config.encryptionKeyVersion,
  });
  const clients = [];
  for (const entry of entries) {
    const key = await credentials.read(entry.owner);
    assert.ok(key, "CREDENTIAL_MISSING");
    clients.push(new OwnerMemoryClient({ owner: entry.owner, baseUrl: config.baseUrl, apiKey: key, scope: null,
      timeoutMs: config.requestTimeoutMs }));
  }

  stage = "replay";
  for (const client of clients) await client.verifyIdentity();
  for (const [index, entry] of entries.entries()) {
    const client = clients[index];
    for (const remoteSessionId of entry.sourceSessionIds) {
      await client.removeSource({ owner: entry.owner, scope: null, remoteSessionId });
    }
    for (const uri of entry.deleteUris) await client.removeMemory(uri);
    for (const [uri, content] of Object.entries(entry.retainedDocuments)) await client.replaceMemory(uri, content);
  }

  stage = "readback";
  for (const [index, entry] of entries.entries()) {
    const client = clients[index];
    for (const remoteSessionId of entry.sourceSessionIds) {
      assert.equal(await client.sessionExists(remoteSessionId), false, "SOURCE_RESTORED");
    }
    for (const [uri, content] of Object.entries(entry.retainedDocuments)) {
      assert.equal(await client.readMemory(uri), content, "RETAINED_DOCUMENT_MISMATCH");
    }
    assert.deepEqual(await client.listMemoryDocuments(), [...entry.expectedDocumentUris].sort(), "DOCUMENT_SET_MISMATCH");
  }
  process.stdout.write(JSON.stringify({ result: "passed", owners: entries.length,
    deletedUris: entries.reduce((sum, entry) => sum + entry.deleteUris.length, 0),
    removedSources: entries.reduce((sum, entry) => sum + entry.sourceSessionIds.length, 0),
    retainedDocuments: entries.reduce((sum, entry) => sum + Object.keys(entry.retainedDocuments).length, 0),
    expectedDocuments: entries.reduce((sum, entry) => sum + entry.expectedDocumentUris.length, 0) }) + "\n");
} catch (error) {
  const firstLine = error instanceof Error ? error.message.split("\n", 1)[0] : "";
  const code = /^[A-Z][A-Z0-9_]*$/.test(firstLine) ? firstLine : "REPLAY_FAILED";
  process.stderr.write(JSON.stringify({ result: "failed", stage, code }) + "\n");
  process.exitCode = 1;
}
