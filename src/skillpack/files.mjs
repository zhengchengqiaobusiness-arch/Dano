import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { recordingDir, skillDir } from "../paths.mjs";
import { usableAuthHeaders, readAuthVault } from "../auth-vault.mjs";
import { readTokenRecord, writeAuthLocalFile } from "../token-store.mjs";
import { originFromUrl } from "../session-store.mjs";

const WRITABLE = new Set(["SKILL.md", "scripts/client.py", "references/api.md"]);

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
  if (!WRITABLE.has(rel) || rel.startsWith("config/")) return { ok: false, error: "frozen_file" };
  const dir = skillDir(skillId);
  const file = path.join(dir, rel);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, String(contents ?? ""), "utf8");
  await rememberHandbook(recordingId, dir);
  return { ok: true, path: file };
}

export async function readSkillFile(skillId, relativePath) {
  const rel = String(relativePath || "").replaceAll("\\", "/");
  if (!WRITABLE.has(rel)) return { ok: false, error: "frozen_file" };
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
  const vault = await readAuthVault(recording.id);
  let headers = usableAuthHeaders(vault.headers);
  if (!Object.keys(headers).length) {
    const token = await readTokenRecord(recording.tenant, recording.subsystem);
    headers = usableAuthHeaders(token.headers);
  }
  await writeAuthLocalFile(dir, headers);
  return { runtime, headers };
}
