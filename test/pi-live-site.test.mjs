import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { readPiModelEnv } from "../src/agent/pi-model.mjs";
import { startRecordingPi } from "../src/agent/pi-session.mjs";
import { hostTools } from "../src/agent/tools.mjs";
import { closeBrowser, openBrowser } from "../src/browser/session.mjs";
import { listEvidence, getEvidence } from "../src/evidence/store.mjs";
import { loadStorageState, hasSessionMaterial } from "../src/session-store.mjs";
import { createRecording } from "../src/session.mjs";

const { apiKey, baseUrl, modelId } = readPiModelEnv();
const liveUrl = String(process.env.CABP_LIVE_URL || "").trim();
const liveGoal = String(process.env.CABP_LIVE_GOAL || "").trim();
const live = Boolean(apiKey && baseUrl && modelId && liveUrl && liveGoal);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function actionsOf(recordingId) {
  const actions = [];
  for (const row of await listEvidence(recordingId, { kinds: ["action"], limit: 0 })) {
    const blob = await getEvidence(recordingId, row.id).catch(() => null);
    actions.push({
      summary: row.summary,
      clicked: blob?.body?.clicked || "",
      filled: blob?.body?.filled || [],
      error: blob?.body?.error || "",
    });
  }
  return actions;
}

test("live PI records a three-file skill on the configured site", { skip: !live }, async (t) => {
  t.timeout = 300000;
  const stored = await loadStorageState(liveUrl);
  if (!hasSessionMaterial(stored)) {
    t.skip("no saved session for this origin");
    return;
  }
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-pi-site-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const recording = await createRecording({ start_url: liveUrl, goal_text: liveGoal });
  recording.emit = () => {};
  let pi = null;
  try {
    await openBrowser({ recordingId: recording.id, url: liveUrl, storageState: stored });
    const tools = hostTools(recording);
    const first = await tools.browser_snapshot();
    const text = String(first.text || "");
    if (/登录|验证码/.test(text) && !/查询|提交|新增/.test(text)) {
      t.skip("saved session opened a login wall");
      return;
    }
    pi = await startRecordingPi({ recordingId: recording.id, tools, recording });
    const deadline = Date.now() + 240000;
    while (!recording.finished && Date.now() < deadline) {
      await sleep(1000);
    }
    const actions = await actionsOf(recording.id);
    const filled = actions.flatMap((row) => row.filled);
    assert.equal(
      filled.some((label) => String(label).includes("请输入") && !liveGoal.includes("请输入")),
      false,
      JSON.stringify(actions),
    );
    assert.equal(recording.finished, true, JSON.stringify({
      status: recording.status,
      verify: recording.verify,
      actions,
    }));
    assert.equal(recording.verify?.ok, true, JSON.stringify(recording.verify));
    const skillId = recording.skillId;
    const skillMd = await readFile(path.join(root, "skills", skillId, "SKILL.md"), "utf8");
    const client = await readFile(path.join(root, "skills", skillId, "scripts", "client.py"), "utf8");
    const api = await readFile(path.join(root, "skills", skillId, "references", "api.md"), "utf8");
    assert.match(skillMd, /^---\r?\n/);
    assert.match(skillMd, /python scripts\/client\.py/);
    assert.match(client, /\/[A-Za-z0-9_{}-]+\/[A-Za-z0-9_{}.-]+/);
    assert.match(api, /\/[A-Za-z0-9_{}-]+\/[A-Za-z0-9_{}.-]+/);
  } finally {
    await pi?.dispose?.();
    await closeBrowser(recording.id);
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});
