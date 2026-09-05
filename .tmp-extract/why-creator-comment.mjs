import { readFileSync } from "node:fs";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.ts";

const events = readFileSync(".business-skill-studio/recordings/rec_mtonyiai_0490311e/events.jsonl", "utf8")
  .trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
const create = buildCapabilityCandidates(events).find(item => item.id.includes("travel-reimburse-save"));
const creator = create.inputForm.find(field => field.name === "creator");
const tripId = create.inputForm.find(field => field.name === "tripId");
const attachments = create.inputForm.find(field => field.name === "attachments");
console.log("creator", JSON.stringify(creator, null, 2));
console.log("tripId", JSON.stringify(tripId, null, 2));
console.log("attachments", JSON.stringify(attachments, null, 2));
console.log("bindings", JSON.stringify(create.bindings, null, 2));
