import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { mkdtemp, mkdir, readFile, writeFile, stat } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const root = path.resolve(import.meta.dirname, "..");

async function exists(target: string) {
  try {
    await stat(target);
    return true;
  } catch {
    return false;
  }
}

test("start.bat never kills the user browser and clears temp files on launch", async () => {
  const [bat, stop] = await Promise.all([
    readFile(path.join(root, "start.bat"), "utf8"),
    readFile(path.join(root, "scripts", "stop-studio.mjs"), "utf8")
  ]);
  assert.match(bat, /clean-temp\.mjs/);
  assert.match(bat, /Your Chrome\/Edge pages stay open/);
  assert.doesNotMatch(bat, /stop the page and every Studio process/);
  assert.doesNotMatch(stop, /taskkill[\s\S]*\/IM[\s\S]*chrome/i);
  assert.doesNotMatch(stop, /taskkill[\s\S]*\/IM[\s\S]*msedge/i);
  assert.match(stop, /LISTENING/);
  assert.match(stop, /userBrowsers/);
  assert.match(stop, /browser-profile/);
});

test("clean-temp removes caches and screenshots but keeps login, catalog, recordings and skills", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "bss-clean-"));
  const dataDir = path.join(temporary, ".business-skill-studio");
  const profile = path.join(dataDir, "browser-profile", "Default");
  await mkdir(path.join(profile, "Cache", "Cache_Data"), { recursive: true });
  await mkdir(path.join(profile, "Code Cache", "js"), { recursive: true });
  await mkdir(path.join(profile, "Local Storage"), { recursive: true });
  await mkdir(path.join(dataDir, "screenshots"), { recursive: true });
  await mkdir(path.join(dataDir, "catalog"), { recursive: true });
  await mkdir(path.join(dataDir, "recordings", "rec_keep"), { recursive: true });
  await mkdir(path.join(dataDir, "skills"), { recursive: true });
  await mkdir(path.join(temporary, ".pi", "sessions"), { recursive: true });
  await writeFile(path.join(profile, "Cache", "Cache_Data", "data_0"), "cache");
  await writeFile(path.join(profile, "Local Storage", "000003.log"), "login");
  await writeFile(path.join(profile, "Cookies"), "cookie");
  await writeFile(path.join(dataDir, "screenshots", "1.jpg"), "img");
  await writeFile(path.join(dataDir, "catalog", "capabilities.json"), "{}");
  await writeFile(path.join(dataDir, "recordings", "rec_keep", "session.json"), "{}");
  await writeFile(path.join(dataDir, "skills", "registry.json"), "[]");
  await writeFile(path.join(temporary, ".pi", "sessions", "old.json"), "{}");

  const result = await execFileAsync(process.execPath, [path.join(root, "scripts", "clean-temp.mjs")], {
    env: { ...process.env, BSS_ROOT: temporary }
  });
  assert.match(result.stdout, /Temporary files were cleared/);
  assert.equal(await exists(path.join(profile, "Cache")), false);
  assert.equal(await exists(path.join(profile, "Code Cache")), false);
  assert.equal(await exists(path.join(dataDir, "screenshots")), false);
  assert.equal(await exists(path.join(temporary, ".pi", "sessions")), false);
  assert.equal(await exists(path.join(profile, "Local Storage", "000003.log")), true);
  assert.equal(await exists(path.join(profile, "Cookies")), true);
  assert.equal(await exists(path.join(dataDir, "catalog", "capabilities.json")), true);
  assert.equal(await exists(path.join(dataDir, "recordings", "rec_keep", "session.json")), true);
  assert.equal(await exists(path.join(dataDir, "skills", "registry.json")), true);
});
