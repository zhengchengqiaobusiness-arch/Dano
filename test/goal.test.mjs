import assert from "node:assert/strict";
import http from "node:http";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { WebSocket } from "ws";
import { createApp, listen } from "../src/server.mjs";
import { setStartRecordingPi } from "../src/agent/pi-session.mjs";
import { readGoal } from "../src/evidence/store.mjs";

test("start keeps the goal text verbatim", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-goal-"));
  const site = http.createServer((_req, res) => {
    res.writeHead(200, { "content-type": "text/html" });
    res.end("<html><body>ok</body></html>");
  });
  await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
  const goal = "请保留这一整段目标，不要收成 query";
  setStartRecordingPi(async () => ({ prompt: async () => {}, dispose() {} }));
  const server = createApp();
  const address = await listen(server, 0);
  const ws = new WebSocket(`ws://127.0.0.1:${address.port}/onboarding/page/record`);
  const messages = [];
  await new Promise((resolve, reject) => {
    ws.on("open", resolve);
    ws.on("error", reject);
  });
  ws.on("message", (raw) => messages.push(JSON.parse(String(raw))));
  ws.send(JSON.stringify({
    type: "start",
    start_url: `http://127.0.0.1:${site.address().port}/`,
    goal_text: goal,
    title: "t",
    tenant: "t",
    subsystem: "app",
  }));
  const deadline = Date.now() + 20000;
  let runId = "";
  while (Date.now() < deadline && !runId) {
    const snap = messages.find((item) => item.snapshot?.run_id);
    if (snap) runId = snap.snapshot.run_id;
    else await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const stored = await readGoal(runId);
  assert.equal(stored.goal_text, goal);
  assert.notEqual(stored.goal_text, "query");
  ws.close();
  await new Promise((resolve) => server.close(resolve));
  await new Promise((resolve) => site.close(resolve));
  setStartRecordingPi(null);
});
