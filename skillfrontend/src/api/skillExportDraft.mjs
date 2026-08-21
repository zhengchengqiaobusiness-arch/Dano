export function normalizeSkillExportDraft(row) {
  const src = row && typeof row === "object" ? row : {};
  const examples = Array.isArray(src.exampleRequests)
    ? src.exampleRequests.map((item) => String(item || "").trim()).filter(Boolean).join("\n")
    : typeof src.exampleRequests === "string"
      ? src.exampleRequests
      : "";
  return {
    title: typeof src.title === "string" ? src.title : "",
    description: typeof src.description === "string" ? src.description : "",
    planningMode: src.planningMode === "fixed" ? "fixed" : "dynamic",
    exampleRequests: examples,
    successCriteria: typeof src.successCriteria === "string" ? src.successCriteria : "",
    forbiddenActions: typeof src.forbiddenActions === "string" ? src.forbiddenActions : "",
  };
}

export function serializeSkillExportDraft(draft) {
  return normalizeSkillExportDraft(draft);
}

export function routeSummaryFromOutcome(route) {
  const row = route && typeof route === "object" ? route : {};
  const steps = Array.isArray(row.steps)
    ? row.steps.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const autoCarry = Array.isArray(row.auto_carry)
    ? row.auto_carry.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const askWhen = Array.isArray(row.ask_when)
    ? row.ask_when.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  return {
    name: String(row.name || row.when_to_use || "未命名路线"),
    whenToUse: String(row.when_to_use || ""),
    steps,
    autoCarry,
    askWhen,
    composition: String(row.composition || ""),
    needsConfirm: Boolean(row.needs_confirm),
  };
}
