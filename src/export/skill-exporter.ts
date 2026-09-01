import path from "node:path";
import { chmod, mkdir, writeFile } from "node:fs/promises";
import type { CapabilityContract } from "../domain.js";
import { slugify, writeJson } from "../utils.js";

function markdownEscape(text: string) {
  return text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildSkillMd(skillName: string, caps: CapabilityContract[]) {
  const operations = [...new Set(caps.map(c => c.operation))].join(", ");
  return `---
name: ${skillName}
description: Execute verified business-system capabilities learned from real UI/API evidence. Use when the user asks to query or operate this business system, including ${operations}; route natural-language requests to verified atomic operations and compose them only through approved bindings.
---

# ${skillName}

This package contains only capabilities that passed the evidence gate.

## Deterministic execution process

1. Read \`contracts/capabilities.json\`.
2. Match the user's goal to **verified** atomic capabilities.
3. If multiple capabilities match, the target is unclear, or required inputs are missing, ask the user.
4. Use \`references/forms-and-candidates.md\` to collect inputs.
5. Automatic multi-step chaining is allowed only through \`approved: true\` bindings in the contract.
6. Before create/update/review/delete, obtain explicit user confirmation. Planning is not confirmation.
7. Execute with:
   \`node scripts/execute.mjs --capability <id> --input '<json>' [--confirm-write]\`
8. Evaluate the returned HTTP status against the capability's \`completion.acceptedHttpStatuses\`.
9. If a result is ambiguous or does not satisfy completion criteria, stop and report it. Do not guess.

## Capability index

${caps.map(c => `- **${c.id}** — ${markdownEscape(c.title)} (${c.operation})`).join("\n")}

See:
- [Routing and composition](references/routing-and-composition.md)
- [Forms and candidate rules](references/forms-and-candidates.md)
- [Evidence manifest](references/evidence.md)
`;
}

function buildRouting(caps: CapabilityContract[]) {
  return `# Routing and composition

## Atomic operations

${caps.map(c => `### ${c.id}
- Operation: \`${c.operation}\`
- Business meaning: ${c.description}
- Transport: \`${c.transport.method} ${c.transport.pathTemplate}\`
- Confirmation: ${c.confirmation.required ? "required" : "not required"}
- Completion statuses: ${c.completion.acceptedHttpStatuses.join(", ")}
`).join("\n")}

## Composition rule

Never infer a data binding from field names alone.
Only bindings with \`approved: true\` in \`contracts/capabilities.json\` may be applied automatically.
If the upstream value is missing or produces more than one plausible target, ask the user.
`;
}

function buildForms(caps: CapabilityContract[]) {
  return `# Forms and candidate rules

${caps.map(c => `## ${c.id}

${c.inputForm.length ? c.inputForm.map(f => {
  const candidate = !f.candidates ? "none" :
    f.candidates.type === "static"
      ? `static: ${f.candidates.values.map(v => `${v.label}=${String(v.value)}`).join(", ")}`
      : `dynamic via ${f.candidates.capabilityId} (${f.candidates.valuePath} / ${f.candidates.labelPath})`;
  return `- \`${f.path}\` — ${f.label}; ${f.required ? "required" : "optional"}; widget=${f.widget}; candidates=${candidate}`;
}).join("\n") : "- No form metadata was captured; use the JSON schema and ask for missing required values."}
`).join("\n")}
`;
}

function buildEvidence(caps: CapabilityContract[]) {
  return `# Evidence manifest

This file intentionally contains evidence references, not captured secrets or full response bodies.

${caps.map(c => `## ${c.id}
Validation: **${c.validation.status}**

${c.evidence.map(e => `- ${e.kind} \`${e.eventId}\`, session \`${e.sessionId}\`, ${e.at}${e.status ? `, HTTP ${e.status}` : ""}`).join("\n")}
`).join("\n")}
`;
}

