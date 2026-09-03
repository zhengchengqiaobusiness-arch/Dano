import { cp, mkdir, readdir, stat } from "node:fs/promises";
import path from "node:path";

const LOGIN_RELATIVE = [
  "Default/Network/Cookies",
  "Default/Network/Cookies-journal",
  "Default/Cookies",
  "Default/Cookies-journal",
  "Default/Local Storage",
  "Default/Session Storage",
  "Default/Login Data",
  "Default/Login Data-journal",
  "Default/Preferences",
  "Local State"
];

const LOGIN_MARKERS = [
  "Default/Network/Cookies",
  "Default/Cookies",
  "Default/Local Storage"
];

async function exists(target: string) {
  try {
    await stat(target);
    return true;
  } catch {
    return false;
  }
}

export async function hasLoginState(profileDir: string) {
  for (const rel of LOGIN_MARKERS) {
    const target = path.join(profileDir, rel);
    try {
      const info = await stat(target);
      if (info.isFile() && info.size > 0) return true;
      if (info.isDirectory()) {
        const entries = await readdir(target);
        if (entries.length) return true;
      }
    } catch { /* missing */ }
  }
  return false;
}

async function copyLoginState(fromDir: string, toDir: string) {
  await mkdir(toDir, { recursive: true });
  for (const rel of LOGIN_RELATIVE) {
    const from = path.join(fromDir, rel);
    if (!await exists(from)) continue;
    const to = path.join(toDir, rel);
    await mkdir(path.dirname(to), { recursive: true });
    await cp(from, to, { recursive: true, force: true }).catch(() => {});
  }
}

async function loginStamp(profileDir: string) {
  let latest = 0;
  for (const rel of LOGIN_MARKERS) {
    try {
      const info = await stat(path.join(profileDir, rel));
      if (info.mtimeMs > latest) latest = info.mtimeMs;
    } catch { /* missing */ }
  }
  return latest;
}

async function newestLoginSource(sharedDir: string, exclude: string) {
  let best: { dir: string; at: number } | undefined;
  const consider = async (dir: string) => {
    if (path.resolve(dir) === path.resolve(exclude) || !await hasLoginState(dir)) return;
    const at = await loginStamp(dir);
    if (!best || at > best.at) best = { dir, at };
  };
  await consider(sharedDir);
  let entries = [];
  try {
    entries = await readdir(sharedDir, { withFileTypes: true });
  } catch {
    return best?.dir;
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name === "Default") continue;
    await consider(path.join(sharedDir, entry.name));
  }
  return best?.dir;
}

export async function seedPageProfile(sharedDir: string, pageDir: string) {
  await mkdir(pageDir, { recursive: true });
  if (await hasLoginState(pageDir)) return pageDir;
  const source = await newestLoginSource(sharedDir, pageDir);
  if (source) await copyLoginState(source, pageDir);
  return pageDir;
}

export async function syncLoginState(fromDir: string, sharedDir: string) {
  if (path.resolve(fromDir) === path.resolve(sharedDir)) return;
  if (!await hasLoginState(fromDir)) return;
  await copyLoginState(fromDir, sharedDir);
}
