import assert from "node:assert/strict";
import http from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { WebSocket } from "ws";
import { fileURLToPath } from "node:url";
import { createApp, listen } from "../src/server.mjs";
import { setStartRecordingPi } from "../src/agent/pi-session.mjs";
import { skillDir } from "../src/paths.mjs";

const fixtureDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

test("fake pi writes the skill and leaves it unchanged", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-api-"));
  let saves = 0;
  const site = http.createServer(async (req, res) => {
    if (req.method === "POST" && req.url === "/api/save") {
      saves += 1;
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
      return;
    }
    const name = req.url === "/inner.html" ? "inner.html" : "iframe-form.html";
    res.writeHead(200, { "content-type": "text/html" });
    res.end(await readFile(path.join(fixtureDir, name)));
  });
  await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
  const startUrl = `http://127.0.0.1:${site.address().port}/`;
  let written = "";
  setStartRecordingPi(async ({ tools, recording }) => {
    const fake = {
      async prompt() {
        const snap = await tools.browser_snapshot();
        const inner = snap.text.split(/\n/).filter((line) => line.includes("ref=f1:") && line.includes("button"));
        const ref = inner[1].match(/ref=(f1:e\d+)/)[1];
        await tools.browser_act({ action: "click", ref });
        const started = Date.now();
        let hit = null;
        while (Date.now() - started < 3000) {
          const list = await tools.network_list({});
          hit = list.items.find((row) => row.method === "POST" && row.path === "/api/save");
          if (hit) break;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        const full = await tools.network_get({ id: hit.id });
        written = "保留整段目标\npython scripts/client.py list\n";
        const api = [
          "## 字段",
          "- page_name: page",
          "- caller_name: list",
          "- request_path: /api/save",
          "- data_type: object",
          "- control: button",
          "- source: caller",
          "- required_kind: page",
          "- format:",
          "- group:",
          `- evidence_ids: ${snap.evidence_id},${full.id}`,
          "- constant_reason:",
          "",
          "## 绑定",
          "- from: caller",
          "- to: /api/save",
          "- locate: body",
          "- transform: none",
          "- unique: yes",
          "- on_mismatch: stop",
          `- evidence_ids: ${snap.evidence_id}`,
          "",
          "## 未解决",
          "",
          "## 已验证读命令",
          "python scripts/client.py list",
          "",
        ].join("\n");
        await tools.write_skill_file({ relative_path: "SKILL.md", contents: written });
        await tools.write_skill_file({ relative_path: "scripts/client.py", contents: "def list_items():\n    print('ok')\n" });
        await tools.write_skill_file({ relative_path: "references/api.md", contents: api });
        await tools.run_skill_command({ argv: ["python", "scripts/client.py", "list"] });
        await tools.verify_skill();
      },
      dispose() {},
    };
    await fake.prompt();
    return fake;
  });
  const server = createApp();
  const address = await listen(server, 0);
  let ws;
  try {
  const messages = [];
  ws = new WebSocket(`ws://127.0.0.1:${address.port}/onboarding/page/record`);
  await new Promise((resolve, reject) => {
    ws.on("open", resolve);
    ws.on("error", reject);
  });
  ws.on("message", (raw) => messages.push(JSON.parse(String(raw))));
  ws.send(JSON.stringify({
    type: "start",
    start_url: startUrl,
    goal_text: "保留整段目标",
    title: "t",
    tenant: "tenant-a",
    subsystem: "app",
  }));
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const done = messages.find((item) => item.snapshot?.status === "skill_written_needs_auth");
    if (done) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.ok(messages.some((item) => item.type === "frame" && item.data));
  const snap = messages.find((item) => item.type === "snapshot");
  assert.match(snap.snapshot.run_id, /^rec_/);
  const finalSnap = [...messages].reverse().find((item) => item.snapshot?.status === "skill_written_needs_auth");
  assert.ok(finalSnap);
  const file = await readFile(path.join(skillDir(`app.${snap.snapshot.run_id}`), "SKILL.md"), "utf8");
  assert.equal(file, written);
  assert.equal(saves, 1);
  const source = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "agent", "pi-session.mjs"), "utf8");
  assert.match(source, /createAgentSession/);
  assert.match(source, /session\.prompt/);
  } finally {
    ws?.close();
    await new Promise((resolve) => server.close(resolve));
    await new Promise((resolve) => site.close(resolve));
    setStartRecordingPi(null);
  }
});
