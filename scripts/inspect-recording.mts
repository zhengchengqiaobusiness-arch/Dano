import { readFile } from "node:fs/promises";
import path from "node:path";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { validateCapability } from "../src/validation/validator.js";
import type { EvidenceEvent } from "../src/domain.js";

const sessionId = process.argv[2] || "rec_mtikfwzv_b27d4710";
const file = path.join(".business-skill-studio", "recordings", sessionId, "events.jsonl");
const events = (await readFile(file, "utf8")).split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line) as EvidenceEvent);
const ui = events.filter(e => e.kind === "ui");
const net = events.filter(e => e.kind === "network");
console.log("session", sessionId, "events", events.length, "ui", ui.length, "net", net.length);
console.log("\n== UI ==");
for (const e of ui) {
  const u = e as any;
  console.log([u.at, u.eventType, u.text || "", u.label || "", u.name || "", u.triggerCandidate ? "TRIGGER" : "", (u.form || []).length + "form"].join(" | "));
}
console.log("\n== NET ==");
for (const e of net) {
  const n = e as any;
  let pathname = n.request?.url;
  try { pathname = new URL(n.request.url).pathname; } catch {}
  console.log([n.at, n.request?.method, pathname, "cause=" + (n.causedByActionId || n.correlatedUiEvidenceId || "-"), n.response?.status, n.correlationType || ""].join(" | "));
}

const caps = buildCapabilityCandidates(events);
console.log("\n== CAPS", caps.length, "==");
for (const cap of caps) {
  const validated = validateCapability(cap, events, caps);
  const fails = validated.validation.checks.filter(c => !c.ok);
  console.log("\n*", validated.validation.status, cap.operation, cap.title, cap.id);
  console.log("  steps", cap.steps.map(s => `${s.role}${s.onDemand ? "/onDemand" : ""} ${s.transport.method} ${s.transport.pathTemplate}`).join(" || "));
  console.log("  fields", cap.inputForm.map(f => `${f.name}:${f.label}:${f.source}/${f.owner}/${f.requiredState}`).join(" | "));
  console.log("  fails", fails.map(f => `${f.name}${f.fieldPath ? "(" + f.fieldPath + ")" : ""}:${f.detail}`).join(" || "));
}
