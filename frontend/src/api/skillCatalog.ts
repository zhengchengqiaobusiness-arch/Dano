export const SKILL_CATALOG_CHANGED = "dano:skill-catalog-changed";

export type CatalogOverlayRow = {
  id?: string;
  recording_id?: string;
  skill_id?: string;
  skill_version?: number;
  skill_export_status?: string;
  skill_lifecycle?: string;
  skill_export_path?: string;
  skill_export_title?: string;
  skill_needs_reexport?: boolean;
};

export type CatalogItem = {
  name?: string;
  skill_id?: string;
  recording_id?: string;
  result_id?: string;
  title?: string;
  version?: number;
  export_path?: string;
  frozen?: boolean;
};

export function mergeRecordingHistory<T extends CatalogOverlayRow & { id?: string; action?: string }>(
  gateway: T[] = [],
  piRows: T[] = [],
): T[] {
  const out: T[] = [];
  const seenRec = new Set<string>();
  const seenId = new Set<string>();
  for (const row of piRows) {
    const rec = String(row.recording_id || (String(row.id || "").startsWith("rec_") ? row.id : "")).trim();
    const id = String(row.id || rec || "").trim();
    if (rec) seenRec.add(rec);
    if (id) seenId.add(id);
    out.push({ ...row, recording_id: rec || row.recording_id });
  }
  for (const row of gateway) {
    const rec = String(row.recording_id || "").trim();
    const id = String(row.id || "").trim();
    if (rec && seenRec.has(rec)) continue;
    if (id && seenId.has(id)) continue;
    out.push(row);
  }
  return out;
}

export function applyExportedCatalog<T extends CatalogOverlayRow>(
  row: T,
  items: CatalogItem[] = [],
): T {
  const recordingId = String(row.recording_id || "").trim();
  const resultId = String(row.id || "").trim();
  const skillId = String(row.skill_id || "").trim();
  const hit = items.find((item) => {
    const rec = String(item.recording_id || "").trim();
    const rid = String(item.result_id || "").trim();
    const name = String(item.name || item.skill_id || "").trim();
    return (recordingId && rec === recordingId)
      || (resultId && rid === resultId)
      || (skillId && name === skillId);
  });
  if (!hit) return row;
  return {
    ...row,
    skill_id: String(hit.name || hit.skill_id || row.skill_id || ""),
    skill_version: Number(hit.version || row.skill_version || 1),
    skill_export_status: "exported",
    skill_lifecycle: "exported",
    skill_export_path: String(hit.export_path || row.skill_export_path || ""),
    skill_export_title: String(hit.title || row.skill_export_title || ""),
    skill_needs_reexport: false,
  };
}

export function skillDisplayId(skill: { name: string; action?: string }): string {
  return skill.action?.trim() || skill.name;
}

export function notifySkillCatalogChanged(target: EventTarget = window): void {
  target.dispatchEvent(new Event(SKILL_CATALOG_CHANGED));
}

export function observeSkillCatalogChanges(
  listener: EventListener,
  target: EventTarget = window,
): () => void {
  target.addEventListener(SKILL_CATALOG_CHANGED, listener);
  return () => target.removeEventListener(SKILL_CATALOG_CHANGED, listener);
}
