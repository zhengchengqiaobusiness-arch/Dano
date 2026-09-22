/**
 * 生成期读 doc/ 目录里当前有的全部文件。增删以目录为准，不写死文件名。
 * 成品禁止携带这些文件。
 */

import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const SKIP_DIRS = new Set(["node_modules", ".git"]);

export function generatorGuideDir(env = process.env) {
  const configured = String(env.DANO_SKILL_REFERENCE_DIR || "").trim();
  if (!configured) return path.join(REPO_ROOT, "doc");
  if (path.isAbsolute(configured)) return path.resolve(configured);
  const root = String(env.DANO_SKILL_REFERENCE_ROOT || "").trim();
  return root ? path.resolve(root, configured) : path.resolve(configured);
}

async function listGuideFiles(dir, rel = "") {
  const here = rel ? path.join(dir, rel) : dir;
  let entries;
  try {
    entries = await readdir(here, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(`生成规范目录不存在: ${dir}`);
    }
    throw error;
  }
  const files = [];
  for (const entry of entries) {
    if (entry.name.startsWith(".")) continue;
    const nextRel = rel ? `${rel}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      files.push(...await listGuideFiles(dir, nextRel));
      continue;
    }
    if (!entry.isFile()) continue;
    files.push({
      path: nextRel.replace(/\\/g, "/"),
      full: path.join(dir, nextRel),
    });
  }
  return files;
}

export async function readGeneratorGuides({ env = process.env, includeContent = false } = {}) {
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
  const listed = (await listGuideFiles(root)).sort((a, b) => a.path.localeCompare(b.path));
  if (!listed.length) {
    return {
      ok: false,
      error: `生成规范目录是空的: ${root}`,
      dir: root,
      files: [],
    };
  }
  const files = [];
  for (const item of listed) {
    files.push({
      path: item.path,
      ...(includeContent ? { content: await readFile(item.full, "utf8") } : {}),
    });
  }
  return { ok: true, dir: root, files };
}
