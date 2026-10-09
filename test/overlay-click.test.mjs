import assert from "node:assert/strict";
import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { closeBrowser, desktopViewport, DESKTOP_VIEWPORT, openBrowser } from "../src/browser/session.mjs";
import { takeSnapshot } from "../src/browser/snapshot.mjs";
import { runAction } from "../src/browser/actions.mjs";

const fixtureDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

test("a preview pane smaller than desktop does not become the page viewport", () => {
  assert.deepEqual(desktopViewport({ width: 900, height: 600 }), DESKTOP_VIEWPORT);
  assert.equal(desktopViewport({ width: 1600, height: 900 }).width, 1600);
});

function publishedRef(line) {
  const match = String(line || "").match(/\[ref=([^\]]+)\]/) || String(line || "").match(/\bref=(f\d+:e\d+@\d+)/);
  return match ? match[1] : "";
}

function lineWith(text, needle) {
  return String(text || "").split(/\n/).find((line) => line.includes(needle) && /\[ref=/.test(line)) || "";
}

async function serve() {
  let saves = 0;
  const server = http.createServer(async (req, res) => {
    if (req.method === "POST" && req.url === "/api/save") {
      saves += 1;
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
      return;
    }
    const body = await readFile(path.join(fixtureDir, "overlay-form.html"));
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, url: `http://127.0.0.1:${server.address().port}/`, saves: () => saves };
}

test("an open dialog is published as overlay_exact and later clicks use the current tree", { timeout: 60000 }, async () => {
  const site = await serve();
  const id = "rec_overlay";
  try {
    await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 900, height: 700 } });
    const first = await takeSnapshot(id, { budgetMs: 4000 });
    const opened = await runAction(id, { action: "click", ref: publishedRef(lineWith(first.text, "打开")) });
    assert.equal(opened.ok, true);
    assert.match(opened.snapshot.text, /overlay_exact|dialog/);
    const emptyTitle = (opened.overlay_fields || []).find((row) => String(row.name || "").includes("标题"));
    assert.ok(emptyTitle);
    assert.equal(emptyTitle.empty, true);
    assert.equal(emptyTitle.required, true);
    assert.ok((opened.required_empty_overlay || []).some((name) => String(name).includes("标题")));
    assert.match(opened.snapshot.text, /col "/);
    const emptyCat = (opened.overlay_fields || []).find((row) => String(row.name || "").includes("分类"));
    assert.ok(emptyCat);
    assert.equal(emptyCat.empty, true);
    const emptyDuty = (opened.overlay_fields || []).find((row) => String(row.name || "").includes("职能清单") || String(row.column || "").includes("职能清单"));
    assert.ok(emptyDuty);
    assert.equal(emptyDuty.required, true);
    const emptySystem = (opened.overlay_fields || []).find((row) => String(row.name || "").includes("所属系统"));
    assert.ok(emptySystem);
    assert.equal(emptySystem.empty, true);
    assert.equal(emptySystem.required, false);
    const blocked = await runAction(id, { action: "click", ref: publishedRef(lineWith(opened.snapshot.text, "被挡住")) });
    const tree = blocked.snapshot?.text || opened.snapshot.text;
    assert.match(tree, /确定/);
    const titleRef = publishedRef(lineWith(tree, "标题"));
    const afterFill = titleRef
      ? await runAction(id, { action: "fill", ref: titleRef, text: "hello" })
      : { ok: true, snapshot: { text: tree } };
    if (titleRef) {
      assert.equal(afterFill.ok, true);
      const titleFact = (afterFill.overlay_fields || []).find((row) => row.ref === titleRef || String(row.name || "").includes("标题"));
      assert.ok(titleFact);
      assert.equal(titleFact.empty, false);
      assert.match(String(titleFact.value || ""), /hello/);
      assert.equal((afterFill.empty_overlay || []).some((name) => String(name).includes("标题")), false);
      assert.equal((afterFill.empty_overlay || []).some((name) => String(name).includes("分类")), true);
      assert.equal((afterFill.empty_overlay || []).some((name) => String(name).includes("所属系统")), true);
    }
    const latest = afterFill.snapshot?.text || tree;
    const ok = await runAction(id, { action: "click", ref: publishedRef(lineWith(latest, "确定")) });
    assert.equal(ok.ok, true);
    assert.equal(ok.error, undefined);
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal((ok.requests || []).some((row) => row.path === "/api/save") || site.saves() >= 1, true);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => site.server.close(resolve));
  }
});

test("a readonly control returns needs_click instead of filling", { timeout: 60000 }, async () => {
  const site = await serve();
  const id = "rec_overlay_readonly";
  try {
    await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 900, height: 700 } });
    const first = await takeSnapshot(id, { budgetMs: 4000 });
    const period = publishedRef(lineWith(first.text, "周期"));
    assert.ok(period);
    const filled = await runAction(id, { action: "fill", ref: period, text: "2026-02-01" });
    assert.equal(filled.ok, false);
    assert.equal(filled.error, "needs_click");
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => site.server.close(resolve));
  }
});

test("a click inside overlay_exact with no xhr is returned as layer_idle", { timeout: 60000 }, async () => {
  const site = await serve();
  const id = "rec_overlay_idle";
  try {
    await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 900, height: 700 } });
    const first = await takeSnapshot(id, { budgetMs: 4000 });
    const opened = await runAction(id, { action: "click", ref: publishedRef(lineWith(first.text, "打开")) });
    const cancelRef = publishedRef(lineWith(opened.snapshot.text, "取消"));
    const idle = await runAction(id, { action: "click", ref: cancelRef });
    assert.equal(idle.ok, true);
    assert.equal(idle.in_overlay, true);
    assert.equal(idle.layer_idle, true);
    assert.equal((idle.requests || []).length, 0);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => site.server.close(resolve));
  }
});
