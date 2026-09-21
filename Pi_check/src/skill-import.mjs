/**
 * Skill 包导入：校验 → 自动导出到配置目录 → 注册到 skill-catalog.json。
 * 支持两种格式：
 *   strict=true  ← Dano 录制包（含 scripts/、references/、config/）
 *   strict=false ← 通用 Agent Skill（只需 SKILL.md 含 name/description）
 */

import { cp, mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { validateSkillPackageDir } from "./skill-export/validator.mjs";
import {
  upsertExportedSkill,
  skillManifestFromExport,
} from "./skill-export/skill-catalog.mjs";

// ── 简易 YAML frontmatter 解析（key: value 行，足以读 SKILL.md）────────────
function parseFrontmatter(text) {
  if (!String(text || "").startsWith("---")) return {};
  const endIdx = text.indexOf("\n---", 3);
  if (endIdx === -1) return {};
  const block = text.slice(4, endIdx);
  const result = {};
  let currentKey = null;
  let currentLines = [];

  for (const line of block.split("\n")) {
    // 多行值（以空格/缩进开头）
    if (currentKey && line.match(/^\s+\S/)) {
      currentLines.push(line.trim());
      continue;
    }
    // 新 key
    if (currentKey) {
      result[currentKey] = currentLines.join(" ").replace(/^["'](.*)["']$/, "$1").trim();
      currentKey = null;
      currentLines = [];
    }
    const m = line.match(/^([\w-]+)\s*:\s*(.*)$/);
    if (!m) continue;
    currentKey = m[1].trim();
    const val = m[2].trim();
    if (val) {
      currentLines = [val];
    }
  }
  if (currentKey && currentLines.length) {
    result[currentKey] = currentLines.join(" ").replace(/^["'](.*)["']$/, "$1").trim();
  }
  return result;
}

// 从 SKILL.md body 提取第一个 H1 标题（如 "# 数据分析技能"）
function extractFirstH1(text) {
  const bodyStart = text.startsWith("---") ? (text.indexOf("\n---", 3) + 4) : 0;
  const body = text.slice(bodyStart);
  const m = body.match(/^#\s+(.+)$/m);
  return m ? m[1].trim() : null;
}

async function readJsonSafe(filePath) {
  try {
    return JSON.parse(await readFile(filePath, "utf8"));
  } catch {
    return null;
  }
}

async function readTextSafe(filePath) {
  try {
    return await readFile(filePath, "utf8");
  } catch {
    return "";
  }
}

// ── 宽松校验：只要求 SKILL.md 存在且 name 非空 ─────────────────────────────
async function validateLenient(root) {
  const text = await readTextSafe(path.join(root, "SKILL.md"));
  if (!text) {
    return { ok: false, issues: [{ severity: "error", code: "missing_skill_md", message: "缺少 SKILL.md" }] };
  }
  const fm = parseFrontmatter(text);
  const name = String(fm.name || "").trim();
  if (!name) {
    return {
      ok: false,
      issues: [{ severity: "error", code: "missing_name", message: "SKILL.md frontmatter 缺少 name 字段" }],
    };
  }
  return { ok: true, issues: [] };
}

/**
 * 从包根目录读取元数据。只读，不写。
 */
export async function readSkillPackageMeta(packageDir) {
  const root = path.resolve(String(packageDir || ""));

  const handbookText = await readTextSafe(path.join(root, "SKILL.md"));
  const frontmatter = parseFrontmatter(handbookText);

  const contract = await readJsonSafe(path.join(root, "references", "CONTRACT.json"));
  const runtime = await readJsonSafe(path.join(root, "config", "runtime.json")) || {};

  const capabilities = Array.isArray(contract?.capabilities) ? contract.capabilities : [];
  const hasWrite = capabilities.some((c) =>
    /create|update|delete|submit|write|approve|reject|withdraw/i.test(String(c.kind || ""))
  );

  const skillName = String(frontmatter.name || "").trim();
  const frontDesc = String(frontmatter.description || "").trim();
  const h1Title = extractFirstH1(handbookText);
  const title = h1Title || skillName;
  const description = frontDesc || title;

  const subsystem = String(runtime.subsystem || "oa");
  const tenant = String(runtime.tenant || "");

  const actionMatch = skillName.match(/\.(.+)$/);
  const action = actionMatch ? actionMatch[1] : skillName;

  return {
    skillId: skillName,
    title,
    description,
    tenant,
    subsystem,
    action,
    riskLevel: hasWrite ? "L3" : "L1",
    capabilities,
    exportPath: root,
    contract,
    runtime,
  };
}

// 导出目录内的 slug
function importedSlug(skillId) {
  const clean = String(skillId || "unknown")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/\.+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return `${clean}-imported`;
}

/**
 * 校验并导入 skill 包。
 *
 * @param {object} files     RecordingFiles 实例
 * @param {string} packageDir 已解压/已有包根目录（绝对路径）
 * @param {object} opts
 *   @param {string}  [opts.outDir]   配置的导出目录；非空时自动复制
 *   @param {boolean} [opts.strict]   true = 完整 Dano 校验，false = 只检查 SKILL.md（默认 false）
 *
 * 成功：{ ok: true, skill, exported_to }
 * 失败：{ ok: false, errors, issues }
 */
export async function importSkillPackage(files, packageDir, { outDir = "", strict = false } = {}) {
  const srcRoot = path.resolve(String(packageDir || ""));
  if (!srcRoot) return { ok: false, errors: ["package_dir 不能为空"] };

  // ── 1. 校验 ─────────────────────────────────────────────────────────────
  const validation = strict
    ? await validateSkillPackageDir(srcRoot)
    : await validateLenient(srcRoot);

  if (!validation.ok) {
    const errors = validation.issues
      .filter((i) => i.severity === "error")
      .map((i) => `[${i.code}] ${i.message}`);
    return { ok: false, errors, issues: validation.issues };
  }

  // ── 2. 元数据 ────────────────────────────────────────────────────────────
  const meta = await readSkillPackageMeta(srcRoot);
  if (!meta.skillId) return { ok: false, errors: ["SKILL.md frontmatter 缺少 name 字段"] };

  // ── 3. 自动导出到配置目录 ─────────────────────────────────────────────────
  let finalDir = srcRoot;
  let exportedTo = null;
  if (outDir) {
    const dest = path.join(path.resolve(outDir), importedSlug(meta.skillId));
    try {
      await mkdir(dest, { recursive: true });
      await cp(srcRoot, dest, { recursive: true, force: true });
      finalDir = dest;
      exportedTo = dest;
    } catch {
      // 导出失败不阻断注册
    }
  }

  // ── 4. 写 skill-catalog.json ─────────────────────────────────────────────
  const manifest = skillManifestFromExport({
    skillId: meta.skillId,
    title: meta.title,
    description: meta.description,
    tenant: meta.tenant,
    subsystem: meta.subsystem,
    action: meta.action,
    exportPath: finalDir,
    draft: { capabilities: meta.capabilities },
  });
  manifest.source = "imported";
  manifest.package_dir = finalDir;

  const item = await upsertExportedSkill(files, manifest);
  return { ok: true, skill: item, exported_to: exportedTo };
}
