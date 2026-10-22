#!/usr/bin/env node
// Run from the built image as the private configuration owner. Never log config.
import { lstat, readFile, rename, writeFile, unlink, chown, realpath } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";

try {
  const [mode, configDirectory, ovPath, envPath] = process.argv.slice(2);
  if (!["check", "sync"].includes(mode) || !configDirectory || !ovPath || !envPath || process.argv.length !== 6) throw Error();
  const built = resolve("dist/server/bridge");
  const { parseMemoryHostConfig } = await import(pathToFileURL(join(built, "memory-host-config.js")).href);
  const { projectMemoryModels } = await import(pathToFileURL(join(built, "memory-model-projection.js")).href);
  const inputs = await Promise.all([ovPath, envPath, join(configDirectory, "memory-service.json")].map(async path => {
    const stat = await lstat(path);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || (stat.mode & 0o077)
      || (process.getuid?.() !== 0 && stat.uid !== process.getuid?.())
      || await realpath(path) !== resolve(path)) throw Error();
    return { path, stat, text: await readFile(path, "utf8") };
  }));
  const config = parseMemoryHostConfig(JSON.parse(inputs[2].text));
  const ov = JSON.parse(inputs[0].text);
  const projected = projectMemoryModels(config, ov, inputs[1].text);
  const contents = [JSON.stringify(projected.openViking, null, 2) + "\n", projected.env];
  if (mode === "check") {
    if (JSON.stringify(ov) !== JSON.stringify(projected.openViking) || inputs[1].text !== projected.env) throw Error();
  } else {
    // Validate all inputs before either atomic replacement. A failed interrupted
    // sync cannot pass the required check before Compose starts.
    for (let i = 0; i < contents.length; i++) {
      const input = inputs[i], temp = `${input.path}.${randomUUID()}.tmp`;
      try {
        await writeFile(temp, contents[i], { flag: "wx", mode: input.stat.mode & 0o777 });
        if (process.getuid?.() === 0) await chown(temp, input.stat.uid, input.stat.gid);
        await rename(temp, input.path);
      } finally { await unlink(temp).catch(error => { if (error.code !== "ENOENT") throw error; }); }
    }
  }
  console.log(mode === "check" ? "MEMORY_MODELS_MATCH" : "MEMORY_MODELS_PROJECTED");
} catch { console.error("MEMORY_MODEL_PROJECTION_FAILED"); process.exitCode = 1; }
