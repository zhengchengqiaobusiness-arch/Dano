import assert from "node:assert/strict";
import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { openBrowser, closeBrowser, getPage } from "../src/browser/session.mjs";
import { takeSnapshot } from "../src/browser/snapshot.mjs";
import { runAction } from "../src/browser/actions.mjs";
import { getNetwork, listNetwork, listScripts } from "../src/browser/network.mjs";

const fixtureDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

async function serve() {
  let saves = 0;
  const server = http.createServer(async (req, res) => {
    if (req.method === "POST" && req.url === "/api/save") {
      saves += 1;
      res.writeHead(200, { "content-type": "application/json", "access-control-allow-origin": "*" });
      res.end("{}");
      return;
    }
    const file = req.url === "/inner.html" ? "inner.html" : "iframe-form.html";
    const body = await readFile(path.join(fixtureDir, file));
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, url: `http://127.0.0.1:${server.address().port}/`, saves: () => saves };
}

function refsIn(text, frameId) {
  return text.split(/\n/).filter((line) => line.includes(`ref=${frameId}:`) && line.includes("button"));
}

test("iframe refs do not click the wrong submit", async () => {
  const site = await serve();
  const id = "rec_browser";
  try {
    await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 1440, height: 900 } });
    const first = await takeSnapshot(id);
    const outerRef = refsIn(first.text, "f0")[0].match(/ref=(f0:\S+)/)[1];
    const page = getPage(id);
    await page.reload({ waitUntil: "domcontentloaded" });
    const stale = await runAction(id, { action: "click", ref: outerRef });
    assert.equal(stale.error, "stale_ref");
    assert.equal(await page.evaluate(() => window.clicks.outer), 0);
    const fresh = await takeSnapshot(id);
    const outer = refsIn(fresh.text, "f0")[0].match(/ref=(f0:\S+)/)[1];
    const outerClick = await runAction(id, { action: "click", ref: outer });
    await page.waitForTimeout(300);
    assert.equal(site.saves(), 0);
    const inner = refsIn(outerClick.snapshot.text, "f1");
    const second = inner[1].match(/ref=(f1:\S+)/)[1];
    const innerClick = await runAction(id, { action: "click", ref: second });
    await page.waitForTimeout(500);
    const posts = listNetwork(id, {}).filter((row) => row.method === "POST" && row.path === "/api/save");
    assert.equal(posts.length, 1);
    const box = innerClick.snapshot.text.split(/\n/).find((line) => line.includes("textbox"));
    const inputRef = box.match(/ref=(f0:\S+)/)[1];
    const filled = await runAction(id, { action: "fill", ref: inputRef, text: "changed" });
    assert.equal(filled.error, "value_not_applied");
    const nodeLine = filled.snapshot.text.split(/\n/).find((line) => line.includes("节点甲"));
    const nodeRef = nodeLine.match(/ref=(f0:\S+)/)[1];
    const nodeClick = await runAction(id, { action: "click", ref: nodeRef });
    assert.equal(nodeClick.ok, true);
    assert.equal(await page.evaluate(() => window.clicks.node), 1);
    const radioLine = nodeClick.snapshot.text.split(/\n/).find((line) => line.includes("日报"));
    const radioRef = radioLine.match(/ref=(f0:\S+)/)[1];
    const radioClick = await runAction(id, { action: "click", ref: radioRef });
    assert.equal(radioClick.ok, true);
    assert.equal(await page.locator("#daily input").isChecked(), true);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => site.server.close(resolve));
  }
});

test("a loaded script is readable and is not a business request", async () => {
  const server = http.createServer(async (req, res) => {
    const file = req.url === "/app.js" ? "app.js" : "script-page.html";
    const body = await readFile(path.join(fixtureDir, file));
    const type = file.endsWith(".js") ? "text/javascript" : "text/html; charset=utf-8";
    res.writeHead(200, { "content-type": type });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const id = "rec_script";
  try {
    await openBrowser({ recordingId: id, url: `http://127.0.0.1:${server.address().port}/`, storageState: null, viewport: { width: 1440, height: 900 } });
    const page = getPage(id);
    let scripts = [];
    for (let i = 0; i < 20 && !scripts.length; i += 1) {
      scripts = listScripts(id);
      if (!scripts.length) await page.waitForTimeout(100);
    }
    assert.equal(scripts.some((row) => row.path === "/app.js"), true);
    assert.equal(listNetwork(id, {}).some((row) => row.path === "/app.js"), false);
    assert.match(getNetwork(id, scripts[0].id).response_body, /packRows/);
    assert.equal(scripts[0].query, undefined);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => server.close(resolve));
  }
});

test("a ref whose name changed is not clicked", async () => {
  const server = http.createServer(async (req, res) => {
    const body = await readFile(path.join(fixtureDir, "rename-button.html"));
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const id = "rec_rename";
  try {
    await openBrowser({ recordingId: id, url: `http://127.0.0.1:${server.address().port}/`, storageState: null, viewport: { width: 1440, height: 900 } });
    const snap = await takeSnapshot(id);
    const ref = snap.text.split(/\n/).find((line) => line.includes("编辑")).match(/ref=(\S+)/)[1];
    const page = getPage(id);
    await page.locator("#b").evaluate((node) => { node.textContent = "删除"; });
    const clicked = await runAction(id, { action: "click", ref });
    assert.equal(clicked.error, "stale_ref");
    assert.equal(clicked.ref, ref);
    assert.equal(await page.evaluate(() => window.clicks), 0);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => server.close(resolve));
  }
});

test("a key keeps the value the control took and the request from that key", async () => {
  const server = http.createServer(async (req, res) => {
    const body = await readFile(path.join(fixtureDir, "press-form.html"));
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const id = "rec_press";
  try {
    await openBrowser({ recordingId: id, url: `http://127.0.0.1:${server.address().port}/`, storageState: null, viewport: { width: 1440, height: 900 } });
    const snap = await takeSnapshot(id);
    const ref = snap.text.split(/\n/).find((line) => line.includes("查询")).match(/ref=(\S+)/)[1];
    const pressed = await runAction(id, { action: "press", ref, key: "Enter" });
    assert.equal(pressed.ok, true);
    assert.equal(pressed.filled_value[0].value, "AB");
    assert.ok(pressed.requests.some((row) => row.method === "POST" && row.path === "/api/search"));
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => server.close(resolve));
  }
});

test("a rewritten field reports the value the control kept, and the snapshot shows its own range", async () => {
  const server = http.createServer(async (req, res) => {
    const body = await readFile(path.join(fixtureDir, "facts-form.html"));
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const id = "rec_facts";
  try {
    await openBrowser({ recordingId: id, url: `http://127.0.0.1:${server.address().port}/`, storageState: null, viewport: { width: 1440, height: 900 } });
    const snap = await takeSnapshot(id);
    const qty = snap.text.split(/\n/).find((line) => line.includes("数量"));
    assert.match(qty, /\[required\]/);
    assert.match(qty, /min=0/);
    assert.match(qty, /max=10/);
    assert.match(qty, /step=1/);
    const codeRef = snap.text.split(/\n/).find((line) => line.includes("编码")).match(/ref=(\S+)/)[1];
    const filled = await runAction(id, { action: "fill", ref: codeRef, text: "ab" });
    assert.equal(filled.ok, true);
    assert.equal(filled.filled_value[0].value, "AB");
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => server.close(resolve));
  }
});
