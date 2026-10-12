import { mkdir, readFile, writeFile, appendFile, cp } from "node:fs/promises";
import path from "node:path";
import { dataRoot, recordingDir, skillDir } from "./paths.mjs";
import { newRecordingId, writeInitialGoal } from "./evidence/store.mjs";
import { skillIdFor, writeRuntimeConfig } from "./skillpack/files.mjs";
import { addLogWriter } from "./log.mjs";

const THOUGHT_LIMIT = 4000;

const recordings = new Map();

export function getRecording(id) {
  return recordings.get(id) || null;
}

export async function createRecording(fields) {
  const id = newRecordingId();
  const subsystem = String(fields.subsystem || "app").replace(/[^A-Za-z0-9_-]/g, "") || "app";
  const recording = {
    id,
    tenant: String(fields.tenant || ""),
    subsystem,
    title: String(fields.title || ""),
    startUrl: String(fields.start_url || ""),
    viewport: fields.viewport || null,
    storageState: fields.storage_state || null,
    status: "recording",
    assistReason: "",
    humanCanClick: true,
    skillDir: "",
    skillId: skillIdFor(subsystem, id),
    verify: null,
    revision: 0,
    paused: false,
    finished: false,
    listeners: new Set(),
    thoughts: [],
  };
  recording.emitThought = (thought) => appendSessionThought(recording, thought);
  attachSessionLog(recording);
  recordings.set(id, recording);
  await writeInitialGoal(id, { page_url: recording.startUrl, goal_text: String(fields.goal_text || "") });
  await persist(recording);
  return recording;
}

function thoughtItem(thought) {
  const item = {
    kind: String(thought?.kind || "text"),
    text: String(thought?.text || ""),
  };
  if (thought?.phase) item.phase = String(thought.phase);
  if (typeof thought?.ok === "boolean") item.ok = thought.ok;
  if (thought?.stream === true) item.stream = true;
  return item;
}

export function appendSessionThought(recording, thought) {
  if (!recording) return;
  const item = thoughtItem(thought);
  if (!item.text && item.kind !== "tool") return;
  const list = Array.isArray(recording.thoughts) ? recording.thoughts : (recording.thoughts = []);
  const last = list[list.length - 1];
  if (
    (item.kind === "thinking" || item.kind === "text")
    && last
    && last.kind === item.kind
    && last.stream
    && item.stream
  ) {
    last.text = String(last.text || "") + item.text;
    schedulePersistThoughts(recording);
    return;
  }
  if (item.kind === "tool" && last?.kind === "tool" && last.phase !== "end") {
    list[list.length - 1] = { ...last, ...item, text: item.text || last.text };
    schedulePersistThoughts(recording);
    return;
  }
  list.push(item);
  if (list.length > THOUGHT_LIMIT) recording.thoughts = list.slice(-THOUGHT_LIMIT);
  schedulePersistThoughts(recording);
}

function schedulePersistThoughts(recording) {
  if (!recording?.id) return;
  clearTimeout(recording.thoughtsTimer);
  recording.thoughtsTimer = setTimeout(() => {
    persistThoughts(recording).catch(() => {});
  }, 200);
  recording.thoughtsTimer.unref?.();
}

export async function persistThoughts(recording) {
  if (!recording?.id) return;
  clearTimeout(recording.thoughtsTimer);
  recording.thoughtsTimer = null;
  const file = path.join(recordingDir(recording.id), "thoughts.json");
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(recording.thoughts || []));
}

async function readThoughts(id) {
  try {
    const raw = JSON.parse(await readFile(path.join(recordingDir(id), "thoughts.json"), "utf8"));
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

function attachSessionLog(recording) {
  if (!recording?.id || recording.detachLog) return;
  const file = path.join(recordingDir(recording.id), "session.log");
  recording.detachLog = addLogWriter((line) => {
    recording.logBuffer = recording.logBuffer || [];
    recording.logBuffer.push(line);
    if (recording.logTimer) return;
    recording.logTimer = setTimeout(() => {
      recording.logTimer = null;
      const chunk = `${(recording.logBuffer || []).join("\n")}\n`;
      recording.logBuffer = [];
      appendFile(file, chunk).catch(() => {});
    }, 100);
    recording.logTimer.unref?.();
  });
}

function bindEmitThought(recording) {
  recording.emitThought = (thought) => appendSessionThought(recording, thought);
}

export function publicSessionStatus(recording) {
  const raw = String(recording?.status || "recording");
  if (raw === "skill_ready" || raw === "skill_written_needs_auth") return "skill_ready";
  if (raw === "failed" || raw === "stopped" || raw === "verify_failed") return "failed";
  return "recording";
}

export function applyVerifyToRecording(recording, verify, skillDirPath = "") {
  recording.verify = verify;
  if (skillDirPath) recording.skillDir = skillDirPath;
  if (verify?.status === "skill_ready" || verify?.status === "skill_written_needs_auth") {
    recording.status = "skill_ready";
    recording.finished = true;
    persistThoughts(recording).catch(() => {});
    return;
  }
  recording.status = "recording";
  recording.finished = false;
}

export function snapshotMessage(recording) {
  recording.revision += 1;
  return {
    type: "snapshot",
    snapshot: {
      run_id: recording.id,
      action: "",
      title: recording.title,
      revision: recording.revision,
      status: publicSessionStatus(recording),
      progress: { step: "capturing", label: "" },
      capture_frozen: false,
      draft: null,
      error: "",
      assist: { reason: recording.assistReason || "" },
      human_can_click: recording.humanCanClick !== false,
      skill_dir: recording.skillDir || "",
      verify: recording.verify,
    },
  };
}

export function emit(recording, message) {
  for (const listener of recording.listeners) listener(message);
}

export async function persist(recording) {
  const file = path.join(recordingDir(recording.id), "state.json");
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify({
    id: recording.id,
    tenant: recording.tenant,
    subsystem: recording.subsystem,
    title: recording.title,
    startUrl: recording.startUrl,
    status: recording.status,
    skillId: recording.skillId,
    skillDir: recording.skillDir,
    verify: recording.verify,
  }, null, 2));
}

export async function loadRecording(id) {
  const live = recordings.get(id);
  if (live) {
    if (!Array.isArray(live.thoughts)) live.thoughts = await readThoughts(id);
    if (!live.emitThought) bindEmitThought(live);
    return live;
  }
  try {
    const raw = JSON.parse(await readFile(path.join(recordingDir(id), "state.json"), "utf8"));
    const status = String(raw.status || "recording");
    const done = status === "skill_ready" || status === "failed" || status === "stopped" || Boolean(raw.finished);
    const recording = {
      ...raw,
      status,
      revision: Number(raw.revision) || 0,
      listeners: new Set(),
      finished: done,
      paused: false,
      humanCanClick: true,
      assistReason: "",
      verify: raw.verify || null,
      thoughts: await readThoughts(id),
    };
    bindEmitThought(recording);
    if (!done) attachSessionLog(recording);
    recordings.set(id, recording);
    return recording;
  } catch {
    return null;
  }
}

export async function exportSkill(id, outDir) {
  const recording = await loadRecording(id);
  if (!recording || publicSessionStatus(recording) !== "skill_ready") {
    return { status: "not_ready", verify: recording?.verify || null };
  }
  const source = recording.skillDir || skillDir(recording.skillId);
  await writeRuntimeConfig(recording, recording.skillId);
  const target = String(outDir || "").trim() || path.join(dataRoot(), "export", recording.skillId);
  const dest = path.resolve(target);
  await cp(source, dest, { recursive: true });
  return { status: "exported", skill_id: recording.skillId, export_path: dest };
}
