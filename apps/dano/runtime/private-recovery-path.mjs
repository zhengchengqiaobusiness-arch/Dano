import assert from "node:assert/strict";
import { constants } from "node:fs";
import { lstat, open, realpath } from "node:fs/promises";
import { isAbsolute, resolve, sep } from "node:path";

export async function privateDirectory(path) {
  assert.ok(isAbsolute(path) && resolve(path) === path && await realpath(path) === path,
    "UNPROTECTED_RECOVERY_PATH");
  const stat = await lstat(path);
  assert.ok(stat.isDirectory() && !stat.isSymbolicLink() && stat.uid === process.getuid?.()
    && (stat.mode & 0o077) === 0, "UNPROTECTED_RECOVERY_PATH");
}

export async function trustedDirectory(path) {
  assert.ok(isAbsolute(path) && resolve(path) === path && await realpath(path) === path,
    "UNPROTECTED_RECOVERY_PATH");
  const stat = await lstat(path);
  assert.ok(stat.isDirectory() && !stat.isSymbolicLink()
    && (stat.uid === 0 || stat.uid === process.getuid?.())
    && (stat.mode & 0o022) === 0, "UNPROTECTED_RECOVERY_PATH");
}

export async function privateFile(path, maxBytes = 64 * 1024 * 1024) {
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    return await privateFileHandle(handle, maxBytes);
  } finally { await handle.close(); }
}

export async function privateFileHandle(handle, maxBytes = 64 * 1024 * 1024) {
  const stat = await handle.stat();
  assert.ok(stat.isFile() && stat.nlink === 1 && stat.uid === process.getuid?.()
    && (stat.mode & 0o077) === 0 && stat.size <= maxBytes, "UNPROTECTED_RECOVERY_PATH");
  return await handle.readFile();
}

export function outside(path, roots) {
  for (const root of roots) {
    assert.ok(path !== root && !path.startsWith(`${root}${sep}`) && !root.startsWith(`${path}${sep}`),
      "UNPROTECTED_RECOVERY_PATH");
  }
}
