import { readFileSync } from "node:fs";
const events = readFileSync(".business-skill-studio/recordings/rec_mtonyiai_0490311e/events.jsonl", "utf8")
  .trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
const write = events.find(event => event.request?.url?.includes("/travel-reimburse/save"));
console.log(JSON.stringify({
  at: write.at,
  ui: write.correlatedUiEvidenceId,
  query: write.request.query,
  body: write.request.body,
  response: write.response?.body
}, null, 2));
const forms = events.filter(event => event.kind === "ui" && event.form?.length).slice(-3);
for (const event of forms) {
  console.log("\nUI", event.label || event.text, event.pageUrl);
  for (const field of event.form) {
    console.log(" ", field.name, field.label, field.value, field.disabled ? "disabled" : "");
  }
}
