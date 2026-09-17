import { randomUUID } from "node:crypto";
import { link, lstat, mkdir, readFile, realpath, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { writeFile as writeFileAtomic } from "atomically";

function isErrorCode(error, code) {
  return error instanceof Error && "code" in error && error.code === code;
}

export function resolveProductName(environmentName, configuredName) {
  const productName = environmentName?.trim() || configuredName?.trim();
  if (!productName) {
    throw new Error(
      "Set productName in dano.config.json or provide DANO_PRODUCT_NAME",
    );
  }
  return productName;
}

export function renderSystemPrompt(template, productName) {
  const name = resolveProductName(productName, undefined);
  const content = template.replaceAll("{产品名称}", () => name);
  if (content.includes("{产品名称}")) throw new Error("SYSTEM_PLACEHOLDER");
  return content;
}

// Deployment callers derive this path from the same environment as entrypoint.
// Reject links before reading/writing; never repair an unexpected owner or path.
export async function validateSystemPromptPath(targetPath, agentDir, owner) {
  if (resolve(targetPath) !== join(resolve(agentDir), "SYSTEM.md")) {
    throw new Error("SYSTEM_PATH");
  }
  if (await realpath(agentDir) !== resolve(agentDir)) throw new Error("SYSTEM_PATH");
  const parent = await lstat(agentDir);
  if (!parent.isDirectory() || parent.uid !== owner.uid || parent.gid !== owner.gid || (parent.mode & 0o022)) {
    throw new Error("SYSTEM_DIRECTORY_METADATA");
  }
  try {
    const stat = await lstat(targetPath);
    if (!stat.isFile() || stat.nlink !== 1) throw new Error("SYSTEM_PATH");
    if (stat.uid !== owner.uid || stat.gid !== owner.gid) throw new Error("SYSTEM_OWNER");
    return stat;
  } catch (error) {
    if (isErrorCode(error, "ENOENT")) return undefined;
    throw error;
  }
}

export async function checkDeployedSystemPrompt(options) {
  const stat = await validateSystemPromptPath(options.targetPath, options.agentDir, options.owner);
  if (!stat || (stat.mode & 0o7777) !== 0o600) throw new Error("SYSTEM_PERMISSIONS");
  const content = await readFile(options.targetPath, "utf8");
  if (content.includes("{产品名称}")) throw new Error("SYSTEM_PLACEHOLDER");
  const expected = renderSystemPrompt(await readFile(options.templatePath, "utf8"), options.productName);
  if (content !== expected) throw new Error("SYSTEM_CONTENT");
}

export async function syncDeployedSystemPrompt(options) {
  const content = renderSystemPrompt(await readFile(options.templatePath, "utf8"), options.productName);
  await mkdir(options.agentDir, { recursive: true, mode: 0o700 });
  await validateSystemPromptPath(options.targetPath, options.agentDir, options.owner);
  await writeFileAtomic(options.targetPath, content, { mode: 0o600, chown: options.owner });
  await checkDeployedSystemPrompt(options);
}

async function initializeFile(targetPath, content) {
  const temporaryPath = join(
    dirname(targetPath),
    `.${basename(targetPath)}.dano-${randomUUID()}`,
  );

  // `atomically` completes and fsyncs the temporary file before publishing it.
  // A same-directory hard link then provides the no-clobber create primitive
  // that rename-based atomic writers intentionally do not offer.
  await writeFileAtomic(temporaryPath, content, {
    chown: false,
    mode: false,
  });

  try {
    await link(temporaryPath, targetPath);
    return "written";
  } catch (error) {
    if (isErrorCode(error, "EEXIST")) return "preserved";
    throw error;
  } finally {
    await unlink(temporaryPath).catch(error => {
      if (!isErrorCode(error, "ENOENT")) throw error;
    });
  }
}

export async function writeSystemPromptFile(targetPath, content, options) {
  if (options.mode === "if-missing") {
    return initializeFile(targetPath, content);
  }

  await writeFileAtomic(targetPath, content);
  return "written";
}

export async function syncSystemPrompt(options) {
  const template = await readFile(options.templatePath, "utf8");
  const content = renderSystemPrompt(template, options.productName);
  return writeSystemPromptFile(options.targetPath, content, {
    mode: options.mode,
  });
}
