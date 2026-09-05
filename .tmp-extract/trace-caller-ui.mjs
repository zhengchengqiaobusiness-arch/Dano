import { createReadStream, readFileSync } from "fs";
import { createInterface } from "readline";
import { fieldHasUiEvidence } from "../src/inference/field-resolver.ts";

const events = [];
const rl = createInterface({
  input: createReadStream(".business-skill-studio/recordings/rec_mtorevd3_bbaaf46e/events.jsonl"),
  crlfDelay: Infinity
});
for await (const line of rl) {
  if (line.trim()) events.push(JSON.parse(line));
}
const byId = new Map(events.map(event => [event.id, event]));
const caps = JSON.parse(readFileSync(".business-skill-studio/catalog/capabilities.json", "utf8").replace(/^\uFEFF/, ""));
const cap = caps.find(item => item.id === "create-post-seal-apply-bill-submit");
const uiRefs = cap.evidence.map(ref => byId.get(ref.eventId)).filter(event => event?.kind === "ui");
console.log("ui evidence", uiRefs.length, uiRefs.map(event => `${event.eventType}:${event.label || event.name || ""}:${(event.form || []).length}`));
for (const field of cap.inputForm.filter(item => item.source === "caller")) {
  console.log(fieldHasUiEvidence(field, uiRefs) ? "ok" : "FAIL", field.path, field.label, field.name, field.widget);
}
