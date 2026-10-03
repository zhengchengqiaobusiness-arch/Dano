import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { piFacts } from "../src/agent/pi-session.mjs";
import { hostTools } from "../src/agent/tools.mjs";
import { closeBrowser, openBrowser } from "../src/browser/session.mjs";
import { lineRef } from "../src/browser/snapshot.mjs";
import { createRecording } from "../src/session.mjs";
import { actionEvidenceSummary, clickedLabels, filledLabels } from "../src/skillpack/verify.mjs";

const html = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "overlay-form.html"));

function refOf(text, name, { inside = "" } = {}) {
  const lines = String(text || "").split("\n");
  if (inside) {
    let depth = -1;
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      if (depth < 0 && line.includes(inside)) {
        depth = (line.match(/^\s*/) || [""])[0].length;
        continue;
      }
      if (depth < 0) continue;
      const indent = (line.match(/^\s*/) || [""])[0].length;
      if (line.trim() && indent <= depth) break;
      if (line.includes(`"${name}"`)) return lineRef(line);
    }
  }
  const line = lines.find((row) => row.includes(`"${name}"`) && (row.includes("ref=") || row.includes("[ref=")));
  return lineRef(line);
}

test("clicked labels are facts in the index, not a verdict", () => {
  assert.deepEqual(clickedLabels([
    { kind: "action", summary: 'click:button "查询"' },
    { kind: "action", summary: "click" },
    { kind: "action", summary: 'fill:textbox "标题"' },
  ]), ['button "查询"']);
});

test("a stale action is recorded as an error fact, not a nameless fill", () => {
  assert.equal(actionEvidenceSummary("fill", { ok: false, error: "stale_ref" }), "error:stale_ref");
  assert.equal(actionEvidenceSummary("click", { ok: false, error: "stale_ref" }), "error:stale_ref");
  assert.deepEqual(filledLabels([{ kind: "action", summary: "error:stale_ref" }]), []);
  assert.deepEqual(clickedLabels([{ kind: "action", summary: "error:stale_ref" }]), []);
});

test("overlay tree keeps dialog nesting; fill the named field; submit inside the dialog", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-overlay-"));
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
        res.end(JSON.stringify({ ok: true }));
      });
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
    goal_text: "点击新增，在弹层填写标题为甲，提交",
  });
  recording.emit = () => {};
  try {
    await openBrowser({ recordingId: recording.id, url: startUrl });
    const tools = hostTools(recording);
    const first = await piFacts(tools, { takeSnapshot: true });
    const add = refOf(first.snapshot.text, "新增");
    assert.ok(add, first.snapshot.text);
    const opened = await tools.browser_act({ action: "click", ref: add });
    assert.equal(opened.ok, true);
    assert.match(String(opened.clicked || ""), /新增/);
    const mid = await tools.context();
    assert.match(String(mid.snapshot?.text || ""), /dialog/i);
    const tree = String(opened.snapshot?.text || "");
    assert.match(tree, /dialog/i);
    assert.equal(/\srow="/.test(tree), false);
    const title = refOf(tree, "标题", { inside: "dialog" }) || refOf(tree, "标题");
    const search = refOf(tree, "请输入部门名称");
    assert.ok(title, tree);
    assert.ok(search, tree);
    assert.notEqual(title, search);
    const filled = await tools.browser_act({ action: "fill", ref: title, text: "甲" });
    assert.equal(filled.ok, true);
    assert.equal(String((filled.filled || []).join(" ")).includes("请输入部门名称"), false);
    assert.match(String((filled.filled || []).join(" ")), /标题/);
    const submit = refOf(filled.snapshot?.text || tree, "提交", { inside: "dialog" });
    assert.ok(submit, filled.snapshot?.text || tree);
    const posted = await tools.browser_act({ action: "click", ref: submit });
    assert.equal(posted.ok, true);
    const hit = (posted.requests || []).find((row) => row.path === "/api/items");
    assert.ok(hit, JSON.stringify(posted.requests || []));
    assert.equal((posted.requests || []).some((row) => row.path === "/api/other"), false);
    const facts = await tools.context();
    assert.equal(facts.filled.some((label) => String(label).includes("标题")), true);
    assert.equal(facts.filled.some((label) => String(label).includes("请输入部门名称")), false);
    assert.equal(facts.clicked.some((label) => String(label).includes("新增")), true);
    assert.equal(facts.clicked.some((label) => String(label).includes("提交")), true);
    assert.equal(bodies.some((row) => row.path === "/api/items" && row.body.includes("甲")), true, JSON.stringify(bodies));
    assert.equal(bodies.some((row) => row.path === "/api/other"), false);
  } finally {
    await closeBrowser(recording.id);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});
