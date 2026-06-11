#!/usr/bin/env node
import { chmodSync, readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const target = resolve(scriptDir, "../dist/bridge/standalone/main.js");
const content = readFileSync(target, "utf8");

if (!content.startsWith("#!")) {
  writeFileSync(target, "#!/usr/bin/env node\n" + content);
}
chmodSync(target, 0o755);
