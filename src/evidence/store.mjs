import { mkdir, readFile, writeFile, appendFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { recordingDir } from "../paths.mjs";

const GOAL_KEYS = [
  "page_url", "goal_text", "capabilities", "modes", "caller_inputs",
  "field_scope", "exclusions", "order", "final_action", "success",
];

function indexFile(recordingId) {
  return path.join(recordingDir(recordingId), "evidence.jsonl");
}

function blobFile(recordingId, id) {
  return path.join(recordingDir(recordingId), "blobs", `${id}.json`);
}

export function newRecordingId() {
  return `rec_${randomUUID().replaceAll("-", "")}`;
}

export async function writeInitialGoal(recordingId, { page_url = "", goal_text = "" } = {}) {
  const goal = Object.fromEntries(GOAL_KEYS.map((key) => [key, ""]));
  goal.page_url = String(page_url || "");
  goal.goal_text = String(goal_text || "");
  const file = path.join(recordingDir(recordingId), "goal.json");
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, `${JSON.stringify(goal, null, 2)}\n`, "utf8");
  return goal;
}

export async function readGoal(recordingId) {
  const file = path.join(recordingDir(recordingId), "goal.json");
  return JSON.parse(await readFile(file, "utf8"));
}

export async function appendGoal(recordingId, key, text) {
  const goal = await readGoal(recordingId);
  if (!Object.hasOwn(goal, key)) return { ok: false, error: "unknown_key" };
  if (String(goal[key] || "") !== "") return { ok: false, error: "already_set" };
  goal[key] = String(text || "");
  await writeFile(path.join(recordingDir(recordingId), "goal.json"), `${JSON.stringify(goal, null, 2)}\n`, "utf8");
  return { ok: true, goal };
}

export async function appendEvidence(recordingId, record) {
  const id = String(record.id || `ev_${randomUUID().replaceAll("-", "")}`);
  const body = record.body === undefined ? null : record.body;
  const hasBody = body !== null && body !== undefined && !(record.body_missing === true && (body === null || body === ""));
  const index = {
    id,
    kind: record.kind,
    at: new Date().toISOString(),
    summary: record.summary || "",
    body_missing: record.body_missing === true || body === null || body === undefined,
    body_bytes: body == null ? 0 : Buffer.byteLength(typeof body === "string" ? body : JSON.stringify(body)),
  };
  if (record.ok !== undefined) index.ok = record.ok;
  if (record.argv) index.argv = record.argv;
  await mkdir(path.dirname(blobFile(recordingId, id)), { recursive: true });
  await writeFile(blobFile(recordingId, id), JSON.stringify({ ...record, id, body }));
  await mkdir(recordingDir(recordingId), { recursive: true });
  await appendFile(indexFile(recordingId), `${JSON.stringify(index)}\n`);
  return { ...index, body };
}

async function readIndex(recordingId) {
  try {
    const text = await readFile(indexFile(recordingId), "utf8");
    return text.split(/\n/).filter(Boolean).map((line) => JSON.parse(line));
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

export async function listEvidence(recordingId, { after_id = "", kinds = null, limit = 30 } = {}) {
  let rows = await readIndex(recordingId);
  if (after_id) {
    const at = rows.findIndex((row) => row.id === after_id);
    rows = at >= 0 ? rows.slice(at + 1) : rows;
  }
  if (Array.isArray(kinds) && kinds.length) rows = rows.filter((row) => kinds.includes(row.kind));
  if (limit) rows = rows.slice(-Number(limit));
  return rows;
}

export async function getEvidence(recordingId, evidenceId) {
  const raw = await readFile(blobFile(recordingId, evidenceId), "utf8");
  return JSON.parse(raw);
}
