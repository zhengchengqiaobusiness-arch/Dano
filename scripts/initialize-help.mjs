import { chmodSync, cpSync, lstatSync, mkdirSync, readdirSync } from "node:fs";
import { isAbsolute, join } from "node:path";

/** Seed a new host manual; an existing manual is entirely administrator-owned. */
export function initializeHelp(directory, defaults) {
  if (!isAbsolute(directory)) throw new Error("DANO_HELP_DIR must be absolute");
  let existing;
  try { existing = lstatSync(directory); } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (existing) {
    if (!existing.isDirectory()) throw new Error("Help directory must be a real directory");
    if (readdirSync(directory).length) return false;
  }
  mkdirSync(directory, { recursive: true, mode: 0o755 });
  cpSync(defaults, directory, { recursive: true, force: false, errorOnExist: true });
  // Public documentation must remain readable by the non-root app, even when
  // the host deploy command runs with a restrictive umask.
  function makeReadable(path) {
    const stat = lstatSync(path);
    if (stat.isDirectory()) {
      chmodSync(path, 0o755);
      for (const name of readdirSync(path)) makeReadable(join(path, name));
    } else if (stat.isFile()) chmodSync(path, 0o644);
  }
  makeReadable(directory);
  return true;
}
