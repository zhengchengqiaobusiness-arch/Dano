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
import { GUIDE_NAMES } from "../src/agent/guides.mjs";

const fixtureDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

test("fake pi writes the skill and leaves it unchanged", { timeout: 60000 }, async () => {
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
  let writeFeedback = null;
  let strictFeedback = null;
  setStartRecordingPi(async ({ tools, recording }) => {
    const fake = {
      async prompt() {
        const snap = await tools.browser_snapshot();
        const inner = snap.text.split(/\n/).filter((line) => /\[ref=f1:/.test(line) && /button/i.test(line));
        const ref = (inner[1].match(/\[ref=(f1:[^\]]+)\]/) || inner[1].match(/ref=(f1:e\d+@\d+)/))[1];
        const acted = await tools.browser_act({ action: "click", ref });
        const askName = (row) => {
          const raw = String(row?.name || row?.column || "").trim();
          const quoted = [...raw.matchAll(/"((?:\\.|[^"])*)"/g)].map((item) => item[1]).find((item) => item.length >= 2);
          const label = quoted || raw.replace(/^(textbox|searchbox|combobox|spinbutton|checkbox|radio|slider|listbox|generic|button)\s+/i, "").trim();
          return label.length >= 2 ? label : "";
        };
        const overlayNames = [];
        for (const row of acted.overlay_editable || snap.overlay_editable || []) {
          const name = askName(row);
          if (name && !overlayNames.includes(name)) overlayNames.push(name);
        }
        if (!overlayNames.length) overlayNames.push("page");
        const questions = overlayNames.map((name, index) => {
          const row = (acted.overlay_editable || snap.overlay_editable || []).find((item) => askName(item) === name) || {};
          const choice = /^(combobox|listbox|radio|checkbox)$/i.test(String(row.role || ""));
          return choice
            ? { id: `f${index}`, question: name, inputType: "radio", required: true, options: [{ id: "opt", label: name }], default: "opt" }
            : { id: `f${index}`, question: name, inputType: "text", required: true, default: " " };
        });
        const fieldRows = overlayNames.map((name) => `- page_name: ${name}\n- source: caller\n- control: textbox`).join("\n\n");
        const started = Date.now();
        let hit = null;
        while (Date.now() - started < 3000) {
          const list = await tools.network_list({});
          hit = list.items.find((row) => row.method === "POST" && row.path === "/api/save");
          if (hit) break;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        const full = await tools.network_get({ id: hit.id });
        for (const name of GUIDE_NAMES) await tools.read_guide({ name });
        written = `---\nname: sample-list\ndescription: 调用方列出并保存\n---\n${JSON.stringify({ title: "列出", questions })}\npython scripts/client.py list\n`;
        const api = [
          "## 字段",
          fieldRows,
          `- evidence_ids: ${snap.evidence_id},${full.id}`,
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
        // 字段段只有 path 子标题、没有 page_name/source 行：写入成功，但结构项当场摆回来，不用等 verify_skill
        const loose = await tools.write_skill_file({ relative_path: "references/api.md", contents: "# 字段\n\n### GET /api/save\n响应 data[]\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n" });
        writeFeedback = loose;
        const strict = await tools.write_skill_file({ relative_path: "references/api.md", contents: api });
        strictFeedback = strict;
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
    const done = messages.find((item) => item.snapshot?.status === "skill_ready");
    if (done) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.ok(messages.some((item) => item.type === "frame" && item.data));
  const snap = messages.find((item) => item.type === "snapshot");
  assert.match(snap.snapshot.run_id, /^rec_/);
  const finalSnap = [...messages].reverse().find((item) => item.snapshot?.status === "skill_ready");
  assert.ok(finalSnap);
  const file = await readFile(path.join(skillDir(`app.${snap.snapshot.run_id}`), "SKILL.md"), "utf8");
  assert.equal(file, written);
  assert.equal(saves, 1);
  assert.equal(writeFeedback?.ok, true);
  assert.ok(Array.isArray(writeFeedback?.structure_errors) && writeFeedback.structure_errors.length > 0);
  assert.ok(writeFeedback.structure_errors.every((item) => item.code !== "command_not_run"));
  assert.ok(writeFeedback.structure_errors.some((item) => String(item.hint || "").includes("page_name")));
  assert.equal(strictFeedback?.ok, true);
  assert.equal(strictFeedback?.structure_errors, undefined);
  const source = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "agent", "pi-session.mjs"), "utf8");
  assert.match(source, /createAgentSession/);
  assert.match(source, /session\.prompt/);
  } finally {
    try {
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "abort" }));
    } catch {
      // 套接字已关
    }
    ws?.close();
    await new Promise((resolve) => server.close(resolve));
    await new Promise((resolve) => site.close(resolve));
    setStartRecordingPi(null);
  }
});
