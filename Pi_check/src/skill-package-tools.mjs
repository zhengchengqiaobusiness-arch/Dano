/**
 * Skill 4 运输：写产物、隔离运行。校验在 skill-export/validator.mjs。不认业务，不引用 back。
 */

import { mkdir, writeFile, cp, rm, readFile } from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { validateSkillPackageDir } from "./skill-export/validator.mjs";

const ARTIFACT_TOP = new Set(["SKILL.md", "config", "scripts", "references"]);
const FROZEN_RELS = new Set([
  "scripts/client.py",
  "scripts/wire_format.py",
  "scripts/runtime.py",
  "scripts/flow.py",
  "scripts/format_list.py",
  "references/CONTRACT.json",
  "references/INPUT_FORMS.md",
  "references/CAPABILITIES.md",
  "references/OPTIONS.md",
  "config/auth.local.json",
  "config/runtime.json",
]);

function safeRel(rel) {
  const text = String(rel || "").replace(/\\/g, "/").replace(/^\/+/, "");
  if (!text || text.includes("..")) throw new Error("非法产物路径");
  const top = text.split("/")[0];
  if (!ARTIFACT_TOP.has(top)) {
    throw new Error(`只写包根约定路径，禁止另开子包: ${text}`);
  }
  if (/\/SKILL\.md$/i.test(text)) {
    throw new Error(`禁止在子目录再写 SKILL.md: ${text}`);
  }
  return text;
}

export function artifactRoot(files, recordingId) {
  return path.join(files.directory(recordingId), "skill-artifacts");
}

export async function writeSkillArtifact(files, recordingId, rel, content) {
  const root = artifactRoot(files, recordingId);
  const normalized = safeRel(rel);
  if (FROZEN_RELS.has(normalized)) {
    throw new Error(`冻结文件由运输层按合同物化，禁止重写: ${normalized}`);
  }
  const target = path.join(root, normalized);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, String(content ?? ""), "utf8");
  return { saved: true, path: normalized };
}

export async function readSkillArtifact(files, recordingId, rel) {
  const normalized = safeRel(rel);
  const target = path.join(artifactRoot(files, recordingId), normalized);
  return { path: normalized, content: await readFile(target, "utf8") };
}

function runPython(args, { cwd, timeoutMs = 20000 } = {}) {
  return new Promise((resolve) => {
    const child = spawn("python", args, {
      cwd: cwd || process.cwd(),
      env: { ...process.env },
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

export async function validateSkillPackage(files, recordingId, { draft } = {}) {
  const saved = draft || (await files.readDraft?.(recordingId).catch(() => null))?.draft || null;
  return validateSkillPackageDir(artifactRoot(files, recordingId), { sourceDraft: saved });
}

export async function runIsolatedScript(files, recordingId, script, args = []) {
  const root = artifactRoot(files, recordingId);
  const rel = safeRel(script);
  const work = path.join(files.directory(recordingId), `.isolated-run-${randomBytes(4).toString("hex")}`);
  await rm(work, { recursive: true, force: true });
  await mkdir(work, { recursive: true });
  await cp(root, work, { recursive: true });
  const target = path.join(work, rel);
  try {
    const ran = await runPython([target, ...asStringArgs(args)], { cwd: work });
    return {
      ok: ran.ok,
      code: ran.code,
      stdout: String(ran.stdout || "").slice(0, 8000),
      stderr: String(ran.stderr || "").slice(0, 4000),
      error: ran.ok ? "" : (ran.error || ran.stderr || "isolated script failed"),
    };
  } finally {
    await rm(work, { recursive: true, force: true }).catch(() => {});
  }
}

export function asStringArgs(args) {
  if (args == null) return [];
  const items = Array.isArray(args) ? args : [args];
  const out = [];
  for (const item of items) {
    if (item == null) continue;
    if (typeof item === "string" || typeof item === "number") {
      out.push(String(item));
      continue;
    }
    if (typeof item === "object") {
      for (const [key, value] of Object.entries(item)) {
        if (value === false || value == null) continue;
        if (key === "args" || key === "arg") {
          out.push(...asStringArgs(value));
          continue;
        }
        out.push(key);
        if (value !== true) out.push(String(value));
      }
      continue;
    }
    out.push(String(item));
  }
  return out;
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
