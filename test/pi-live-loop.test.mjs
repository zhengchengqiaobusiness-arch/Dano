import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readPiModelEnv } from "../src/agent/pi-model.mjs";
import { startRecordingPi } from "../src/agent/pi-session.mjs";
import { hostTools } from "../src/agent/tools.mjs";
import { closeBrowser, openBrowser } from "../src/browser/session.mjs";
import { listEvidence, getEvidence } from "../src/evidence/store.mjs";
import { createRecording } from "../src/session.mjs";

const { apiKey, baseUrl, modelId } = readPiModelEnv();
const live = Boolean(apiKey && baseUrl && modelId);
const html = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "mixed-query.html"));

const overlayHtml = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "overlay-form.html"));

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

test("live PI clicks the named query control and writes a three-file skill", { skip: !live }, async (t) => {
  t.timeout = 300000;
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-pi-live-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    const url = (req.url || "/").split("?")[0];
    if (url.startsWith("/api/")) {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ data: [{ id: 1, name: "甲" }] }));
      return;
    }
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const startUrl = `http://127.0.0.1:${port}/`;
  const recording = await createRecording({
    start_url: startUrl,
    goal_text: "点击查询，列出记录",
  });
  recording.emit = () => {};
  let pi = null;
  try {
    await openBrowser({ recordingId: recording.id, url: startUrl });
    const tools = hostTools(recording);
    pi = await startRecordingPi({ recordingId: recording.id, tools, recording });
    const deadline = Date.now() + 240000;
    while (!recording.finished && Date.now() < deadline) {
      await sleep(1000);
    }
    const actions = await actionsOf(recording.id);
    const clicked = actions.map((row) => row.clicked).filter(Boolean);
    const filled = actions.flatMap((row) => row.filled);
    assert.equal(filled.some((label) => String(label).includes("请输入部门名称")), false, JSON.stringify(actions));
    assert.equal(clicked.some((label) => /周一|"1"|"2"/.test(label)), false, JSON.stringify(clicked));
    assert.equal(clicked.some((label) => String(label).includes("查询")), true, JSON.stringify(clicked));
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
    assert.match(client, /\/api\/items/);
    assert.match(api, /\/api\/items/);
  } finally {
    await pi?.dispose?.();
    await closeBrowser(recording.id);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("live PI fills the dialog field and submits inside the overlay", { skip: !live }, async (t) => {
  t.timeout = 300000;
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-pi-overlay-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const bodies = [];
  const server = createServer((req, res) => {
    const url = (req.url || "/").split("?")[0];
    if (url.startsWith("/api/")) {
      const chunks = [];
      req.on("data", (chunk) => chunks.push(chunk));
      req.on("end", () => {
        bodies.push({ path: url, method: req.method, body: Buffer.concat(chunks).toString("utf8") });
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ ok: true, id: 1 }));
      });
      return;
    }
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(overlayHtml);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const startUrl = `http://127.0.0.1:${port}/`;
  const recording = await createRecording({
    start_url: startUrl,
    goal_text: "点击新增，在弹层填写标题为甲，提交，产出创建记录的 Skill",
  });
  recording.emit = () => {};
  let pi = null;
  try {
    await openBrowser({ recordingId: recording.id, url: startUrl });
    const tools = hostTools(recording);
    pi = await startRecordingPi({ recordingId: recording.id, tools, recording });
    const deadline = Date.now() + 240000;
    while (!recording.finished && Date.now() < deadline) {
      await sleep(1000);
    }
    const actions = await actionsOf(recording.id);
    const clicked = actions.map((row) => row.clicked).filter(Boolean);
    const filled = actions.flatMap((row) => row.filled);
    assert.equal(filled.some((label) => String(label).includes("请输入部门名称")), false, JSON.stringify(actions));
    assert.equal(clicked.some((label) => String(label).includes("新增")), true, JSON.stringify(clicked));
    assert.equal(filled.some((label) => String(label).includes("标题")), true, JSON.stringify(filled));
    assert.equal(bodies.some((row) => row.path === "/api/other"), false, JSON.stringify(bodies));
    assert.equal(recording.finished, true, JSON.stringify({
      status: recording.status,
      verify: recording.verify,
      actions,
      bodies,
    }));
    assert.equal(recording.verify?.ok, true, JSON.stringify(recording.verify));
    const client = await readFile(path.join(root, "skills", recording.skillId, "scripts", "client.py"), "utf8");
    assert.match(client, /\/api\/items/);
  } finally {
    await pi?.dispose?.();
    await closeBrowser(recording.id);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});
