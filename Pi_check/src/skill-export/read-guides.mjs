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
  if (!configured) return path.join(REPO_ROOT, "doc");
  if (path.isAbsolute(configured)) return path.resolve(configured);
  const root = String(env.DANO_SKILL_REFERENCE_ROOT || "").trim();
  return root ? path.resolve(root, configured) : path.resolve(configured);
}

/**
 * 只读顶层目录中 skill-generator-*.md 和 REQUIRED_GUIDE_FILES 文件。
 * 不递归子目录——子目录可能含有大量无关 .md（如 O2OA、CodeMirror 等），
 * 全部读入会使 LLM 上下文爆炸导致 Skill4 卡死。
 */
async function listTopLevelGuides(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(`生成规范目录不存在: ${dir}`);
    }
    throw error;
  }
  const files = [];
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const name = entry.name;
    if (!name.toLowerCase().endsWith(".md")) continue;
    // 只收录：必须文件 或 skill-generator-*.md
    const isRequired = REQUIRED_GUIDE_FILES.includes(name);
    const isGuide = name.startsWith("skill-generator-") || name.startsWith("skill_generator_");
    if (!isRequired && !isGuide) continue;
    files.push({ path: name, full: path.join(dir, name) });
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
  const listed = await listTopLevelGuides(root);
  if (!listed.length) {
    return {
      ok: false,
      error: `生成规范目录没有任何 skill-generator-*.md: ${root}`,
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
