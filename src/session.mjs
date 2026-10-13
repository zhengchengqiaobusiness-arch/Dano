import { mkdir, readFile, writeFile, appendFile, cp, rm } from "node:fs/promises";
import path from "node:path";
import { isInternalDataPath, recordingDir, skillDir } from "./paths.mjs";
import { newRecordingId, writeInitialGoal } from "./evidence/store.mjs";
import { skillIdFor, writeRuntimeConfig } from "./skillpack/files.mjs";
import { addLogWriter } from "./log.mjs";
import { persistExportDirectory, resolveExportDirectory } from "./token-store.mjs";
import { readCatalog, setSkillExportPath } from "./skillpack/catalog.mjs";

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
    wroteSkill: false,
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
  if (recording?.finished) return "failed";
  return "recording";
}

export function publicSessionProgress(recording) {
  const status = publicSessionStatus(recording);
  if (status === "skill_ready") return { step: "done", label: "Skill 产出完毕" };
  if (status === "failed") {
    const first = Array.isArray(recording?.verify?.errors) ? recording.verify.errors[0] : null;
    const code = String(first?.code || "").trim();
    return { step: "failed", label: code ? `产出失败 ${code}` : "产出失败" };
  }
  if (Array.isArray(recording?.verify?.errors) && recording.verify.errors.length) {
    return { step: "writing", label: "校验未过，正在改三文件" };
  }
  if (recording?.wroteSkill) return { step: "writing", label: "正在写三文件" };
  return { step: "capturing", label: "" };
}

export function settlePreviewWithoutBrowser(recording, { hasPage = false, hasPi = false } = {}) {
  if (!recording || recording.finished) return false;
  if (hasPage || hasPi) return false;
  if (!recording.wroteSkill && !recording.verify) return false;
  recording.finished = true;
  const verifyStatus = recording.verify?.status;
  recording.status = (verifyStatus === "skill_ready" || verifyStatus === "skill_written_needs_auth")
    ? "skill_ready"
    : "failed";
  return true;
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
      progress: publicSessionProgress(recording),
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
    finished: Boolean(recording.finished),
    wroteSkill: Boolean(recording.wroteSkill),
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
      wroteSkill: Boolean(raw.wroteSkill),
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

export function exportedPackageName(skillMdText, skillId) {
  const yaml = String(skillMdText || "").match(/^---\r?\n([\s\S]*?)\r?\n---/);
  const named = yaml ? String(yaml[1]).match(/^name\s*:\s*["']?([^\s"'\n]+)/m) : null;
  const raw = String(named?.[1] || skillId || "skill").trim();
  return raw.replace(/[\\/]+/g, "-") || "skill";
}

export async function exportSkill(id, outDir) {
  const recording = await loadRecording(id);
  if (!recording || publicSessionStatus(recording) !== "skill_ready") {
    return { status: "not_ready", verify: recording?.verify || null };
  }
  const source = recording.skillDir || skillDir(recording.skillId);
  try {
    await writeRuntimeConfig(recording, recording.skillId);
  } catch {
    // 源包仍复制；runtime 写失败不挡导出
  }
  let handbook = "";
  try {
    handbook = await readFile(path.join(source, "SKILL.md"), "utf8");
  } catch {
    handbook = "";
  }
  const root = await resolveExportDirectory(outDir);
  const dest = path.resolve(root, exportedPackageName(handbook, recording.skillId));
  if (isInternalDataPath(dest)) {
    return { status: "export_failed", errors: ["export_dir_internal"], skill_id: recording.skillId };
  }
  if (path.resolve(source) !== dest) {
    await mkdir(path.dirname(dest), { recursive: true });
    await rm(dest, { recursive: true, force: true });
    await cp(source, dest, { recursive: true });
  }
  await persistExportDirectory(root);
  await setSkillExportPath(recording.skillId, dest);
  return { status: "exported", skill_id: recording.skillId, export_path: dest, out_dir: root };
}

export async function exportCatalogSkills(outDir, { tenant = "" } = {}) {
  const root = await resolveExportDirectory(outDir);
  await persistExportDirectory(root);
  const wanted = String(tenant || "").trim();
  const written = [];
  const errors = [];
  for (const row of await readCatalog()) {
    if (row.frozen) continue;
    if (wanted && String(row.tenant || "") !== wanted) continue;
    const recordingId = String(row.recording_id || "").trim();
    const label = row.title || row.name || recordingId;
    if (!recordingId.startsWith("rec_")) {
      errors.push(`${label}: 缺少 recording_id`);
      continue;
    }
    const outcome = await exportSkill(recordingId, root);
    if (outcome.status === "exported") written.push(outcome.export_path);
    else errors.push(`${label}: ${outcome.status || "export_failed"}`);
  }
  return {
    out_dir: root,
    mode: "dump",
    count: written.length,
    written,
    errors,
  };
}
