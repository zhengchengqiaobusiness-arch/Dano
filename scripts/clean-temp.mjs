import { readdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(process.env.BSS_ROOT || path.join(path.dirname(fileURLToPath(import.meta.url)), ".."));
const dataDir = path.join(root, ".business-skill-studio");
const profileDir = path.join(dataDir, "browser-profile");

const TEMP_DIR_NAMES = new Set([
  "Cache",
  "Code Cache",
  "GPUCache",
  "DawnGraphiteCache",
  "DawnWebGPUCache",
  "ShaderCache",
  "GrShaderCache",
  "Crashpad",
  "BrowserMetrics",
  "optimization_guide_hint_cache",
  "GraphiteDawnCache",
  "Service Worker"
]);

const TEMP_FILE_NAMES = /^(chrome_debug\.log|DEBUG|SingletonLock|SingletonSocket|SingletonCookie)$/i;

const KEEP_TOP_LEVEL = new Set(["catalog", "skills", "recordings"]);

async function remove(target) {
  await rm(target, { recursive: true, force: true });
}

async function walkAndClean(directory) {
  let entries = [];
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (TEMP_DIR_NAMES.has(entry.name)) await remove(full);
      else await walkAndClean(full);
      continue;
    }
    if (TEMP_FILE_NAMES.test(entry.name)) await remove(full);
  }
}

await remove(path.join(dataDir, "screenshots"));
await remove(path.join(root, ".pi", "sessions"));
await walkAndClean(profileDir);

const leftover = [];
for (const name of KEEP_TOP_LEVEL) leftover.push(name);
console.log(`Temporary files were cleared. Kept ${leftover.join(", ")}, login cookies, and exported skills.`);
