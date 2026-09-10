/**
 * 生成期读 doc/。成品禁止携带这些文件。
 */

import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

export const REQUIRED_GUIDE_FILES = [
  "skill-generator-ask-user-question-guide.md",
  "skill-generator-auth-and-token.md",
  "skill-generator-workflow.md",
  "skill-generator-live-options.md",
];

export function generatorGuideDir(env = process.env) {
  const configured = String(env.DANO_SKILL_REFERENCE_DIR || "").trim();
  return configured
    ? path.resolve(configured)
    : path.join(REPO_ROOT, "doc");
}

async function walkMarkdown(dir, files = [], prefix = "") {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(`生成规范目录不存在: ${dir}`);
    }
    throw error;
  }
  for (const entry of entries) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      await walkMarkdown(full, files, rel);
      continue;
    }
    if (entry.isFile() && entry.name.toLowerCase().endsWith(".md")) {
      files.push({ path: rel.replace(/\\/g, "/"), full });
    }
  }
  return files;
}

export async function readGeneratorGuides({ env = process.env } = {}) {
  const root = generatorGuideDir(env);
  const info = await stat(root).catch(() => null);
  if (!info?.isDirectory()) {
    return {
      ok: false,
      error: `生成规范目录不存在: ${root}`,
      dir: root,
      files: [],
    };
  }
  const listed = await walkMarkdown(root);
  if (!listed.length) {
    return {
      ok: false,
      error: `生成规范目录没有任何 .md: ${root}`,
      dir: root,
      files: [],
    };
  }
  const names = new Set(listed.map((item) => path.posix.basename(item.path)));
  const missing = REQUIRED_GUIDE_FILES.filter((name) => !names.has(name));
  const files = [];
  for (const item of listed) {
    files.push({
      path: item.path,
      content: await readFile(item.full, "utf8"),
    });
  }
  if (missing.length) {
    return {
      ok: false,
      error: `缺少生成规范: ${missing.join(", ")}`,
      dir: root,
      missing,
      files,
    };
  }
  return { ok: true, dir: root, missing: [], files };
}
