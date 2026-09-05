import { readFileSync } from "node:fs";
const events = readFileSync(".business-skill-studio/recordings/rec_mtom949t_f8baee12/events.jsonl", "utf8")
  .split(/\n/).filter(Boolean).map(line => JSON.parse(line));
const creates = events.filter(e => e.kind === "network" && /\/oa\/supply\/create/.test(e.request?.url || ""));
for (const event of creates) {
  console.log("CREATE", event.at, event.request.method, event.request.url);
  console.log("status", event.response?.status, "body", JSON.stringify(event.request.body || event.request.postData || event.request.json || event.request).slice(0, 2000));
  console.log("response", JSON.stringify(event.response?.body || event.response).slice(0, 500));
}
const lastDialog = [...events].reverse().find(e => e.kind === "ui" && e.eventType === "snapshot" && e.scope === "dialog");
console.log("\nLAST DIALOG FIELDS");
console.log(JSON.stringify(lastDialog?.formFields || lastDialog?.todoFields || lastDialog, null, 2).slice(0, 4000));
const pageSnap = events.find(e => e.kind === "ui" && e.eventType === "snapshot" && e.scope === "page");
console.log("\nPAGE FILTERS");
console.log(JSON.stringify((pageSnap?.formFields || []).map(f => ({ label: f.label, name: f.name, kind: f.kind })), null, 2));