const EXECUTOR = String.raw`#!/usr/bin/env node
import { readFile } from "node:fs/promises";

function getByPath(value, path) {
  if (path === "$") return value;
  const parts = path.replace(/^\$\./, "").split(".").filter(Boolean);
  let current = value;
  for (const part of parts) {
    if (current == null) return undefined;
    current = current[part];
  }
  return current;
}

function arg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}
const confirmWrite = process.argv.includes("--confirm-write");
const capabilityId = arg("--capability");
const input = JSON.parse(arg("--input") || "{}");
if (!capabilityId) throw new Error("--capability is required");

const contract = JSON.parse(await readFile(new URL("../contracts/capabilities.json", import.meta.url), "utf8"));
const cap = contract.capabilities.find(c => c.id === capabilityId);
if (!cap) throw new Error("Unknown capability: " + capabilityId);
if (cap.validation.status !== "verified") throw new Error("Capability is not verified");
if (cap.confirmation.required && !confirmWrite) {
  throw new Error("Write confirmation required. Re-run with --confirm-write only after explicit user confirmation.");
}

const headers = { accept: "application/json", ...JSON.parse(process.env.SKILL_AUTH_HEADERS || "{}") };
const url = new URL(cap.transport.urlTemplate);
const consumed = new Set();

url.pathname = url.pathname.replace(/\{([^}]+)\}/g, (_, key) => {
  if (input[key] === undefined) throw new Error("Missing URL parameter: " + key);
  consumed.add(key);
  return encodeURIComponent(String(input[key]));
});
for (const [key, value] of [...url.searchParams]) {
  const match = value.match(/^\{([^}]+)\}$/);
  if (!match) continue;
  const field = match[1];
  if (input[field] === undefined) url.searchParams.delete(key);
  else {
    consumed.add(field);
    url.searchParams.set(key, String(input[field]));
  }
}

const method = cap.transport.method.toUpperCase();
const init = { method, headers };
if (method === "GET" || method === "HEAD") {
  for (const [key, value] of Object.entries(input)) {
    if (!consumed.has(key) && value !== undefined) url.searchParams.set(key, String(value));
  }
} else {
  headers["content-type"] = "application/json";
  init.body = JSON.stringify(Object.fromEntries(Object.entries(input).filter(([key]) => !consumed.has(key))));
}

const response = await fetch(url, init);
const text = await response.text();
let body = text;
try { body = JSON.parse(text); } catch {}
const assertions = (cap.completion.assertions || []).map(assertion => {
  const actual = getByPath(body, assertion.path);
  const ok = assertion.kind === "exists" ? actual !== undefined
    : assertion.kind === "nonempty" ? actual !== undefined && actual !== null && actual !== "" && (!Array.isArray(actual) || actual.length > 0)
    : Object.is(actual, assertion.value);
  return { ...assertion, actual, ok };
});
const statusOk = cap.completion.acceptedHttpStatuses.includes(response.status);
const ok = statusOk && assertions.every(a => a.ok);
console.log(JSON.stringify({ ok, status: response.status, body, assertions }, null, 2));
if (!ok) process.exitCode = 2;
`;

const CANDIDATES = String.raw`#!/usr/bin/env node
import { readFile } from "node:fs/promises";

function arg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

function extractMany(root, path) {
  const normalized = path.replace(/^\$\.?/, "");
  if (!normalized) return [root];
  const tokens = normalized.split(".").filter(Boolean);
  let values = [root];
  for (const token of tokens) {
    const wildcard = token.endsWith("[*]");
    const key = wildcard ? token.slice(0, -3) : token;
    const next = [];
    for (const value of values) {
      const child = key ? value?.[key] : value;
      if (wildcard) {
        if (Array.isArray(child)) next.push(...child);
      } else if (child !== undefined) next.push(child);
    }
    values = next;
  }
  return values;
}

function authHeaders() {
  return { accept: "application/json", ...JSON.parse(process.env.SKILL_AUTH_HEADERS || "{}") };
}

async function executeQuery(cap, input) {
  if (cap.validation.status !== "verified" || cap.operation !== "query") {
    throw new Error("Dynamic candidate source must be a verified query capability");
  }
  const url = new URL(cap.transport.urlTemplate);
  const consumed = new Set();
  url.pathname = url.pathname.replace(/\{([^}]+)\}/g, (_, key) => {
    if (input[key] === undefined) throw new Error("Missing URL parameter: " + key);
    consumed.add(key);
    return encodeURIComponent(String(input[key]));
  });
  for (const [key, value] of [...url.searchParams]) {
    const match = value.match(/^\{([^}]+)\}$/);
    if (!match) continue;
    const field = match[1];
    if (input[field] === undefined) url.searchParams.delete(key);
    else {
      consumed.add(field);
      url.searchParams.set(key, String(input[field]));
    }
  }

  const method = cap.transport.method.toUpperCase();
  const headers = authHeaders();
  const init = { method, headers };
  if (method === "GET" || method === "HEAD") {
    for (const [key, value] of Object.entries(input)) {
      if (!consumed.has(key) && value !== undefined) url.searchParams.set(key, String(value));
    }
  } else {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(Object.fromEntries(Object.entries(input).filter(([key]) => !consumed.has(key))));
  }
  const response = await fetch(url, init);
  const text = await response.text();
  let body = text;
  try { body = JSON.parse(text); } catch {}
  if (!cap.completion.acceptedHttpStatuses.includes(response.status)) {
    throw new Error("Candidate source failed with HTTP " + response.status);
  }
  return body;
}

const contract = JSON.parse(await readFile(new URL("../contracts/capabilities.json", import.meta.url), "utf8"));
const capId = arg("--capability") || process.argv[2];
const fieldPath = arg("--field");
const input = JSON.parse(arg("--input") || "{}");
if (!capId) throw new Error("--capability is required");
const cap = contract.capabilities.find(c => c.id === capId);
if (!cap) throw new Error("Unknown capability");

const fields = fieldPath ? cap.inputForm.filter(f => f.path === fieldPath) : cap.inputForm;
const output = [];
for (const field of fields) {
  const rule = field.candidates;
  if (!rule) {
    output.push({ path: field.path, candidates: null });
    continue;
  }
  if (rule.type === "static") {
    output.push({ path: field.path, candidates: rule.values, source: "static" });
    continue;
  }
  const source = contract.capabilities.find(c => c.id === rule.capabilityId);
  if (!source) throw new Error("Candidate source capability not found: " + rule.capabilityId);
  const sourceInput = rule.dependsOn?.length
    ? Object.fromEntries(rule.dependsOn.filter(k => input[k] !== undefined).map(k => [k, input[k]]))
    : input;
  const body = await executeQuery(source, sourceInput);
  const values = extractMany(body, rule.valuePath);
  const labels = extractMany(body, rule.labelPath);
  output.push({
    path: field.path,
    source: rule.capabilityId,
    candidates: values.map((value, i) => ({ value, label: String(labels[i] ?? value) }))
  });
}
console.log(JSON.stringify(output, null, 2));
`;


