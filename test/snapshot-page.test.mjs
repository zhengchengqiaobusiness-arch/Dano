import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { writeInitialGoal } from "../src/evidence/store.mjs";
import { closeBrowser, getPage, openBrowser } from "../src/browser/session.mjs";
import { locatorFor, lineRef, takeSnapshot } from "../src/browser/snapshot.mjs";
import { runAction } from "../src/browser/actions.mjs";

const dir = path.dirname(fileURLToPath(import.meta.url));
const pages = {
  "/": path.join(dir, "fixtures", "named-table.html"),
  "/inner-panel.html": path.join(dir, "fixtures", "inner-panel.html"),
};

function exactLines(text) {
  const exact = [];
  let inExact = false;
  for (const line of String(text || "").split("\n")) {
    if (line === "goal_exact") {
      inExact = true;
      continue;
    }
    if (inExact && line.startsWith("- ")) {
      exact.push(line);
      continue;
    }
    if (inExact) break;
  }
  return exact;
}

async function withPage(goalText, run) {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-snap-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const files = {
    "/": await readFile(pages["/"]),
    "/inner-panel.html": await readFile(pages["/inner-panel.html"]),
  };
  const server = createServer((req, res) => {
    const url = (req.url || "/").split("?")[0];
    const body = files[url] || files["/"];
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const recordingId = `rec_snap_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: goalText,
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    await getPage(recordingId).waitForSelector("iframe");
    return await run(recordingId);
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
}

test("live snapshot prints column names on cells and goal_exact skips the search box", async () => {
  await withPage("点击组织机构 应填数量的数字部分", async (recordingId) => {
    const snapshot = await takeSnapshot(recordingId);
    assert.match(snapshot.text, /^epoch /);
    assert.equal(snapshot.text.includes("\nfields\n"), false);
    assert.equal(/\srow="/.test(snapshot.text), false);
    const exact = exactLines(snapshot.text);
    assert.equal(exact.some((line) => line.includes("请输入部门名称")), false);
    assert.equal(exact.some((line) => line.includes("开始日期")), false);
    assert.equal(exact.some((line) => line.includes("周一") || line.includes('"1" 周一') || line.includes('"2" 周二')), false);
    assert.equal(exact.some((line) => line.includes("组织机构")), true);
    const number = snapshot.text.split("\n").find((line) => line.includes('"12"') && line.includes("应填数量"));
    assert.ok(number, snapshot.text);
    assert.equal(exact.some((line) => line.includes('"12"') && line.includes("应填数量")), true);
    assert.match(snapshot.text, /\n {2,}- /);
    assert.equal(/\nfields\n/.test(snapshot.text), false);
    const ref = lineRef(number);
    const hit = locatorFor(recordingId, ref);
    assert.ok(hit);
    const clicked = await runAction(recordingId, { action: "click", ref });
    assert.equal(clicked.ok, true);
    assert.match(String(clicked.clicked || ""), /应填数量/);
  });
});

test("a later snapshot invalidates the previous ref", async () => {
  await withPage("点击组织机构 应填数量的数字部分", async (recordingId) => {
    const first = await takeSnapshot(recordingId);
    const number = first.text.split("\n").find((line) => line.includes('"12"') && line.includes("应填数量"));
    const ref = lineRef(number);
    await takeSnapshot(recordingId);
    const stale = await runAction(recordingId, { action: "click", ref });
    assert.equal(stale.ok, false);
    assert.equal(stale.error, "stale_ref");
    assert.ok(stale.snapshot?.text);
  });
});

test("iframe controls keep their frame prefix and stay clickable", async () => {
  await withPage("点击提交", async (recordingId) => {
    const snapshot = await takeSnapshot(recordingId);
    const exact = exactLines(snapshot.text);
    const submit = exact.find((line) => line.includes("提交")) || snapshot.text.split("\n").find((line) => line.includes('button "提交"'));
    assert.ok(submit, snapshot.text);
    const ref = lineRef(submit);
    assert.match(ref, /^f[1-9]\d*:e\d+@\d+$/);
    const clicked = await runAction(recordingId, { action: "click", ref });
    assert.equal(clicked.ok, true);
    assert.match(String(clicked.clicked || ""), /提交/);
  });
});

test("snapshot waits until a late-rendered control is in the tree", async () => {
  const html = await readFile(path.join(dir, "fixtures", "delayed-app.html"));
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-delayed-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const recordingId = `rec_delayed_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: "点击查询",
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    const snapshot = await takeSnapshot(recordingId);
    assert.match(snapshot.text, /查询/);
    const line = snapshot.text.split("\n").find((row) => row.includes('"查询"') && (row.includes("ref=") || row.includes("[ref=")));
    const ref = lineRef(line);
    assert.ok(ref, snapshot.text);
    const clicked = await runAction(recordingId, { action: "click", ref });
    assert.equal(clicked.ok, true);
    assert.match(String(clicked.clicked || ""), /查询/);
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("snapshot keeps waiting when the first tree is only a shell for several seconds", async () => {
  const html = await readFile(path.join(dir, "fixtures", "delayed-late.html"));
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-late-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const recordingId = `rec_late_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: "点击查询",
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    const snapshot = await takeSnapshot(recordingId);
    assert.match(snapshot.text, /查询/);
    const line = snapshot.text.split("\n").find((row) => row.includes('"查询"') && (row.includes("ref=") || row.includes("[ref=")));
    const ref = lineRef(line);
    assert.ok(ref, snapshot.text);
    const clicked = await runAction(recordingId, { action: "click", ref });
    assert.equal(clicked.ok, true);
    assert.match(String(clicked.clicked || ""), /查询/);
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("press waits until a late-opened control is in the tree", async () => {
  const html = await readFile(path.join(dir, "fixtures", "delayed-press.html"));
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-press-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const recordingId = `rec_press_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: "选择今天",
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    const snapshot = await takeSnapshot(recordingId);
    const line = snapshot.text.split("\n").find((row) => row.includes('"日期"') && (row.includes("ref=") || row.includes("[ref=")));
    const ref = lineRef(line);
    assert.ok(ref, snapshot.text);
    const pressed = await runAction(recordingId, { action: "press", ref, key: "Enter" });
    assert.equal(pressed.ok, true);
    assert.match(String(pressed.snapshot?.text || ""), /今天/);
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("click waits until a late-opened listbox option is in the tree", async () => {
  const html = await readFile(path.join(dir, "fixtures", "delayed-listbox.html"));
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-listbox-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const recordingId = `rec_listbox_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: "选择研发部门",
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    const snapshot = await takeSnapshot(recordingId);
    const line = snapshot.text.split("\n").find((row) => row.includes('"部门"') && (row.includes("ref=") || row.includes("[ref=")));
    const ref = lineRef(line);
    assert.ok(ref, snapshot.text);
    const clicked = await runAction(recordingId, { action: "click", ref });
    assert.equal(clicked.ok, true);
    assert.match(String(clicked.snapshot?.text || ""), /研发部门/);
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("fill on a combobox waits until a late option is in the tree", async () => {
  const html = await readFile(path.join(dir, "fixtures", "delayed-combobox.html"));
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-combo-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  const server = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const recordingId = `rec_combo_${port}`;
  try {
    await writeInitialGoal(recordingId, {
      page_url: `http://127.0.0.1:${port}/`,
      goal_text: "选择研发部门",
    });
    await openBrowser({ recordingId, url: `http://127.0.0.1:${port}/` });
    const snapshot = await takeSnapshot(recordingId);
    const line = snapshot.text.split("\n").find((row) => row.includes('"部门"') && (row.includes("ref=") || row.includes("[ref=")));
    const ref = lineRef(line);
    assert.ok(ref, snapshot.text);
    const filled = await runAction(recordingId, { action: "fill", ref, text: "研发" });
    assert.equal(filled.ok, true);
    assert.match(String(filled.snapshot?.text || ""), /研发部门/);
  } finally {
    await closeBrowser(recordingId);
    await new Promise((resolve) => server.close(resolve));
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
});
