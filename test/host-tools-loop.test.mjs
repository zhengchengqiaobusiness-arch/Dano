import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GUIDE_NAMES } from "../src/agent/guides.mjs";
import { piFacts } from "../src/agent/pi-session.mjs";
import { lineRef } from "../src/browser/snapshot.mjs";
import { hostTools } from "../src/agent/tools.mjs";
import { closeBrowser, getPage, openBrowser } from "../src/browser/session.mjs";
import { createRecording } from "../src/session.mjs";

const html = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "query-page.html"));

function exactRef(text, name) {
  let inExact = false;
  for (const line of String(text || "").split("\n")) {
    if (line === "goal_exact") {
      inExact = true;
      continue;
    }
    if (inExact && line.startsWith("- ")) {
      if (line.includes(`"${name}"`)) return lineRef(line);
      continue;
    }
    if (inExact) break;
  }
  return "";
}

test("host tools click goal_exact, write three files, and pass structural verify", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-tools-"));
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
  const recording = await createRecording({ start_url: startUrl, goal_text: "点击查询" });
  recording.emit = () => {};
  try {
    await openBrowser({ recordingId: recording.id, url: startUrl });
    const tools = hostTools(recording);
    const facts = await piFacts(tools, { takeSnapshot: true });
    assert.match(String(facts.snapshot?.text || ""), /查询/);
    assert.equal(facts.snapshot.body, undefined);
    assert.equal(facts.snapshot.refs, undefined);
    assert.deepEqual(Object.keys(facts).slice(0, 4), ["goal", "requests", "filled", "clicked"]);
    const snap = { text: facts.snapshot.text };
    const ref = exactRef(snap.text, "查询");
    assert.ok(ref, snap.text);
    const clicked = await tools.browser_act({ action: "click", ref });
    assert.equal(clicked.ok, true);
    assert.match(String(clicked.clicked || ""), /查询/);
    assert.equal(clicked.snapshot?.refs, undefined);
    assert.match(String(clicked.snapshot?.text || ""), /\[ref=/);
    const req = (clicked.requests || []).find((row) => row.path === "/api/items");
    assert.ok(req, JSON.stringify(clicked.requests || []));
    const after = await tools.context();
    assert.deepEqual(Object.keys(after).slice(0, 4), ["goal", "requests", "filled", "clicked"]);
    assert.match(String(after.snapshot?.text || ""), /查询/);
    assert.ok((after.requests || []).some((row) => row.path === "/api/items"));
    assert.equal((after.clicked || []).some((label) => String(label).includes("查询")), true);
    const full = await tools.network_get({ id: req.id });
    assert.equal(full.path, "/api/items");
    for (const name of GUIDE_NAMES) {
      const guide = await tools.read_guide({ name });
      assert.equal(guide.ok, true);
    }
    const written = await tools.write_skill_file({
      relative_path: "SKILL.md",
      contents: "---\nname: list-items\ndescription: 点击查询列出记录\n---\npython scripts/client.py list\n",
    });
    assert.equal(written.ok, true);
    await tools.write_skill_file({
      relative_path: "scripts/client.py",
      contents: "def list_items():\n    return get('/api/items', deptId=deptId)\nprint('{}')\n",
    });
    await tools.write_skill_file({
      relative_path: "references/api.md",
      contents: `GET /api/items\ndeptId\nevidence ${req.id}\n`,
    });
    const frozen = await tools.write_skill_file({ relative_path: "config/runtime.json", contents: "{}" });
    assert.equal(frozen.ok, false);
    const ran = await tools.run_skill_command({ argv: ["python", "scripts/client.py", "list"] });
    assert.equal(ran.ok, true, ran.stderr || ran.stdout);
    const verify = await tools.verify_skill();
    assert.equal(verify.errors.some((item) => item.code === "field_unaccounted"), false);
    assert.deepEqual(verify.errors, []);
    assert.equal(verify.ok, true);
    assert.equal(recording.status, "skill_written_needs_auth");
    assert.equal(recording.finished, true);
  } finally {
    await closeBrowser(recording.id);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("first facts packet shows the tree so the named control is distinct from a nearby search box", async () => {
  const dir = path.dirname(fileURLToPath(import.meta.url));
  const pages = {
    "/": await readFile(path.join(dir, "fixtures", "named-table.html")),
    "/inner-panel.html": await readFile(path.join(dir, "fixtures", "inner-panel.html")),
  };
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-facts-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    const url = (req.url || "/").split("?")[0];
    const body = pages[url] || pages["/"];
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const startUrl = `http://127.0.0.1:${port}/`;
  const recording = await createRecording({
    start_url: startUrl,
    goal_text: "点击组织机构 应填数量的数字部分",
  });
  recording.emit = () => {};
  try {
    await openBrowser({ recordingId: recording.id, url: startUrl });
    await getPage(recording.id).waitForSelector("iframe");
    const tools = hostTools(recording);
    const facts = await piFacts(tools, { takeSnapshot: true });
    const text = String(facts.snapshot?.text || "");
    assert.match(text, /\n {2,}- /);
    assert.equal(text.includes("请输入部门名称") && exactRef(text, "请输入部门名称") !== "", false);
    assert.ok(exactRef(text, "组织机构"), text);
    const numberLine = text.split("\n").find((line) => line.includes('"12"') && line.includes("应填数量"));
    const ref = lineRef(numberLine);
    assert.ok(ref, text);
    const clicked = await tools.browser_act({ action: "click", ref });
    assert.equal(clicked.ok, true);
    assert.match(String(clicked.clicked || ""), /应填数量/);
    assert.equal(String(clicked.clicked || "").includes("请输入部门名称"), false);
  } finally {
    await closeBrowser(recording.id);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("facts packet requests are action-tagged, not the page-load fetches", async () => {
  const html = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "boot-and-query.html"));
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-boot-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    const url = (req.url || "/").split("?")[0];
    if (url.startsWith("/api/")) {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const startUrl = `http://127.0.0.1:${port}/`;
  const recording = await createRecording({ start_url: startUrl, goal_text: "点击查询" });
  recording.emit = () => {};
  try {
    await openBrowser({ recordingId: recording.id, url: startUrl });
    const tools = hostTools(recording);
    const deadline = Date.now() + 3000;
    let boot = false;
    while (Date.now() < deadline) {
      const listed = await tools.network_list({});
      boot = (listed.items || []).some((row) => row.path === "/api/boot");
      if (boot) break;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.equal(boot, true);
    const before = await tools.context();
    assert.equal((before.requests || []).some((row) => row.path === "/api/boot"), false);
    assert.equal((before.index || []).some((row) => String(row.summary || "").includes("/api/boot")), false, JSON.stringify(before.index));
    const facts = await piFacts(tools, { takeSnapshot: true });
    const ref = exactRef(facts.snapshot.text, "查询");
    assert.ok(ref, facts.snapshot.text);
    const clicked = await tools.browser_act({ action: "click", ref });
    assert.equal(clicked.ok, true);
    const after = await tools.context();
    assert.equal((after.requests || []).some((row) => row.path === "/api/items"), true, JSON.stringify(after.requests));
    assert.equal((after.requests || []).some((row) => row.path === "/api/boot"), false, JSON.stringify(after.requests));
    assert.equal((after.requests || []).some((row) => row.path === "/api/poll"), false, JSON.stringify(after.requests));
    assert.equal((after.index || []).some((row) => String(row.summary || "").includes("/api/boot")), false, JSON.stringify(after.index));
    assert.equal((after.index || []).some((row) => String(row.summary || "").includes("/api/poll")), false, JSON.stringify(after.index));
    assert.equal((after.index || []).some((row) => String(row.summary || "").includes("/api/items")), true, JSON.stringify(after.index));
    await new Promise((resolve) => setTimeout(resolve, 700));
    const later = await tools.context();
    assert.equal((later.index || []).some((row) => String(row.summary || "").includes("/api/poll")), false, JSON.stringify(later.index));
    assert.equal((later.requests || []).some((row) => row.path === "/api/items"), true, JSON.stringify(later.requests));
    const listed = await tools.network_list({});
    assert.equal((listed.items || []).some((row) => row.path === "/api/boot"), true);
    assert.equal((listed.items || []).some((row) => row.path === "/api/poll"), true);
  } finally {
    await closeBrowser(recording.id);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});
