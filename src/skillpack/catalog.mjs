import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { dataRoot, isInternalDataPath } from "../paths.mjs";
import { readGoal } from "../evidence/store.mjs";

export function catalogTitle(title, goalText, fallback) {
  const named = String(title || "").trim();
  if (named && !/^https?:\/\//.test(named)) return named.slice(0, 40);
  const line = String(goalText || "").split(/\n/).map((item) => item.trim()).find((item) => item && !/^https?:\/\//.test(item));
  return (line || fallback || "未命名").slice(0, 40);
}

function catalogFile() {
  return path.join(dataRoot(), "skill-catalog.json");
}

export async function readCatalog() {
  try {
    const raw = JSON.parse(await readFile(catalogFile(), "utf8"));
    return Array.isArray(raw) ? raw : [];
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    return [];
  }
}

export function catalogItemMatches(row, skillId) {
  const key = String(skillId || "").trim();
  if (!key) return false;
  return row.skill_id === key || row.name === key || row.recording_id === key || row.action === key;
}

export function skillOwnedByTenant(row, tenant) {
  return String(row?.tenant || "") === String(tenant || "");
}

function findCatalogRow(items, skillId, tenant) {
  return items.find((row) => {
    if (!catalogItemMatches(row, skillId)) return false;
    if (tenant === undefined) return true;
    return skillOwnedByTenant(row, tenant);
  }) || null;
}

async function saveCatalog(items) {
  await mkdir(path.dirname(catalogFile()), { recursive: true });
  await writeFile(catalogFile(), `${JSON.stringify(items, null, 2)}\n`, "utf8");
}

export async function upsertCatalog(recording) {
  const items = await readCatalog();
  let goalText = "";
  try {
    goalText = String((await readGoal(recording.id)).goal_text || "");
  } catch {
    goalText = "";
  }
  const now = new Date().toISOString();
  const item = {
    name: recording.skillId,
    skill_id: recording.skillId,
    tenant: recording.tenant || "",
    subsystem: recording.subsystem || "app",
    action: recording.id,
    title: catalogTitle(recording.title, goalText, recording.skillId),
    description: goalText,
    integration: "page",
    risk_level: "L1",
    parameters: { type: "object", properties: {}, additionalProperties: true },
    recording_id: recording.id,
    result_id: recording.id,
    export_path: recording.skillDir,
    source: "playwright_cabp",
    created_at: now,
    updated_at: now,
    version: 1,
    frozen: false,
  };
  const index = items.findIndex((row) => row.skill_id === item.skill_id || row.recording_id === item.recording_id);
  if (index >= 0) {
    const prev = items[index];
    items[index] = {
      ...prev,
      ...item,
      created_at: prev.created_at || now,
      frozen: Boolean(prev.frozen),
      version: Number(prev.version || 1),
      export_path: prev.export_path && prev.export_path !== item.export_path ? prev.export_path : item.export_path,
    };
  } else items.push(item);
  await saveCatalog(items);
  return items[index >= 0 ? index : items.length - 1];
}

export async function patchCatalogItem(skillId, patch, tenant) {
  const items = await readCatalog();
  const item = findCatalogRow(items, skillId, tenant);
  if (!item) return null;
  Object.assign(item, patch || {}, { updated_at: new Date().toISOString() });
  await saveCatalog(items);
  return item;
}

export async function setSkillFrozen(skillId, frozen, tenant) {
  return patchCatalogItem(skillId, { frozen: Boolean(frozen) }, tenant);
}

export async function setSkillExportPath(skillId, exportPath) {
  return patchCatalogItem(skillId, { export_path: String(exportPath || "").trim() });
}

export async function getSkill(skillId, tenant) {
  return findCatalogRow(await readCatalog(), skillId, tenant);
}

export async function removeSkill(skillId, tenant) {
  const items = await readCatalog();
  const found = findCatalogRow(items, skillId, tenant);
  if (!found) return null;
  await saveCatalog(items.filter((row) => row !== found));
  return found;
}

export function exportedFolderToRemove(item) {
  const exportPath = String(item?.export_path || item?.package_dir || "").trim();
  if (!exportPath || isInternalDataPath(exportPath)) return "";
  return exportPath;
}

export function freezeExportFolder(item) {
  if (String(item?.source || "") === "imported") return "";
  return exportedFolderToRemove(item);
}

export async function listSkillsPage(page = 1, pageSize = 0, { tenant, includeFrozen = true } = {}) {
  let items = await readCatalog();
  if (tenant !== undefined) items = items.filter((row) => skillOwnedByTenant(row, tenant));
  if (!includeFrozen) items = items.filter((row) => !row.frozen);
  const size = pageSize > 0 ? pageSize : items.length || 0;
  const start = (Math.max(1, page) - 1) * (size || 1);
  const slice = size ? items.slice(start, start + size) : items;
  return { items: slice, total: items.length, page: Math.max(1, page), page_size: size || items.length };
}
