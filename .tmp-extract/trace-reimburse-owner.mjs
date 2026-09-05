import { readFileSync } from "node:fs";
import {
  collectUiObservations,
  owningFormEvent,
  relatedUiEvents,
  sameValue,
  findObservation,
  looksPickerField
} from "../src/inference/field-resolver.ts";

const events = readFileSync(".business-skill-studio/recordings/rec_mtonyiai_0490311e/events.jsonl", "utf8")
  .trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
const save = events.find(event => event.request?.url?.includes("/travel-reimburse/save"));
const uiEvents = events.filter(event => event.kind === "ui");
const uiById = new Map(uiEvents.map(item => [item.id, item]));
const sample = save.request.body;
const correlated = uiById.get(save.correlatedUiEvidenceId);
const owner = owningFormEvent(save, uiEvents, sample);
const nearby = relatedUiEvents(save, uiById, sample);
const observations = collectUiObservations(nearby);

console.log("sameValue(0, [])", sameValue(0, []));
console.log("sameValue([], 0)", sameValue([], 0));
console.log("correlated", {
  id: correlated?.id,
  at: correlated?.at,
  text: correlated?.text,
  label: correlated?.label,
  name: correlated?.name,
  form: (correlated?.form || []).map(item => `${item.name}|${item.label}|${item.value}`)
});
console.log("owner", {
  id: owner?.id,
  at: owner?.at,
  text: owner?.text,
  label: owner?.label,
  form: (owner?.form || []).map(item => `${item.name}|${item.label}|${item.value}`)
});
console.log("nearby", nearby.map(item => ({
  id: item.id,
  at: item.at,
  text: item.text,
  label: item.label,
  name: item.name,
  formLabels: (item.form || []).map(field => field.label)
})));
const comments = observations.filter(item => String(item.label || item.name || "").includes("意见") || String(item.name || "").includes("reason"));
console.log("commentObs", comments);
const creatorField = { path: "$.creator", name: "creator", label: "creator", valueType: "string", source: "system", required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "", widget: "text" };
console.log("looksPicker", looksPickerField(creatorField));
console.log("findObservation", findObservation(creatorField, "1", observations, [], sample));
console.log("obs", observations.map(item => ({ name: item.name, label: item.label, value: item.value, type: item.type })));
