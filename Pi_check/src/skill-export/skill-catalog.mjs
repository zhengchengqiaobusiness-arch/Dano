/**
 * 录制导出后的 Skills 目录。和出包同一份记录，重导覆盖同一条。
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

function catalogPath(files) {
  return path.join(files.root, "skill-catalog.json");
}

function emptyCatalog() {
  return { version: 1, items: [] };
}

export async function loadSkillCatalog(files) {
  try {
    const raw = JSON.parse(await readFile(catalogPath(files), "utf8"));
    const items = Array.isArray(raw?.items) ? raw.items : [];
    return { version: 1, items };
  } catch (error) {
    if (error?.code === "ENOENT") return emptyCatalog();
    throw error;
  }
}

async function saveSkillCatalog(files, catalog) {
  await mkdir(files.root, { recursive: true });
  await writeFile(catalogPath(files), `${JSON.stringify(catalog, null, 2)}\n`, "utf8");
}

export function skillManifestFromExport({
  skillId,
  title,
  description = "",
  tenant = "",
  subsystem,
  action,
  recordingId,
  resultId,
  exportPath,
  version = 1,
  draft,
  frozen = false,
}) {
  const capabilities = Array.isArray(draft?.capabilities) ? draft.capabilities : [];
  const properties = {};
  for (const cap of capabilities) {
    const schema = cap?.input_schema?.properties;
    if (schema && typeof schema === "object") Object.assign(properties, schema);
  }
  return {
    name: skillId,
    skill_id: skillId,
    tenant: tenant || "",
    subsystem: subsystem || "oa",
    action: action || skillId.split(".").slice(1).join(".") || recordingId,
    title: title || skillId,
    description: description || title || "",
    integration: "page",
    risk_level: capabilities.some((cap) => /create|update|delete|submit|write/i.test(String(cap.kind || ""))) ? "L3" : "L1",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    version,
    frozen: Boolean(frozen),
    recording_id: recordingId || "",
    result_id: resultId || recordingId || "",
    export_path: exportPath || "",
    package_dir: exportPath || "",
    source: "pi_check_recording",
    parameters: { type: "object", properties, additionalProperties: true },
  };
}

export async function upsertExportedSkill(files, manifest) {
  const catalog = await loadSkillCatalog(files);
  const name = String(manifest.name || manifest.skill_id || "").trim();
  if (!name) throw new Error("skill_id 不能为空");
  const recordingId = String(manifest.recording_id || "").trim();
  const next = {
    ...manifest,
    name,
    skill_id: name,
    updated_at: new Date().toISOString(),
  };
  const index = catalog.items.findIndex((item) => (
    item.name === name
    || (recordingId && item.recording_id === recordingId)
    || (manifest.result_id && item.result_id === manifest.result_id)
  ));
  if (index >= 0) {
    const prev = catalog.items[index];
    next.created_at = prev.created_at || next.created_at;
    next.version = Number(prev.version || 0) + 1;
    next.frozen = Boolean(prev.frozen);
    catalog.items[index] = { ...prev, ...next };
  } else {
    catalog.items.unshift(next);
  }
  await saveSkillCatalog(files, catalog);
  return catalog.items.find((item) => item.name === name);
}

export async function listExportedSkills(files, { includeFrozen = true } = {}) {
  const catalog = await loadSkillCatalog(files);
  return catalog.items.filter((item) => includeFrozen || !item.frozen);
}

export async function getExportedSkill(files, skillId) {
  const catalog = await loadSkillCatalog(files);
  return catalog.items.find((item) => item.name === skillId || item.skill_id === skillId) || null;
}

export async function getExportedSkillByRecording(files, recordingId) {
  const key = String(recordingId || "").trim();
  if (!key) return null;
  const catalog = await loadSkillCatalog(files);
  return catalog.items.find((item) => item.recording_id === key) || null;
}

export async function setExportedSkillFrozen(files, skillId, frozen) {
  const catalog = await loadSkillCatalog(files);
  const item = catalog.items.find((row) => row.name === skillId || row.skill_id === skillId);
  if (!item) return null;
  item.frozen = Boolean(frozen);
  item.updated_at = new Date().toISOString();
  await saveSkillCatalog(files, catalog);
  return item;
}

export async function removeExportedSkill(files, skillId) {
  const catalog = await loadSkillCatalog(files);
  const before = catalog.items.length;
  catalog.items = catalog.items.filter((item) => item.name !== skillId && item.skill_id !== skillId);
  await saveSkillCatalog(files, catalog);
  return before !== catalog.items.length;
}
