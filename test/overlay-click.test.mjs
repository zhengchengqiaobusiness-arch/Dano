import assert from "node:assert/strict";
import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { browserSession, closeBrowser, desktopViewport, DESKTOP_VIEWPORT, getPage, openBrowser } from "../src/browser/session.mjs";
import { takeSnapshot } from "../src/browser/snapshot.mjs";
import { clickFailureKind, fieldFillText, runAction, withFillFieldText } from "../src/browser/actions.mjs";

const fixtureDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

test("a preview pane smaller than desktop does not become the page viewport", () => {
  assert.deepEqual(desktopViewport({ width: 900, height: 600 }), DESKTOP_VIEWPORT);
  assert.equal(desktopViewport({ width: 1600, height: 900 }).width, 1600);
});

test("click failures distinguish overlay intercepts from timeout and hidden controls", () => {
  assert.equal(clickFailureKind(new Error("Pointer events intercepts pointer")), "behind_overlay");
  assert.equal(clickFailureKind(new Error("<div> intercepts pointer events")), "behind_overlay");
  assert.equal(clickFailureKind(new Error("Element is not visible")), "not_visible");
  assert.equal(clickFailureKind(new Error("element is outside of the viewport")), "outside_viewport");
  assert.equal(clickFailureKind(new Error("Timeout 8000ms exceeded.")), "click_timeout");
  assert.equal(clickFailureKind(new Error("strict mode violation")), "click_failed");
});

test("fill_fields accepts value as the text to type", () => {
  assert.equal(fieldFillText({ ref: "f0:e1@1", value: "日报标题" }), "日报标题");
  assert.equal(fieldFillText({ ref: "f0:e1@1", text: "已有 text", value: "忽略" }), "已有 text");
  assert.equal(fieldFillText({ ref: "f0:e1@1" }), null);
  const act = withFillFieldText({
    action: "fill_fields",
    fields: [{ ref: "f0:e1@1", value: "工作总结" }],
  });
  assert.equal(act.fields[0].text, "工作总结");
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
    const dateFact = (opened.overlay_editable || []).find((row) => String(row.name || "").includes("汇报日期"));
    assert.ok(dateFact);
    const emptyTitle = (opened.overlay_fields || []).find((row) => String(row.name || "").includes("标题"));
    assert.ok(emptyTitle);
    assert.equal(emptyTitle.empty, true);
    assert.equal(emptyTitle.required, true);
    const editableTitle = (opened.overlay_editable || []).find((row) => String(row.name || "").includes("标题"));
    assert.ok(editableTitle);
    assert.ok(editableTitle.ref);
    const requiredTitle = (opened.required_empty_overlay || []).find((item) => String(item?.name || item).includes("标题"));
    assert.ok(requiredTitle);
    assert.ok(requiredTitle.ref);
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
    // 选中项画在包裹层、内嵌过滤框为空的下拉：已选即不是空，也不进必填空
    const pickedType = (opened.overlay_fields || []).find((row) => String(row.name || "").includes("汇报类型"));
    assert.ok(pickedType);
    assert.equal(pickedType.required, true);
    assert.equal(pickedType.empty, false);
    assert.match(String(pickedType.value || ""), /日报/);
    assert.equal((opened.required_empty_overlay || []).some((item) => String(item?.name || item).includes("汇报类型")), false);
    const pickedPeriod = (opened.overlay_fields || []).find((row) => String(row.name || "").includes("周期类型"));
    assert.ok(pickedPeriod);
    assert.equal(pickedPeriod.required, true);
    assert.equal(pickedPeriod.empty, false);
    assert.match(String(pickedPeriod.value || ""), /周报/);
    assert.equal((opened.required_empty_overlay || []).some((item) => String(item?.name || item).includes("周期类型")), false);
    assert.equal((opened.overlay_fields || []).some((row) => String(row.name || "").includes("隐藏必填")), false);
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
    assert.match(first.text, /overlay_exact/);
    const filled = await runAction(id, { action: "fill", ref: period, text: "2026-02-01" });
    assert.equal(filled.ok, false);
    assert.equal(filled.error, "needs_click");
    const periodFact = (filled.overlay_fields || []).find((row) => String(row.name || "").includes("周期"));
    assert.ok(periodFact);
    assert.equal(periodFact.readonly, true);
    assert.equal((filled.overlay_editable || []).some((row) => String(row.name || "").includes("周期")), false);
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

test("hash route does not bump epoch or drop current refs", { timeout: 60000 }, async () => {
  const html = `<!doctype html><html><body>
    <a id="go" href="#/other">去另一页</a>
    <button id="stay" type="button">仍在</button>
    <p id="panel">home</p>
    <script>
      window.addEventListener("hashchange", () => {
        document.getElementById("panel").textContent = location.hash || "home";
      });
    </script>
  </body></html>`;
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  const id = "rec_hash_epoch";
  try {
    await openBrowser({ recordingId: id, url, storageState: null, viewport: { width: 900, height: 700 } });
    const first = await takeSnapshot(id, { budgetMs: 4000 });
    const stay = publishedRef(lineWith(first.text, "仍在"));
    assert.ok(stay);
    const epoch = browserSession(id).epoch;
    const snap = browserSession(id).snap;
    await getPage(id).evaluate(() => { location.hash = "#/other"; });
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(browserSession(id).epoch, epoch);
    assert.equal(browserSession(id).snap, snap);
    const again = await runAction(id, { action: "click", ref: stay });
    assert.equal(again.ok, true);
    assert.notEqual(again.error, "stale_ref");
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => server.close(resolve));
  }
});

test("hash open waits until the new view overlay is current", { timeout: 60000 }, async () => {
  const html = `<!doctype html><html><body>
    <div id="a"><label>甲字段<input id="a-field" /></label></div>
    <div id="b" style="display:none"><label>乙字段<input id="b-field" required aria-required="true" /></label></div>
    <script>
      function show() {
        const onB = location.hash === "#/b";
        document.getElementById("a").hidden = onB;
        document.getElementById("b").hidden = !onB;
        document.getElementById("a").style.display = onB ? "none" : "block";
        document.getElementById("b").style.display = onB ? "block" : "none";
      }
      window.addEventListener("hashchange", () => setTimeout(show, 80));
      show();
    </script>
  </body></html>`;
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  const id = "rec_hash_overlay";
  try {
    await openBrowser({ recordingId: id, url, storageState: null, viewport: { width: 900, height: 700 } });
    const home = await runAction(id, { action: "snapshot" });
    assert.match(String((home.overlay_fields || []).map((row) => row.name).join(" ")), /甲字段/);
    const opened = await runAction(id, { action: "open", url: `${url}#/b` });
    assert.equal(opened.ok, true);
    const names = (opened.overlay_fields || []).map((row) => String(row.name || ""));
    assert.equal(names.some((name) => name.includes("甲字段")), false);
    assert.equal(names.some((name) => name.includes("乙字段")), true);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => server.close(resolve));
  }
});

test("browser_open same url is idempotent; same-origin hash may goto", { timeout: 60000 }, async () => {
  const site = await serve();
  const id = "rec_open_nav";
  try {
    await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 900, height: 700 } });
    const same = await runAction(id, { action: "open", url: getPage(id).url() });
    assert.equal(same.ok, true);
    assert.notEqual(same.error, "already_open");
    const next = `${site.url.replace(/\/?$/, "/") }#/other`;
    const opened = await runAction(id, { action: "open", url: next });
    assert.equal(opened.ok, true);
    assert.match(getPage(id).url(), /#\/other/);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => site.server.close(resolve));
  }
});
