import { readFileSync, writeFileSync } from "node:fs";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.ts";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.ts";

const events = readFileSync(".business-skill-studio/recordings/rec_mtopd8ei_8012d6d3/events.jsonl", "utf8")
  .trim()
  .split(/\n/)
  .map(line => JSON.parse(line));
const raw = buildCapabilityCandidates(events);
const create = raw.find(item => item.transport.pathTemplate.includes("/oa/car/create"));
const query = raw.find(item => item.transport.pathTemplate.endsWith("/oa/car/page"));
const fin = finalizeCapabilities(raw, events);
const finalized = fin.find(item => item.transport.pathTemplate.includes("/oa/car/create"));

function dump(cap) {
  return (cap?.inputForm || []).map(field => ({
    source: field.source,
    name: field.name,
    label: field.label,
    defaultRule: field.defaultRule,
    detail: String(field.sourceDetail || "").slice(0, 80),
    candidates: field.candidates?.type
  }));
}

const out = {
  rawQuery: dump(query),
  rawCreate: dump(create),
  finalizedCreate: dump(finalized)
};
writeFileSync(".tmp-extract/preview-car.json", JSON.stringify(out, null, 2));
console.log("wrote", out.rawCreate.length, out.finalizedCreate.length);
