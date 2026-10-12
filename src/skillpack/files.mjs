import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { recordingDir, skillDir } from "../paths.mjs";
import { mergePackedHeaders, readAuthVault, usableAuthHeaders } from "../auth-vault.mjs";
import { persistBrowserSession } from "../browser/session.mjs";
import { latestCredentialHeaders } from "../browser/network.mjs";
import { readTokenRecord, writeAuthLocalFile } from "../token-store.mjs";
import { originFromUrl } from "../session-store.mjs";

const WRITABLE = new Set(["SKILL.md", "scripts/client.py", "references/api.md"]);
const API_TITLES = ["字段", "绑定", "未解决", "已验证读命令"];

function hasAtxTitle(text, title) {
  const escaped = String(title || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?:^|\\n)#{1,6}\\s+${escaped}\\s*(?:\\n|$)`).test(String(text || ""));
}

export function skillIdFor(subsystem, recordingId) {
  const sub = String(subsystem || "app").replace(/[^A-Za-z0-9_-]/g, "") || "app";
  const id = String(recordingId || "").replace(/[^A-Za-z0-9_-]/g, "");
  return `${sub}.${id}`;
}

function handbookStamp(recordingId) {
  return path.join(recordingDir(recordingId), "handbook-sha.json");
}

async function digestFile(file) {
  try {
    const text = await readFile(file);
    return createHash("sha256").update(text).digest("hex");
  } catch {
    return "";
  }
}

export async function rememberHandbook(recordingId, dir) {
  const payload = {
    "SKILL.md": await digestFile(path.join(dir, "SKILL.md")),
    "scripts/client.py": await digestFile(path.join(dir, "scripts", "client.py")),
  };
  await mkdir(recordingDir(recordingId), { recursive: true });
  await writeFile(handbookStamp(recordingId), JSON.stringify(payload));
  return payload;
}

export async function handbookChanged(recordingId, dir) {
  let stamp = {};
  try {
    stamp = JSON.parse(await readFile(handbookStamp(recordingId), "utf8"));
  } catch {
    return false;
  }
  const now = {
    "SKILL.md": await digestFile(path.join(dir, "SKILL.md")),
    "scripts/client.py": await digestFile(path.join(dir, "scripts", "client.py")),
  };
  return now["SKILL.md"] !== stamp["SKILL.md"] || now["scripts/client.py"] !== stamp["scripts/client.py"];
}

export async function writeSkillFile(recordingId, skillId, relativePath, contents) {
  const rel = String(relativePath || "").replaceAll("\\", "/");
  if (!WRITABLE.has(rel) || rel.startsWith("config/")) {
    return { ok: false, error: "frozen_file", writable: [...WRITABLE] };
  }
  const body = String(contents ?? "");
  if (rel === "references/api.md") {
    const missing = API_TITLES.filter((title) => !hasAtxTitle(body, title));
    if (missing.length) {
      return {
        ok: false,
        error: "not_invocable",
        file: rel,
        hint: `references/api.md 标题必须有「${missing.join("」「")}」。不要用接口 path 当标题。`,
      };
    }
  }
  const dir = skillDir(skillId);
  const file = path.join(dir, rel);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, body, "utf8");
  await rememberHandbook(recordingId, dir);
  return { ok: true, path: file };
}

export function authSpawnEnv(authFile) {
  try {
    const packed = JSON.parse(readFileSync(authFile, "utf8"));
    const headers = usableAuthHeaders(packed?.headers);
    if (!Object.keys(headers).length) return {};
    return { DANO_AUTH_HEADERS: JSON.stringify(headers) };
  } catch {
    return {};
  }
}

export async function readSkillFile(skillId, relativePath) {
  const rel = String(relativePath || "").replaceAll("\\", "/");
  if (!WRITABLE.has(rel)) return { ok: false, error: "frozen_file", writable: [...WRITABLE] };
  const text = await readFile(path.join(skillDir(skillId), rel), "utf8");
  return { ok: true, contents: text };
}

export async function writeRuntimeConfig(recording, skillId) {
  const dir = skillDir(skillId);
  const runtime = {
    tenant: String(recording.tenant || ""),
    subsystem: String(recording.subsystem || "app"),
    base_url: originFromUrl(recording.startUrl) || "",
  };
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "config", "runtime.json"), `${JSON.stringify(runtime, null, 2)}\n`, "utf8");
  await persistBrowserSession(recording.id).catch(() => null);
  const vault = await readAuthVault(recording.id);
  const live = latestCredentialHeaders(recording.id);
  let headers = mergePackedHeaders(vault.headers, live);
  if (!Object.keys(headers).length) {
    const token = await readTokenRecord(recording.tenant, recording.subsystem);
    headers = usableAuthHeaders(token.headers);
  }
  await writeAuthLocalFile(dir, headers, vault.credential);
  return { runtime, headers };
}
