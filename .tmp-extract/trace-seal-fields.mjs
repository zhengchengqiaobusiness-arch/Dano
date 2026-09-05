import { createReadStream } from "fs";
import { createInterface } from "readline";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.ts";
import { applyExactEvidenceJoin, applyDeterministicCatalogJudgment } from "../src/inference/pi-skill-runtime.ts";

const path = ".business-skill-studio/recordings/rec_mtorevd3_bbaaf46e/events.jsonl";
const events = [];
const rl = createInterface({ input: createReadStream(path), crlfDelay: Infinity });
for await (const line of rl) {
  if (line.trim()) events.push(JSON.parse(line));
}
const built = buildCapabilityCandidates(events);
console.log("caps", built.map(c => c.id).filter(id => /seal/i.test(id)));
const create = built.find(c => /seal-apply-bill-submit/.test(c.id));
if (!create) {
  console.log("no create");
  process.exit(0);
}
console.log("BUILT ui", create.evidence.filter(e => e.kind === "ui").length);
for (const f of create.inputForm) {
  if (/useType|useMode|isUrgent|documentCount|sealName|company|deptName|expected/.test(f.name)) {
    console.log("built", f.name, f.source, f.label);
  }
}
const joined = applyExactEvidenceJoin(create, events);
for (const f of joined.inputForm) {
  if (/useType|useMode|isUrgent|documentCount|sealName|company|deptName|expected/.test(f.name)) {
    console.log("join", f.name, f.source, f.label, (f.sourceDetail || "").slice(0, 60));
  }
}
