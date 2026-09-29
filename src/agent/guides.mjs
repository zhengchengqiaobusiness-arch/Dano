export const GUIDE_NAMES = [
  "skill-contract.md",
  "skill-generator-auth-and-token.md",
  "skill-generator-live-options.md",
  "skill-generator-ask-user-question-guide.md",
  "skill-generator-workflow.md",
];

export function guidesFor(requests, filled) {
  const names = ["skill-contract.md", "skill-generator-auth-and-token.md"];
  const rows = Array.isArray(requests) ? requests : [];
  if (rows.some((row) => row?.option_list || row?.url_string)) names.push("skill-generator-live-options.md");
  const business = new Set(rows.filter((row) => row?.path && !row.issues_credential).map((row) => `${row.method || ""} ${row.path}`));
  if (business.size >= 2) names.push("skill-generator-workflow.md");
  const caller = (Array.isArray(filled) && filled.length > 0)
    || rows.some((row) => (Array.isArray(row?.changed_keys) && row.changed_keys.length) || (Array.isArray(row?.added_keys) && row.added_keys.length));
  if (caller) names.push("skill-generator-ask-user-question-guide.md");
  return names;
}

export function guideBody() {
  return "";
}

export function guideText(_name, fileText) {
  return String(fileText || "");
}
