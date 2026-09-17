import { chmod, lstat, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import {
  checkDeployedSystemPrompt, resolveProductName, syncDeployedSystemPrompt,
  validateSystemPromptPath, writeSystemPromptFile,
} from "../apps/dano/runtime/system-prompt.mjs";

// This command runs as the image's app user with Compose's effective environment.
// It emits markers only: JSON parse errors and filesystem errors can contain data.
try {
  const [action, transaction, expectedName, ...extra] = process.argv.slice(2);
  const runtimeDir = resolve(process.env.DANO_RUNTIME_DIR || "/opt/dano/runtime-data");
  const agentDir = resolve(process.env.PI_CODING_AGENT_DIR || join(runtimeDir, ".pi/agent"));
  if (!agentDir.startsWith(`${runtimeDir}/`)) throw new Error("path");
  const options = {
    agentDir,
    targetPath: join(agentDir, "SYSTEM.md"),
    templatePath: join(process.env.DANO_RUNTIME_DEFAULTS_DIR || "/app/deploy/runtime-defaults", "SYSTEM.md"),
    owner: { uid: 1000, gid: 1000 },
  };
  if (action === "sync" || action === "check") {
    let configuredName;
    if (!process.env.DANO_PRODUCT_NAME?.trim()) {
      const config = JSON.parse(await readFile(process.env.DANO_CONFIG_PATH || "/app/dano.config.json", "utf8"));
      configuredName = typeof config.productName === "string" ? config.productName : undefined;
    }
    options.productName = resolveProductName(process.env.DANO_PRODUCT_NAME, configuredName);
    if (transaction === "--expected-product-name" &&
        (extra.length || expectedName !== options.productName)) throw new Error("PRODUCT_IDENTITY_MISMATCH");
    await (action === "sync" ? syncDeployedSystemPrompt : checkDeployedSystemPrompt)(options);
  } else {
    if (!/^[a-f0-9-]{36}$/.test(transaction || "")) throw new Error("transaction");
    const backupDir = join(agentDir, `.system-rollback-${transaction}`);
    if (action === "backup") {
      await mkdir(agentDir, { recursive: true, mode: 0o700 });
      const stat = await validateSystemPromptPath(options.targetPath, agentDir, options.owner);
      await mkdir(backupDir, { mode: 0o700 });
      await writeFile(join(backupDir, "metadata.json"), JSON.stringify({ exists: Boolean(stat), mode: stat ? stat.mode & 0o7777 : 0o600 }), { mode: 0o600 });
      if (stat) await writeFile(join(backupDir, "SYSTEM.md"), await readFile(options.targetPath), { mode: 0o600 });
    } else if (action === "restore" || action === "discard") {
      const backupStat = await lstat(backupDir).catch(error => {
        if (action === "discard" && error.code === "ENOENT") return undefined;
        throw error;
      });
      if (backupStat && (!backupStat.isDirectory() || backupStat.uid !== 1000 || (backupStat.mode & 0o7777) !== 0o700)) throw new Error("backup");
      if (action === "restore") {
        await validateSystemPromptPath(options.targetPath, agentDir, options.owner);
        const metadata = JSON.parse(await readFile(join(backupDir, "metadata.json"), "utf8"));
        if (metadata.exists) {
          await writeSystemPromptFile(options.targetPath, await readFile(join(backupDir, "SYSTEM.md")), { mode: "replace" });
          await chmod(options.targetPath, metadata.mode);
        } else {
          await rm(options.targetPath, { force: true });
        }
      } else await rm(backupDir, { recursive: true, force: true });
    } else throw new Error("action");
  }
  console.log(`[system-prompt] ${action}: PASS`);
} catch {
  console.error("[system-prompt] FAIL (configuration, content, path or metadata); no contents emitted");
  process.exitCode = 1;
}