export async function exportSkill(
  outputRoot: string,
  requestedName: string,
  allCapabilities: CapabilityContract[]
) {
  const caps = allCapabilities.filter(c => c.validation.status === "verified");
  if (!caps.length) throw new Error("No verified capabilities to export");

  const skillName = slugify(requestedName).slice(0, 64);
  const dir = path.join(outputRoot, skillName);
  await mkdir(path.join(dir, "contracts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "scripts"), { recursive: true });

  await writeFile(path.join(dir, "SKILL.md"), buildSkillMd(skillName, caps), "utf8");
  await writeFile(path.join(dir, "references", "routing-and-composition.md"), buildRouting(caps), "utf8");
  await writeFile(path.join(dir, "references", "forms-and-candidates.md"), buildForms(caps), "utf8");
  await writeFile(path.join(dir, "references", "evidence.md"), buildEvidence(caps), "utf8");
  await writeJson(path.join(dir, "contracts", "capabilities.json"), {
    schemaVersion: "1.0",
    generatedAt: new Date().toISOString(),
    capabilities: caps
  });
  await writeJson(path.join(dir, "contracts", "capabilities.schema.json"), {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    title: "Business Skill Capability Catalog",
    type: "object",
    required: ["schemaVersion", "capabilities"],
    properties: {
      schemaVersion: { type: "string" },
      generatedAt: { type: "string" },
      capabilities: {
        type: "array",
        items: {
          type: "object",
          required: ["id", "operation", "transport", "inputSchema", "outputSchema", "evidence", "validation", "completion"],
          properties: {
            id: { type: "string" },
            title: { type: "string" },
            description: { type: "string" },
            operation: { enum: ["query", "create", "update", "review", "delete", "unknown"] },
            transport: { type: "object" },
            inputSchema: { type: "object" },
            outputSchema: { type: "object" },
            inputForm: { type: "array" },
            evidence: { type: "array" },
            bindings: { type: "array" },
            validation: { type: "object" },
            completion: { type: "object" }
          }
        }
      }
    }
  });
  await writeJson(path.join(dir, "skill.contract.json"), {
    schemaVersion: "1.0",
    skill: skillName,
    capabilityIds: caps.map(c => c.id),
    routing: {
      ambiguity: "ask-user",
      composition: "approved-bindings-only",
      writes: "explicit-confirmation-required"
    }
  });
  await writeFile(path.join(dir, "scripts", "execute.mjs"), EXECUTOR, "utf8");
  await writeFile(path.join(dir, "scripts", "candidates.mjs"), CANDIDATES, "utf8");
  await chmod(path.join(dir, "scripts", "execute.mjs"), 0o755);
  await chmod(path.join(dir, "scripts", "candidates.mjs"), 0o755);

  return { dir, count: caps.length, skillName };
}
