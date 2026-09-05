/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createPlaywrightBrowser } from "../src/browser-capture.mjs";
import {
  collectVisibleControlsInPage,
  projectVisibleControlSnapshot,
  summarizeVisibleControls,
} from "../src/visible-controls.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

test("可见控件只投影已有字段，不判断能力", () => {
  const snapshot = projectVisibleControlSnapshot({
    seq: 9,
    kind: "visible_control",
    payload: {
      url: "https://example.com/#/form",
      reason: "navigated",
      controls: [
        { region: "form", name: "title", label: "标题", placeholder: "请输入标题", control_kind: "input" },
        { region: "form", name: "", label: "开始日期", control_kind: "date", required_mark: true },
        { region: "form", name: "", label: "附件", control_kind: "upload" },
      ],
    },
  });
  assert.equal(snapshot.kind, "visible_control");
  assert.equal(snapshot.count, 3);
  assert.equal(snapshot.controls[1].control_kind, "date");
  assert.equal(snapshot.controls[1].required_mark, true);
  assert.equal(summarizeVisibleControls(snapshot.controls), "标题、开始日期、附件");
  assert.ok(!JSON.stringify(snapshot).includes("capability"));
});

test("采集日期、下拉、上传和折叠筛选，日期只读输入不当灰框", async (t) => {
  const html = await readFile(path.join(ROOT, "tests", "fixtures", "visible-controls.html"));
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const browser = await chromium.launch({ headless: true });
  t.after(async () => {
    await browser.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${port}/`);
  const controls = await page.evaluate(collectVisibleControlsInPage);
  const find = (label, kind) => controls.find((item) => item.label === label && (!kind || item.control_kind === kind));
  assert.equal(find("编号")?.region, "filter");
  assert.equal(find("编号")?.control_kind, "input");
  assert.equal(find("创建时间", "date")?.region, "filter");
  assert.equal(find("创建时间", "date")?.readonly, false);
  assert.equal(find("* 类型", "select")?.readonly, false);
  assert.equal(find("开始日期", "date")?.readonly, false);
  assert.ok(controls.some((item) => item.control_kind === "upload"));
  assert.ok(controls.some((item) => item.control_kind === "date" && (item.placeholder === "请选择日期" || item.label === "截止日期")));
  assert.ok(!JSON.stringify(controls).includes("capability"));
  assert.ok(!JSON.stringify(controls).includes("work-report"));
});

test("SPA 路由变化后补采当前页控件，不判断能力", async (t) => {
  const html = `<!doctype html><html><body>
    <form class="el-form"><div class="el-form-item"><label class="el-form-item__label">甲</label><input name="first" /></div></form>
    <a id="go" href="#/next">next</a>
    <script>
      window.addEventListener("hashchange", () => {
        document.body.innerHTML = '<form class="el-form"><div class="el-form-item"><label class="el-form-item__label">乙</label><input name="second" /></div></form>';
      });
    </script>
  </body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const events = [];
  const appendEvidence = async (kind, payload) => {
    events.push({ kind, payload });
    return { seq: events.length };
  };
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_test", byteLength: bytes.byteLength });
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_route_controls", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence,
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.page.click("#go");
  await handle.page.waitForTimeout(1200);
  const snapshots = events.filter((event) => event.kind === "visible_control");
  assert.ok(snapshots.some((event) => (event.payload?.controls || []).some((item) => item.name === "first" || item.label === "甲")));
  assert.ok(snapshots.some((event) => (event.payload?.controls || []).some((item) => item.name === "second" || item.label === "乙")));
  assert.ok(!JSON.stringify(snapshots).includes("capability"));
});
