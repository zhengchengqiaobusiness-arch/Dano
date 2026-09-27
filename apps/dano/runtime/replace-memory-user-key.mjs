#!/usr/bin/env node
import assert from "node:assert/strict";
import { isAbsolute, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { MemoryCredentialStore } from "../dist/server/bridge/memory-credential-store.js";
import { outside, privateDirectory, privateFile, trustedDirectory } from "./private-recovery-path.mjs";

const validId = value => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const fail = code => { throw new Error(code); };

async function keyIdentity(baseUrl, key, timeoutMs) {
  let response;
  try {
    response = await fetch(`${baseUrl}/health`, {
      redirect: "error", signal: AbortSignal.timeout(timeoutMs),
      headers: { "X-API-Key": key },
    });
  } catch { fail("MEMORY_IDENTITY_CHECK_FAILED"); }
  if (response.status === 401 || response.status === 403) return null;
  if (response.status !== 200) fail("MEMORY_IDENTITY_CHECK_FAILED");
  try {
    const identity = await response.json();
    assert.ok(identity && typeof identity === "object" && !Array.isArray(identity));
    return identity;
  } catch { fail("MEMORY_IDENTITY_CHECK_FAILED"); }
}

/** Dano and OpenViking must be quiesced by the operator before this offline
 * update. Only an existing encrypted USER record may be replaced. */
export async function replaceMemoryUserKey(configRoot, dataRoot, userId, newKey) {
  assert.ok([configRoot, dataRoot].every(path => isAbsolute(path) && resolve(path) === path)
    && validId(userId), "INVALID_ARGUMENTS");
  outside(configRoot, [dataRoot]);
  if (typeof newKey !== "string" || !newKey || Buffer.byteLength(newKey) > 16384
    || /[\s\x00-\x1f\x7f]/.test(newKey)) fail("MEMORY_USER_KEY_INVALID");
  const memoryRoot = join(configRoot, "memory");
  const hostRoot = join(dataRoot, "host-state");
  const serviceRoot = join(hostRoot, "memory-service");
  const credentialsRoot = join(serviceRoot, "credentials");
  await Promise.all([trustedDirectory(configRoot), trustedDirectory(dataRoot),
    privateDirectory(memoryRoot), privateDirectory(hostRoot),
    privateDirectory(serviceRoot), privateDirectory(credentialsRoot)]);
  const config = JSON.parse((await privateFile(join(memoryRoot, "memory-service.json"), 65536)).toString("utf8"));
  const url = new URL(config.baseUrl);
  assert.ok(validId(config.accountId) && /^[a-f0-9]{64}$/.test(config.encryptionKey)
    && typeof config.encryptionKeyVersion === "string" && config.encryptionKeyVersion
    && Number.isSafeInteger(config.requestTimeoutMs) && config.requestTimeoutMs > 0
    && ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
    && url.pathname === "/" && !url.search && !url.hash, "MEMORY_MANAGEMENT_CONFIG_INVALID");
  const owner = { accountId: config.accountId, userId };
  const store = new MemoryCredentialStore({ directory: credentialsRoot,
    encryptionKey: Buffer.from(config.encryptionKey, "hex"),
    keyVersion: config.encryptionKeyVersion });
  const oldKey = await store.read(owner);
  if (!oldKey) fail("MEMORY_CREDENTIAL_MISSING");
  if (oldKey === newKey) fail("MEMORY_USER_KEY_UNCHANGED");
  const next = await keyIdentity(url.origin, newKey, config.requestTimeoutMs);
  if (next?.auth_mode !== "api_key" || next.role !== "user"
    || next.account_id !== owner.accountId || next.user_id !== owner.userId) {
    fail("MEMORY_NEW_USER_KEY_OWNER_MISMATCH");
  }
  const previous = await keyIdentity(url.origin, oldKey, config.requestTimeoutMs);
  if (previous?.auth_mode === "api_key") {
    fail(previous.account_id === owner.accountId && previous.user_id === owner.userId
      ? "MEMORY_OLD_USER_KEY_STILL_ACTIVE" : "MEMORY_OLD_USER_KEY_IDENTITY_CHANGED");
  }
  if (previous !== null && previous?.auth_mode !== "none") fail("MEMORY_OLD_USER_KEY_REVOCATION_UNPROVEN");
  await store.write(owner, newKey);
  assert.equal(await store.read(owner), newKey, "MEMORY_CREDENTIAL_REPLACEMENT_FAILED");
  return { replaced: 1 };
}

async function readKeyFromStdin() {
  if (process.stdin.isTTY) fail("MEMORY_USER_KEY_STDIN_REQUIRED");
  const chunks = [];
  let size = 0;
  try {
    for await (const chunk of process.stdin) {
      size += chunk.length;
      if (size > 16385) fail("MEMORY_USER_KEY_INVALID");
      chunks.push(chunk);
    }
    const bytes = Buffer.concat(chunks);
    let value = bytes.toString("utf8");
    if (value.endsWith("\n")) value = value.slice(0, -1);
    bytes.fill(0);
    return value;
  } finally {
    chunks.forEach(chunk => chunk.fill(0));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 3) fail("INVALID_ARGUMENTS");
    const key = await readKeyFromStdin();
    const result = await replaceMemoryUserKey(...args, key);
    process.stdout.write(JSON.stringify({ result: "passed", stage: "replace-user-key", ...result }) + "\n");
  } catch (error) {
    const firstLine = error instanceof Error ? error.message.split("\n", 1)[0] : "";
    const code = /^[A-Z][A-Z0-9_]*$/.test(firstLine) ? firstLine : "MEMORY_USER_KEY_REPLACEMENT_FAILED";
    process.stderr.write(JSON.stringify({ result: "failed", stage: "replace-user-key", code }) + "\n");
    process.exitCode = 1;
  }
}
