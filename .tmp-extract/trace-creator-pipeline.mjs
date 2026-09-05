import { readFileSync } from "node:fs";
import {
  assignUniqueFromSamples,
  bindByLabelAffinity,
  bindByRecordedOptions,
  bindBySemanticLabel,
  bindByUniqueMatching,
  bindLeftoverFields,
  collectUiObservations,
  finalizeCallerFields,
  owningFormEvent,
  promoteUnboundFillable,
  recordedLists,
  relatedUiEvents,
  requestValueAt,
  resolveFieldOwnership
} from "../src/inference/field-resolver.ts";

const events = readFileSync(".business-skill-studio/recordings/rec_mtonyiai_0490311e/events.jsonl", "utf8")
  .trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
const save = events.find(event => event.request?.url?.includes("/travel-reimburse/save"));
const uiEvents = events.filter(event => event.kind === "ui");
const uiById = new Map(uiEvents.map(item => [item.id, item]));
const sample = save.request.body;
const owner = owningFormEvent(save, uiEvents, sample);
const observations = collectUiObservations(relatedUiEvents(save, uiById, sample));
const lists = recordedLists(events.filter(event => event.kind === "network"));
const inferred = Object.keys(sample).map(name => ({
  path: `$.${name}`,
  name,
  label: name,
  valueType: Array.isArray(sample[name]) ? "array" : typeof sample[name] === "number" ? "integer" : sample[name] === null ? "unknown" : "string",
  source: "system",
  required: false,
  requiredBasis: "not-observed",
  systemHandled: true,
  sourceDetail: "",
  widget: "text"
}));

function show(step, fields) {
  const field = fields.find(item => item.name === "creator");
  console.log(step, field?.source, field?.label, field?.widget, field?.sourceDetail?.slice(0, 40));
}

let fields = inferred.map(field => resolveFieldOwnership(field, requestValueAt(sample, field.path), observations, lists, sample));
show("resolve", fields);
fields = bindBySemanticLabel(fields, observations, sample, lists);
show("semantic", fields);
fields = bindLeftoverFields(fields, observations, sample, lists, owner);
show("leftover", fields);
fields = assignUniqueFromSamples(fields, observations, [sample], lists);
show("unique", fields);
fields = bindByUniqueMatching(fields, observations, sample, lists);
show("uniqueMatch", fields);
fields = bindByLabelAffinity(fields, observations, sample, lists);
show("affinity", fields);
fields = bindByRecordedOptions(fields, observations, sample, lists);
show("options", fields);
fields = promoteUnboundFillable(fields, observations, sample);
show("promote", fields);
fields = finalizeCallerFields(fields, observations, sample, lists, owner);
show("finalize", fields);
console.log("callers", fields.filter(item => item.source === "caller").map(item => `${item.name}:${item.label}`));
