import { readFileSync, writeFileSync } from "node:fs";

const catalog = JSON.parse(readFileSync(".business-skill-studio/catalog/capabilities.json", "utf8"));
const target = catalog.find(item => item.id === "create-post-supply-apply-bill-submit");
const events = [];
for (const session of ["rec_mtolm2pc_7fd6f767", "rec_mtolq7b6_afd90cd7"]) {
  const text = readFileSync(`.business-skill-studio/recordings/${session}/events.jsonl`, "utf8");
  for (const line of text.split(/\n/).filter(Boolean)) events.push(JSON.parse(line));
}

const { fieldHasUiEvidence, collectionRowHasUiEvidence, collectUiObservations, requestValueAt, findObservation } = await import("../src/inference/field-resolver.ts");
const { evidenceSample } = await import("../src/inference/field-derivation.ts");

const sample = evidenceSample(target, events);
const uiRefs = target.evidence
  .map(ref => events.find(e => e.id === ref.eventId))
  .filter(e => e?.kind === "ui");
const obs = collectUiObservations(uiRefs);
const uniqueObs = [];
const seen = new Set();
for (const item of obs) {
  const key = `${item.name}|${item.label}|${item.value}|${item.type}|${item.disabled}`;
  if (seen.has(key)) continue;
  seen.add(key);
  uniqueObs.push({ name: item.name, label: item.label, value: item.value, type: item.type, disabled: item.disabled, options: item.options?.slice(0, 5) });
}

const callers = target.inputForm.filter(f => f.source === "caller").map(field => {
  const value = requestValueAt(sample, field.path);
  const ui = fieldHasUiEvidence(field, uiRefs);
  const row = collectionRowHasUiEvidence(field, target.inputForm, uiRefs);
  const cand = field.candidates?.type;
  const backed = value === undefined || ui || row || cand === "capability" || cand === "static";
  return {
    path: field.path,
    name: field.name,
    label: field.label,
    widget: field.widget,
    required: field.required,
    value,
    ui,
    row,
    cand,
    backed,
    sourceDetail: field.sourceDetail,
    defaultRule: field.defaultRule
  };
});

const report = {
  sampleKeys: sample && typeof sample === "object" ? Object.keys(sample) : sample,
  sample,
  failing: callers.filter(item => !item.backed),
  allCaller: callers,
  uniqueObs
};
writeFileSync(".tmp-extract/caller-ui-report.json", JSON.stringify(report, null, 2));
console.log(JSON.stringify({
  failing: report.failing,
  allCaller: report.allCaller,
  uniqueObs
}, null, 2));
