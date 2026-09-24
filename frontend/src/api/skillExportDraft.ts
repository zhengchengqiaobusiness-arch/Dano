export interface SkillExportDraft {
  title: string;
}

export interface RouteSummary {
  name: string;
  whenToUse: string;
  steps: string[];
  autoCarry: string[];
  askWhen: string[];
  composition: string;
  needsConfirm: boolean;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
}

export function normalizeSkillExportDraft(row: unknown): SkillExportDraft {
  const src = asRecord(row);
  return {
    title: typeof src.title === "string" ? src.title : "",
  };
}

export function serializeSkillExportDraft(draft: unknown): SkillExportDraft {
  return normalizeSkillExportDraft(draft);
}

export function routeSummaryFromOutcome(route: unknown): RouteSummary {
  const row = asRecord(route);
  return {
    name: String(row.name || row.when_to_use || "未命名路线"),
    whenToUse: String(row.when_to_use || ""),
    steps: asStringList(row.steps),
    autoCarry: asStringList(row.auto_carry),
    askWhen: asStringList(row.ask_when),
    composition: String(row.composition || ""),
    needsConfirm: Boolean(row.needs_confirm),
  };
}
