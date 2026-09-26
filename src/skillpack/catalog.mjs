import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { dataRoot } from "../paths.mjs";
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
  const index = items.findIndex((row) => row.skill_id === item.skill_id);
  if (index >= 0) items[index] = { ...items[index], ...item, created_at: items[index].created_at || now };
  else items.push(item);
  await mkdir(path.dirname(catalogFile()), { recursive: true });
  await writeFile(catalogFile(), `${JSON.stringify(items, null, 2)}\n`, "utf8");
  return item;
}

export async function setSkillFrozen(skillId, frozen) {
  const items = await readCatalog();
  const item = items.find((row) => row.skill_id === skillId || row.name === skillId);
  if (!item) return null;
  item.frozen = Boolean(frozen);
  item.updated_at = new Date().toISOString();
  await writeFile(catalogFile(), `${JSON.stringify(items, null, 2)}\n`, "utf8");
  return item;
}

export async function removeSkill(skillId) {
  const items = await readCatalog();
  const next = items.filter((row) => row.skill_id !== skillId && row.name !== skillId);
  if (next.length === items.length) return null;
  await writeFile(catalogFile(), `${JSON.stringify(next, null, 2)}\n`, "utf8");
  return items.find((row) => row.skill_id === skillId || row.name === skillId);
}

export async function listSkillsPage(page = 1, pageSize = 0) {
  const items = await readCatalog();
  const size = pageSize > 0 ? pageSize : items.length || 0;
  const start = (Math.max(1, page) - 1) * (size || 1);
  const slice = size ? items.slice(start, start + size) : items;
  return { items: slice, total: items.length, page: Math.max(1, page), page_size: size || items.length };
}
