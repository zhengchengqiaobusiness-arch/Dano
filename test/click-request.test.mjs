import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GUIDE_NAMES } from "../src/agent/guides.mjs";
import { appendEvidence, writeInitialGoal } from "../src/evidence/store.mjs";
import { closeBrowser, getPage, openBrowser } from "../src/browser/session.mjs";
import { lineRef, takeSnapshot } from "../src/browser/snapshot.mjs";
import { runAction } from "../src/browser/actions.mjs";
import { requestKeyIndex } from "../src/browser/network.mjs";
import { skillDir } from "../src/paths.mjs";
import { verifySkill } from "../src/skillpack/verify.mjs";

const html = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "query-page.html"));

function refOf(text, name) {
  const line = String(text || "").split("\n").find((row) => row.includes(`"${name}"`) && (row.includes("ref=") || row.includes("[ref=")));
  return lineRef(line);
}

async function withQueryPage(run) {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-query-"));
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
  const recordingId = `rec_query_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: "点击查询",
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    return await run(recordingId, root);
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
}

test("a click that fires fetch returns that request on the same action", async () => {
  await withQueryPage(async (recordingId) => {
    const snapshot = await takeSnapshot(recordingId);
    const ref = refOf(snapshot.text, "查询");
    assert.ok(ref, snapshot.text);
    const result = await runAction(recordingId, { action: "click", ref });
    assert.equal(result.ok, true);
    assert.match(String(result.clicked || ""), /查询/);
    const hit = (result.requests || []).find((row) => row.path === "/api/items");
    assert.ok(hit, JSON.stringify(result.requests || []));
    assert.ok((hit.keys || []).includes("deptId"));
  });
});

test("requests from that click are enough for a three-file skill to pass structural verify", async () => {
  await withQueryPage(async (recordingId) => {
    const snapshot = await takeSnapshot(recordingId);
    const result = await runAction(recordingId, { action: "click", ref: refOf(snapshot.text, "查询") });
    assert.equal(result.ok, true);
    const rows = requestKeyIndex(recordingId).filter((row) => row.path === "/api/items");
    assert.equal(rows.length, 1);
    const skillId = "app.rec_query_skill";
    const dir = skillDir(skillId);
    for (const name of GUIDE_NAMES) {
      await appendEvidence(recordingId, { kind: "guide", summary: name, body: name, body_missing: false });
    }
    await appendEvidence(recordingId, {
      kind: "verify",
      ok: true,
      argv: ["python", "scripts/client.py", "list"],
      summary: "python scripts/client.py list",
      stdout: "{}",
      stderr: "",
      body: "{}",
    });
    await mkdir(path.join(dir, "scripts"), { recursive: true });
    await mkdir(path.join(dir, "references"), { recursive: true });
    await mkdir(path.join(dir, "config"), { recursive: true });
    await writeFile(path.join(dir, "SKILL.md"), "---\nname: list-items\ndescription: 点击查询列出记录\n---\npython scripts/client.py list\n");
    await writeFile(path.join(dir, "scripts/client.py"), "def list_items():\n    return get('/api/items', deptId=deptId)\n");
    await writeFile(path.join(dir, "references/api.md"), `GET /api/items\ndeptId\nevidence ${rows[0].id}\n`);
    await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
    await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{}}\n");
    const verify = await verifySkill(dir, recordingId, { skillId });
    assert.equal(verify.errors.some((item) => item.code === "field_unaccounted"), false);
    assert.deepEqual(verify.errors, []);
    assert.equal(verify.ok, true);
  });
});

test("a click inside an iframe still captures the parent fetch", async () => {
  const page = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "iframe-parent-fetch.html"));
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-iframe-fetch-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    const url = (req.url || "/").split("?")[0];
    if (url.startsWith("/api/")) {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ data: [] }));
      return;
    }
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(page);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const recordingId = `rec_iframe_fetch_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: "点击提交",
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    const pageHandle = getPage(recordingId);
    await pageHandle.waitForSelector("iframe");
    await pageHandle.frameLocator("iframe").getByRole("button", { name: "提交" }).waitFor();
    const snapshot = await takeSnapshot(recordingId);
    const ref = refOf(snapshot.text, "提交");
    assert.ok(ref, snapshot.text);
    const result = await runAction(recordingId, { action: "click", ref });
    assert.equal(result.ok, true);
    assert.match(String(result.clicked || ""), /提交/);
    const hit = (result.requests || []).find((row) => row.path === "/api/items");
    assert.ok(hit, JSON.stringify(result.requests || []));
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("clicking a column header still returns that column's cells when the click also fetches", async () => {
  const page = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "sort-header.html"));
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-sort-header-"));
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
    res.end(page);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const recordingId = `rec_sort_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: "点击 Count 的数字部分",
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    const snapshot = await takeSnapshot(recordingId);
    const ref = refOf(snapshot.text, "Count");
    assert.ok(ref, snapshot.text);
    const result = await runAction(recordingId, { action: "click", ref });
    assert.equal(result.ok, true);
    assert.match(String(result.clicked || ""), /Count/);
    assert.ok((result.requests || []).some((row) => row.path === "/api/sort"), JSON.stringify(result.requests || []));
    assert.ok(Array.isArray(result.same_column) && result.same_column.length, JSON.stringify(result));
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});
