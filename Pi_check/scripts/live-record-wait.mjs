/**
 * 真机录制：启动 PI，等到交出能力再停。
 * 不替 PI 编能力，只启动、观察、必要时停录触发定稿。调查顺序以 Skill 为准。
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.PI_CHECK_URL || "http://127.0.0.1:18080";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const targetUrl = process.argv[2] || "http://boot.dianshixinxi.com:90/oa/duty/dutyLeaveApply?billType=duty_leave";
const goal = process.argv[3] || "按 Skill 把该页独立业务动作做成可调用能力。自动控制。只有阻断才请人。目标做完就交能力、出包、定稿。不要等用户说结束。不要锁预览，不要把 JSON 写在对话里。";
const maxWaitMs = Number(process.env.LIVE_RECORD_WAIT_MS || 420000);

async function readJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(text.slice(0, 400) || `HTTP ${response.status}`);
  }
}

async function api(method, pathname, body) {
  const response = await fetch(`${BASE}${pathname}`, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await readJson(response);
  if (!response.ok) {
    throw new Error(payload.error || payload.publicMessage || payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}

async function readDraft(id) {
  try {
    const raw = JSON.parse(await readFile(path.join(ROOT, "data", id, "pi-draft.json"), "utf8"));
    const draft = raw.draft || raw;
    const caps = Array.isArray(draft.capabilities) ? draft.capabilities : [];
    return {
      count: caps.length,
      ids: caps.map((item) => item.capability_id || item.name || item.title).filter(Boolean),
    };
  } catch {
    return { count: 0, ids: [] };
  }
}

async function readEvidenceTail(id) {
  try {
    const lines = String(await readFile(path.join(ROOT, "data", id, "evidence.jsonl"), "utf8"))
      .split(/\r?\n/)
      .filter(Boolean);
    const events = lines.map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    }).filter(Boolean);
    const clicks = events.filter((item) => (
      item.kind === "interaction"
      && item.payload?.actor === "pi"
      && /click|choose|fill|select/i.test(String(item.payload?.kind || ""))
    ));
    const last = clicks.at(-1);
    return {
      total: events.length,
      piActs: clicks.length,
      lastAct: last ? `${last.payload?.kind} ${last.payload?.selector || last.payload?.ref || ""} ${last.payload?.text || ""}`.trim() : "",
    };
  } catch {
    return { total: 0, piActs: 0, lastAct: "" };
  }
}

const started = await api("POST", "/api/recordings", { targetUrl, goal });
const id = started.id || started.session?.id;
if (!id) throw new Error("没有 recording id");
console.log(`started ${id}`);
console.log(`url=${targetUrl}`);

const deadline = Date.now() + maxWaitMs;
let lastNote = "";
let sawReady = false;
let idleAfterDraft = 0;
let lastDraftCount = 0;
let lastPiActs = 0;

while (Date.now() < deadline) {
  const session = await api("GET", `/api/recordings/${id}`).catch((error) => ({ error: error.message }));
  const draft = await readDraft(id);
  const evidence = await readEvidenceTail(id);
  const status = session.status || session.error || "";
  const note = [
    status,
    session.publicMessage || "",
    `draft=${draft.count}`,
    `piActs=${evidence.piActs}`,
    evidence.lastAct ? `last=${evidence.lastAct}` : "",
  ].filter(Boolean).join(" | ");
  if (note !== lastNote) {
    console.log(note);
    lastNote = note;
  }
  if (session.browserStatus === "ready" || session.status === "recording" || session.status === "pi_finalizing") {
    sawReady = true;
  }
  if (session.status === "succeeded" || session.hasFinalResult) {
    const result = await api("GET", `/api/recordings/${id}/result`).catch(() => null);
    const caps = result?.result?.capabilities || result?.capabilities || [];
    console.log(`SUCCEEDED caps=${caps.length}`);
    for (const cap of caps) {
      console.log(`- ${cap.capability_id || cap.name} | ${cap.title || cap.name} | ${cap.kind || ""}`);
    }
    console.log(JSON.stringify({
      ok: caps.length > 0,
      id,
      status: session.status,
      capabilityCount: caps.length,
      ids: caps.map((item) => item.capability_id || item.name),
    }));
    if (!caps.length) process.exitCode = 1;
    process.exit();
  }
  if (session.status === "failed") {
    console.error(`FAILED ${session.publicMessage || session.error || "录制失败"}`);
    process.exitCode = 1;
    process.exit();
  }
  if (draft.count > lastDraftCount || evidence.piActs > lastPiActs) {
    idleAfterDraft = 0;
    lastDraftCount = draft.count;
    lastPiActs = evidence.piActs;
  } else if (sawReady && (draft.count > 0 || evidence.piActs >= 2)) {
    idleAfterDraft += 1;
  }
  if (session.status === "pi_finalizing") {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    continue;
  }
  if (sawReady && draft.count > 0 && Date.now() > deadline - 20000) {
    console.log("等待超时，停录把已有草稿定稿");
    break;
  }
  if (sawReady && evidence.piActs >= 3 && Date.now() > deadline - 20000 && draft.count === 0) {
    console.log("已有真实点击但还没交草稿，停录让 PI 定稿");
    break;
  }
  await new Promise((resolve) => setTimeout(resolve, 2500));
}

console.log("stopping, waiting for PI result");
try {
  const stopped = await api("POST", `/api/recordings/${id}/stop`);
  const caps = stopped.result?.capabilities || [];
  console.log(`status=${stopped.session?.status || stopped.status} capabilities=${caps.length}`);
  for (const cap of caps) {
    console.log(`- ${cap.capability_id || cap.name} | ${cap.title || cap.name} | ${cap.kind || ""}`);
  }
  console.log(JSON.stringify({
    ok: caps.length > 0,
    id,
    capabilityCount: caps.length,
    ids: caps.map((item) => item.capability_id || item.name),
    unresolved: stopped.result?.unresolved || [],
  }));
  if (!caps.length) process.exitCode = 1;
} catch (error) {
  const draft = await readDraft(id);
  console.error(`STOP_FAIL ${error.message}`);
  console.error(`draft=${draft.count} ${draft.ids.join(",")}`);
  process.exitCode = 1;
}
