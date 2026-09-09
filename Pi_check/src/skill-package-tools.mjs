/**
 * Skill 4 运输：写产物、校验、隔离运行。不认业务。
 */

import { mkdir, writeFile, cp, rm, readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const BACK_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "back");
const REPO_ROOT = path.resolve(BACK_ROOT, "..");

const REQUIRED_GENERATOR_GUIDES = [
  "skill-generator-ask-user-question-guide.md",
  "skill-generator-auth-and-token.md",
  "skill-generator-workflow.md",
  "skill-generator-live-options.md",
];

export function generatorGuideRoot() {
  const configured = String(process.env.DANO_SKILL_REFERENCE_DIR || "doc").trim() || "doc";
  const root = path.resolve(REPO_ROOT, configured);
  const relative = path.relative(REPO_ROOT, root);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("DANO_SKILL_REFERENCE_DIR 不得超出项目根目录");
  }
  return root;
}

async function listMarkdownFiles(root) {
  const found = [];
  async function walk(dir) {
    let entries = [];
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(full);
        continue;
      }
      if (entry.isFile() && entry.name.toLowerCase().endsWith(".md")) {
        found.push(full);
      }
    }
  }
  await walk(root);
  found.sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" }));
  return found;
}

export async function readGeneratorGuides() {
  const root = generatorGuideRoot();
  let info;
  try {
    info = await stat(root);
  } catch {
    return { ok: false, error: `Skill 参考目录不存在: ${root}`, files: [] };
  }
  if (!info.isDirectory()) {
    return { ok: false, error: `Skill 参考目录不是文件夹: ${root}`, files: [] };
  }
  const paths = await listMarkdownFiles(root);
  if (!paths.length) {
    return { ok: false, error: `Skill 参考目录中没有 Markdown 文件: ${root}`, files: [] };
  }
  const files = [];
  for (const full of paths) {
    const rel = path.relative(root, full).split(path.sep).join("/");
    files.push({ path: rel, content: await readFile(full, "utf8") });
  }
  const names = new Set(files.map((item) => item.path.split("/").pop()));
  const missing = REQUIRED_GENERATOR_GUIDES.filter((name) => !names.has(name));
  if (missing.length) {
    return {
      ok: false,
      error: `Skill 参考目录缺少必要规范: ${missing.join(", ")}`,
      files,
    };
  }
  return { ok: true, files };
}

function safeRel(rel) {
  const text = String(rel || "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "")
    .replace(/^(skill-artifacts\/)+/i, "");
  if (!text || text.includes("..")) throw new Error("非法产物路径");
  return text;
}

export function artifactRoot(files, recordingId) {
  return path.join(files.directory(recordingId), "skill-artifacts");
}

function isGeneratorGuideLeak(rel) {
  return safeRel(rel).replace(/\\/g, "/").startsWith("references/generator-guides");
}

export async function writeSkillArtifact(files, recordingId, rel, content) {
  const pathRel = safeRel(rel);
  if (isGeneratorGuideLeak(pathRel)) {
    return {
      saved: false,
      error: "禁止写入 references/generator-guides。那是生成规范，不能进消费者包。",
    };
  }
  const root = artifactRoot(files, recordingId);
  const target = path.join(root, pathRel);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, String(content ?? ""), "utf8");
  return { saved: true, path: pathRel };
}

export async function deleteSkillArtifact(files, recordingId, rel) {
  const pathRel = safeRel(rel);
  const root = artifactRoot(files, recordingId);
  const target = path.join(root, pathRel);
  await rm(target, { recursive: true, force: true });
  return { deleted: true, path: pathRel };
}

async function stripLeakedGeneratorGuides(root) {
  const leaked = path.join(root, "references", "generator-guides");
  await rm(leaked, { recursive: true, force: true });
}

async function stripNestedArtifactRoot(root) {
  await rm(path.join(root, "skill-artifacts"), { recursive: true, force: true });
}

function runPython(args, { cwd, timeoutMs = 20000 } = {}) {
  return new Promise((resolve) => {
    const child = spawn("python", args, {
      cwd: cwd || BACK_ROOT,
      env: { ...process.env, PYTHONPATH: BACK_ROOT },
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      resolve({ ok: false, error: "timeout", stdout, stderr });
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ ok: false, error: error.message, stdout, stderr });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ ok: code === 0, code, stdout, stderr });
    });
  });
}

export async function validateSkillPackage(files, recordingId) {
  const root = artifactRoot(files, recordingId);
  await stripLeakedGeneratorGuides(root);
  await stripNestedArtifactRoot(root);
  const script = [
    "import json,sys",
    "from pathlib import Path",
    "from dano.export.skill_package.validator import validate_skill_package",
    `print(json.dumps(validate_skill_package(Path(r'''${root}'''))))`,
  ].join("; ");
  const ran = await runPython(["-c", script], { cwd: BACK_ROOT });
  if (!ran.ok) {
    return { ok: false, issues: [{ severity: "error", code: "validator_failed", message: ran.stderr || ran.error || ran.stdout }] };
  }
  try {
    return JSON.parse(ran.stdout.trim().split("\n").at(-1) || "{}");
  } catch {
    return { ok: false, issues: [{ severity: "error", code: "validator_parse", message: ran.stdout || ran.stderr }] };
  }
}

export async function runIsolatedScript(files, recordingId, script, args = []) {
  const root = artifactRoot(files, recordingId);
  const rel = safeRel(script);
  const work = path.join(root, ".isolated");
  await rm(work, { recursive: true, force: true });
  await mkdir(work, { recursive: true });
  await cp(root, work, { recursive: true, filter: (src) => !src.includes(`${path.sep}.isolated`) });
  const target = path.join(work, rel);
  const ran = await runPython([target, ...asStringArgs(args)], { cwd: work });
  return {
    ok: ran.ok,
    code: ran.code,
    stdout: String(ran.stdout || "").slice(0, 8000),
    stderr: String(ran.stderr || "").slice(0, 4000),
    error: ran.ok ? "" : (ran.error || ran.stderr || "isolated script failed"),
  };
}

function asStringArgs(args) {
  if (Array.isArray(args)) return args.map((item) => String(item));
  if (args == null) return [];
  return [String(args)];
}

export async function readPageAsset({ evidence, recordingId, url, targetUrl = "" }) {
  const wanted = String(url || "").trim();
  if (!wanted) return { found: false, error: "缺少 url" };
  let parsed;
  try {
    parsed = new URL(wanted);
  } catch {
    return { found: false, error: "url 非法" };
  }
  if (!/^https?:$/i.test(parsed.protocol)) {
    return { found: false, error: "只读 http(s) 同源前端" };
  }
  let origin = "";
  try {
    origin = targetUrl ? new URL(targetUrl).origin : "";
  } catch {
    origin = "";
  }
  if (origin && parsed.origin !== origin) {
    return { found: false, error: "非本场同源" };
  }
  const events = await evidence.files.readEvidence(recordingId);
  const loaded = (Array.isArray(events) ? events : []).some((item) => {
    const href = String(item?.payload?.url || item?.payload?.path || "");
    return href === wanted || href.startsWith(wanted);
  });
  if (!loaded) return { found: false, error: "本场未加载该 URL" };
  try {
    const response = await fetch(wanted, { redirect: "follow" });
    const text = await response.text();
    return {
      found: true,
      url: wanted,
      status: response.status,
      content_type: response.headers.get("content-type") || "",
      text: text.slice(0, 20000),
      truncated: text.length > 20000,
    };
  } catch (error) {
    return { found: false, error: error.message || String(error) };
  }
}
