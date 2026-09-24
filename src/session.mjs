import { mkdir, readFile, writeFile, cp } from "node:fs/promises";
import path from "node:path";
import { dataRoot, recordingDir, skillDir } from "./paths.mjs";
import { newRecordingId, writeInitialGoal } from "./evidence/store.mjs";
import { skillIdFor } from "./skillpack/files.mjs";

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
  };
  recordings.set(id, recording);
  await writeInitialGoal(id, { page_url: recording.startUrl, goal_text: String(fields.goal_text || "") });
  await persist(recording);
  return recording;
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
      status: recording.status,
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
  if (live) return live;
  try {
    const raw = JSON.parse(await readFile(path.join(recordingDir(id), "state.json"), "utf8"));
    recordings.set(id, raw);
    return raw;
  } catch {
    return null;
  }
}

export async function exportSkill(id, outDir) {
  const recording = await loadRecording(id);
  if (!recording || recording.status !== "skill_ready") {
    return { status: "not_ready", verify: recording?.verify || null };
  }
  const source = recording.skillDir || skillDir(recording.skillId);
  const target = String(outDir || "").trim() || path.join(dataRoot(), "export", recording.skillId);
  const dest = path.resolve(target);
  await cp(source, dest, { recursive: true });
  return { status: "exported", skill_id: recording.skillId, export_path: dest };
}
