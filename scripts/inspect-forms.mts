import { readFile } from "node:fs/promises";
import path from "node:path";

const sessionId = process.argv[2] || "rec_mtikfwzv_b27d4710";
const file = path.join(".business-skill-studio", "recordings", sessionId, "events.jsonl");
const events = (await readFile(file, "utf8")).split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
for (const e of events.filter((x: any) => x.kind === "ui" && (x.triggerCandidate || x.text === "搜索" || x.text === "新增" || String(x.text || "").includes("确")))) {
  console.log("\n====", e.id, e.eventType, e.text, e.label, "form", (e.form || []).length);
  for (const f of e.form || []) {
    console.log(" ", JSON.stringify({ name: f.name, label: f.label, type: f.type, value: f.value, actual: f.actualValue, display: f.displayValue, required: f.required, disabled: f.disabled }));
  }
}
const create = events.find((x: any) => x.kind === "network" && String(x.request?.url || "").includes("/purchase-order/create"));
console.log("\n==== CREATE BODY ====");
console.log(JSON.stringify(create?.request?.body, null, 2));
const query = events.find((x: any) => x.kind === "network" && x.causedByActionId && String(x.request?.url || "").includes("/purchase-order/page"));
console.log("\n==== QUERY ====");
console.log(query?.id, query?.request?.url, JSON.stringify(query?.request?.query));
