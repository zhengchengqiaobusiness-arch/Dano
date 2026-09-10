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
  collectPageFacts,
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
        {
          region: "filter",
          label: "组织机构",
          control_kind: "select",
          options: ["研发部门", "测试部门"],
        },
        {
          region: "filter",
          label: "统计周期",
          control_kind: "date",
          range: true,
          placeholder: "开始日期 → 结束日期",
        },
        {
          region: "dialog",
          label: "提交意见",
          placeholder: "请输入提交意见",
          control_kind: "textarea",
        },
      ],
    },
  });
  assert.equal(snapshot.kind, "visible_control");
  assert.equal(snapshot.count, 6);
  assert.equal(snapshot.controls[5].region, "dialog");
  assert.equal(snapshot.controls[1].control_kind, "date");
  assert.equal(snapshot.controls[1].required_mark, true);
  assert.deepEqual(snapshot.controls[3].options, ["研发部门", "测试部门"]);
  assert.equal(snapshot.controls[4].range, true);
  assert.equal(
    summarizeVisibleControls(snapshot.controls),
    "标题、开始日期(date)、附件(upload)、组织机构(select:研发部门/测试部门)、统计周期(date)、提交意见(textarea)",
  );
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
  const facts = await page.evaluate(collectPageFacts);
  const controls = facts.visible;
  const shot = facts.controls;
  const find = (label, kind) => controls.find((item) => item.label === label && (!kind || item.control_kind === kind));
  const findShot = (label) => shot.find((item) => item.label === label || item.placeholder === label);
  assert.equal(find("编号")?.region, "filter");
  assert.equal(find("编号")?.control_kind, "input");
  assert.equal(find("创建时间"), undefined, "折叠筛选未展开时不采集隐藏字段");
  await page.click("#expand");
  const expanded = await page.evaluate(collectPageFacts);
  const findExpanded = (label, kind) => expanded.visible.find((item) => item.label === label && (!kind || item.control_kind === kind));
  assert.equal(findExpanded("创建时间", "date")?.region, "filter");
  assert.equal(findExpanded("创建时间", "date")?.readonly, false);
  assert.equal(find("类型", "select")?.readonly, false);
  const reportType = find("汇报类型", "select");
  assert.ok(reportType, "disabled select should still be collected");
  assert.equal(reportType.readonly, true);
  assert.equal(reportType.disabled, true);
  assert.ok(!controls.some((item) => item.label === "汇报类型" && item.control_kind === "input" && !item.readonly && !item.disabled));
  assert.equal(find("使用印章", "select")?.readonly, false, "hidden disabled descendant must not lock an openable select");
  assert.equal(find("是否可用")?.readonly, false);
  assert.ok((find("是否可用")?.options || []).includes("启用"));
  assert.equal(find("所属部门", "select")?.readonly, false);
  assert.equal(find("锁定部门", "select")?.readonly, true);
  assert.equal(findShot("请选择印章")?.readonly, false);
  assert.equal(findShot("请选择部门")?.readonly, false);
  assert.equal(findShot("已锁定")?.readonly, true);
  assert.ok(!controls.some((item) => /共\s*2\s*条|20条\/页/.test(`${item.label}${item.placeholder}`)));
  assert.ok(!shot.some((item) => /20条\/页/.test(`${item.label}${item.placeholder}`)));
  assert.ok(!controls.some((item) => String(item.label || "").startsWith("*")));
  assert.equal(find("开始日期", "date")?.readonly, false);
  assert.ok(controls.some((item) => item.control_kind === "upload"));
  assert.ok(controls.some((item) => item.control_kind === "date" && (item.placeholder === "请选择日期" || item.label === "截止日期")));
  const tree = controls.find((item) => item.control_kind === "select" && (item.label === "组织机构" || item.placeholder === "请输入部门名称" || (item.options || []).includes("研发部门")));
  assert.ok(tree, "sidebar tree should be collected as a select fact");
  assert.equal(tree.region, "filter");
  assert.ok((tree.options || []).includes("测试部门"));
  const treeAction = (facts.actions || []).find((item) => item.label === "研发部门");
  assert.ok(treeAction, "snapshot must advertise the visible tree node so PI can click it");
  assert.match(String(treeAction.selector || ""), /^text=/);
  const tabs = controls.find((item) => item.control_kind === "select" && (item.options || []).includes("日报") && (item.options || []).includes("周报"));
  assert.ok(tabs, "page-level radio/tab group should be collected even without a form-item");
  const period = controls.find((item) => item.control_kind === "date" && (item.label === "统计周期" || item.range));
  assert.ok(period, "visible date range should be collected as one date control");
  assert.equal(period.range, true);
  assert.match(String(period.placeholder || ""), /开始日期/);
  const contents = controls.filter((item) => item.region === "table" && item.label === "工作内容");
  const progresses = controls.filter((item) => item.region === "table" && item.label === "完成进度");
  assert.equal(contents.length, 1);
  assert.equal(progresses.length, 1);
  assert.equal(contents[0].section, "已完成工作");
  const addRow = controls.find((item) => item.control_kind === "button" && item.label === "添加工作项");
  assert.ok(addRow, "add-row button should be collected as a fact");
  assert.equal(addRow.region, "table");
  const planContents = controls.filter((item) => item.region === "table" && item.label === "计划内容");
  assert.equal(planContents.length, 1);
  assert.equal(planContents[0].section, "工作计划");
  const attach = controls.find((item) => item.control_kind === "upload");
  assert.ok(attach);
  assert.ok(attach.section === "附件信息" || attach.label === "上传附件" || attach.label === "附件信息");
  const note = controls.find((item) => item.label === "补充说明");
  assert.ok(note, "form textarea should stay a separate fact from table rows");
  assert.equal(note.region, "form");
  assert.equal(note.control_kind, "textarea");
  const opinion = controls.find((item) => item.placeholder === "请输入提交意见" || item.label === "提交意见");
  assert.ok(opinion, "confirm-dialog textarea should be collected");
  assert.equal(opinion.region, "dialog");
  const picker = controls.find((item) => item.control_kind === "checkbox" && /张三/.test(item.label));
  assert.ok(picker, "dialog user-picker checkbox should be a visible_control fact");
  assert.equal(picker.region, "dialog");
  assert.ok(
    (facts.actions || []).some((item) => item.label === "张三" && (item.kind === "row" || item.kind === "checkbox")),
    "snapshot must expose 张三 as a clickable row or checkbox",
  );
  assert.ok(
    (facts.actions || []).some((item) => item.kind === "checkbox" && /张三/.test(item.label || "")),
    "snapshot must expose the user-picker checkbox",
  );
  assert.ok(!JSON.stringify(controls).includes("capability"));
  assert.ok(!JSON.stringify(controls).includes("work-report"));
});

test("snapshot 广告树节点，不广告无名钮和顶栏角标", async (t) => {
  const html = `<!doctype html><html lang="zh-CN"><body>
    <header class="el-header">
      <button type="button" class="el-button" id="icon-only"><svg width="16" height="16"></svg></button>
      <button type="button" class="el-button" id="badge">0</button>
      <button type="button" class="el-button" id="who">admin</button>
    </header>
    <aside class="el-aside">
      <div class="title">组织机构</div>
      <input placeholder="请输入部门名称" />
      <div role="tree" class="el-tree">
        <div role="treeitem">深圳总公司</div>
        <div class="el-tree-node__label">研发部门</div>
      </div>
      <div class="vue-treeselect">
        <div class="vue-treeselect__menu">
          <div class="vue-treeselect__label">总公司(5)</div>
        </div>
      </div>
    </aside>
    <div class="page-filters">
      <div class="el-radio-group" role="radiogroup">
        <label class="el-radio-button">日报</label>
        <label class="el-radio-button">周报</label>
      </div>
      <span class="label">统计周期</span>
      <div class="el-date-editor el-range-editor">
        <input placeholder="开始日期" />
        <input placeholder="结束日期" />
      </div>
    </div>
    <form class="el-form">
      <button type="button" class="el-button">新增</button>
      <button type="button" class="el-button">保 存</button>
    </form>
  </body></html>`;
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
  const facts = await page.evaluate(collectPageFacts);
  const actions = facts.actions || [];
  const labels = actions.map((item) => String(item.label || ""));
  assert.ok(labels.includes("研发部门"), `tree node missing, got ${labels.join(",")}`);
  assert.ok(labels.includes("深圳总公司"), `tree node missing, got ${labels.join(",")}`);
  assert.ok(labels.includes("总公司(5)"), `vue-treeselect node missing, got ${labels.join(",")}`);
  assert.ok(labels.includes("新增"));
  assert.ok(labels.includes("保 存") || labels.includes("保存"));
  assert.ok(!labels.some((label) => !label.trim()), "unlabeled header icons must not be advertised");
  assert.ok(!labels.includes("0"), "numeric badges must not be advertised");
  assert.ok(!labels.includes("admin"), "header identity is not a business action");
  assert.ok(actions.every((item) => item.selector && !/^ref=a\d+$/.test(item.selector)), "actions must have a semantic selector, not bare ref=aN");
  const period = (facts.controls || []).find((item) => item.control_kind === "date");
  assert.ok(period, "date range must appear in snapshot controls");
  assert.match(String(period.placeholder || ""), /开始日期/);
  assert.match(String(period.placeholder || ""), /结束日期/);
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

test("加行或打开确认弹层后补采当前页控件", async (t) => {
  const html = `<!doctype html><html><body>
    <h3>已完成工作</h3>
    <button type="button" id="add" class="el-button">添加工作项</button>
    <table class="el-table">
      <thead><tr><th>工作内容</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <script>
      document.getElementById("add").addEventListener("click", () => {
        const row = document.createElement("tr");
        row.innerHTML = '<td><input placeholder="请输入工作内容" /></td>';
        document.getElementById("rows").appendChild(row);
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
    recording: { id: "rec_add_row_controls", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence,
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.page.click("#add");
  await handle.page.waitForTimeout(900);
  const snapshots = events.filter((event) => event.kind === "visible_control");
  assert.ok(snapshots.some((event) => (
    event.payload?.reason === "interaction"
    && (event.payload?.controls || []).some((item) => item.label === "工作内容" && item.region === "table")
  )), "clicking add-row should recapture the new table input");
});

test("任意点击打开弹层后补采，不依赖新增文案", async (t) => {
  const html = `<!doctype html><html><body>
    <form class="el-form"><div class="el-form-item"><label class="el-form-item__label">列表名</label><input name="q" /></div></form>
    <button type="button" id="open">打开面板</button>
    <div id="dlg" style="display:none" class="el-dialog" role="dialog" aria-modal="true">
      <div class="el-form-item">
        <label class="el-form-item__label">模版名称</label>
        <input placeholder="请输入模版名称" />
      </div>
      <div class="el-form-item">
        <label class="el-form-item__label">使用公章</label>
        <div class="el-select"><input readonly placeholder="请选择使用公章" /></div>
      </div>
    </div>
    <script>
      document.getElementById("open").addEventListener("click", () => {
        document.getElementById("dlg").style.display = "block";
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
    events.push({ kind, payload, seq: events.length + 1 });
    return { seq: events.length };
  };
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_test", byteLength: bytes.byteLength });
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_open_panel_controls", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence,
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.page.click("#open");
  await handle.page.waitForTimeout(900);
  const snapshots = events.filter((event) => event.kind === "visible_control");
  assert.ok(snapshots.some((event) => (
    (event.payload?.controls || []).some((item) => item.label === "模版名称" && item.region === "dialog")
  )), "opening a dialog by a generic click should recapture dialog fields");
  assert.ok(snapshots.some((event) => (
    (event.payload?.controls || []).some((item) => item.label === "使用公章" && item.readonly === false)
  )), "openable select in a dialog must not be marked readonly");
});

test("snapshot 写入当前页 visible_control", async (t) => {
  const html = `<!doctype html><html><body>
    <form class="el-form"><div class="el-form-item"><label class="el-form-item__label">标题</label><input placeholder="请输入标题" /></div></form>
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
    recording: { id: "rec_snapshot_controls", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence,
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  const shot = await handle.inspect();
  assert.ok(shot.controls.some((item) => item.placeholder === "请输入标题" || item.label === "标题"));
  assert.ok(events.some((event) => event.kind === "visible_control" && event.payload?.reason === "snapshot"));
});

function evidenceSink() {
  const appendEvidence = async () => ({ seq: 1 });
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_test", byteLength: bytes.byteLength });
  return appendEvidence;
}

test("fill 日期框不得改口成下拉", async (t) => {
  const html = await readFile(path.join(ROOT, "tests", "fixtures", "visible-controls.html"));
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_fill_date", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence: evidenceSink(),
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.inspect();
  const pickerFill = await handle.actBySelector({
    selector: "placeholder=请选择日期",
    action: "fill",
    text: "2026-09-08",
  });
  assert.notEqual(pickerFill.error, "这是下拉。用 choose，不要往里面打字。");
  assert.notEqual(pickerFill.code, "not_found");
  assert.equal(pickerFill.ok, true, pickerFill.error || "fill date failed");
  const rangeFill = await handle.actBySelector({
    selector: "placeholder=开始日期",
    action: "fill",
    text: "2026-09-01",
  });
  assert.notEqual(rangeFill.error, "这是下拉。用 choose，不要往里面打字。");
  assert.equal(rangeFill.ok, true, rangeFill.error || "fill range start failed");
});

test("choose 点已经出现的可见原文，不要求下拉 option", async (t) => {
  const html = `<!doctype html><html><body>
    <div role="radiogroup" aria-label="汇报类型">
      <label role="radio">日报</label>
      <label role="radio">周报</label>
      <label role="radio">月报</label>
    </div>
    <label>请假类型
      <select>
        <option>请选择</option>
        <option>事假</option>
        <option>病假</option>
      </select>
    </label>
    <script>
      window.__picked = "";
      for (const item of document.querySelectorAll("[role='radio']")) {
        item.addEventListener("click", () => { window.__picked = item.textContent.trim(); });
      }
    </script>
  </body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_choose_visible", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence: evidenceSink(),
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.inspect();
  const radio = await handle.actBySelector({
    selector: "label=汇报类型",
    action: "choose",
    text: "日报",
  });
  assert.equal(radio.ok, true, radio.error || "choose 日报 failed");
  assert.equal(await handle.page.evaluate(() => window.__picked), "日报");
  const selected = await handle.actBySelector({
    selector: "label=请假类型",
    action: "choose",
    text: "事假",
  });
  assert.equal(selected.ok, true, selected.error || "choose 事假 failed");
  const value = await handle.page.locator("select").inputValue();
  assert.equal(value, "事假");
});

test("choose 能点已经出现的树节点", async (t) => {
  const html = `<!doctype html><html><body>
    <aside class="el-aside">
      <div role="tree" class="el-tree">
        <div role="treeitem" id="dept">研发部门</div>
      </div>
    </aside>
    <script>
      window.__picked = "";
      document.getElementById("dept").addEventListener("click", () => { window.__picked = "研发部门"; });
    </script>
  </body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_choose_tree", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence: evidenceSink(),
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.inspect();
  const picked = await handle.actBySelector({
    selector: "text=研发部门",
    action: "choose",
    text: "研发部门",
  });
  assert.equal(picked.ok, true, picked.error || "choose tree node failed");
  assert.equal(await handle.page.evaluate(() => window.__picked), "研发部门");
});

test("点保存必须点到按钮，不能落到旁边的标题框", async (t) => {
  const html = `<!doctype html><html><body>
    <form class="el-form" style="width:720px;padding:24px">
      <div class="el-form-item">
        <label class="el-form-item__label">申请标题</label>
        <div class="el-input"><input placeholder="请输入申请标题" /></div>
      </div>
      <div class="el-form-item">
        <label class="el-form-item__label">请示内容</label>
        <textarea placeholder="请输入请示内容"></textarea>
      </div>
      <div class="dialog-footer" style="margin-top:24px">
        <button type="button" class="el-button">取消</button>
        <button type="button" class="el-button" id="save">保存</button>
      </div>
    </form>
    <script>
      window.__clicked = "";
      document.getElementById("save").addEventListener("click", () => { window.__clicked = "save"; });
      document.querySelector("input").addEventListener("click", () => { window.__clicked = "title"; });
    </script>
  </body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_save_center", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence: evidenceSink(),
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.inspect();
  const clicked = await handle.actBySelector({
    selector: 'role=button[name="保存"]',
    action: "click",
  });
  assert.equal(clicked.ok, true, clicked.error || "click 保存 failed");
  assert.equal(await handle.page.evaluate(() => window.__clicked), "save");
});

test("点 保 存 不得落到旁边的 提 交", async (t) => {
  const html = `<!doctype html><html><body>
    <form class="el-form">
      <div class="dialog-footer" style="display:flex;gap:12px;padding:16px">
        <button type="button" class="el-button el-button--primary" id="submit" style="width:96px;height:36px">提 交</button>
        <button type="button" class="el-button" id="save" style="width:72px;height:32px">保 存</button>
        <button type="button" class="el-button" id="del" style="width:72px;height:32px">删 除</button>
        <button type="button" class="el-button" id="close" style="width:72px;height:32px">关 闭</button>
      </div>
    </form>
    <script>
      window.__clicked = "";
      for (const id of ["submit", "save", "del", "close"]) {
        document.getElementById(id).addEventListener("click", () => { window.__clicked = id; });
      }
    </script>
  </body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_save_not_submit", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence: evidenceSink(),
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.inspect();
  const clicked = await handle.actBySelector({
    selector: 'role=button[name="保 存"]',
    action: "click",
  });
  assert.equal(clicked.ok, true, clicked.error || "click 保 存 failed");
  assert.equal(await handle.page.evaluate(() => window.__clicked), "save");
});

test("choose 打开后是日历时不得改口，只回报 option_not_seen", async (t) => {
  const html = `<!doctype html><html><body>
    <label>开始日期 <input id="start" placeholder="开始日期" readonly /></label>
    <div id="cal" hidden style="display:none;grid-template-columns:repeat(7,32px);gap:2px"></div>
    <script>
      const cal = document.getElementById("cal");
      for (let day = 1; day <= 31; day += 1) {
        const cell = document.createElement("div");
        cell.textContent = String(day);
        cell.style.width = "28px";
        cell.style.height = "28px";
        cal.appendChild(cell);
      }
      document.getElementById("start").addEventListener("click", () => {
        cal.hidden = false;
        cal.style.display = "grid";
      });
    </script>
  </body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_choose_calendar", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence: evidenceSink(),
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.inspect();
  const chosen = await handle.actBySelector({
    selector: "placeholder=开始日期",
    action: "choose",
    text: "日报",
  });
  assert.equal(chosen.ok, false);
  assert.equal(chosen.code, "option_not_seen");
  assert.equal(chosen.panel, "calendar");
  assert.doesNotMatch(String(chosen.error || ""), /这是下拉/);
  const written = await handle.actBySelector({
    selector: "placeholder=开始日期",
    action: "fill",
    text: "2026-09-01",
  });
  assert.equal(written.ok, true, written.error || "fill 日历格 failed");
});

test("嵌套容器和拆分表头不得吞掉叶子控件，行内列可填可选", async (t) => {
  const html = await readFile(path.join(ROOT, "tests", "fixtures", "nested-form-table.html"));
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_nested_table", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence: evidenceSink(),
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.inspect();
  const facts = await handle.page.evaluate(collectPageFacts);
  const visible = facts.visible || [];
  const shot = facts.controls || [];
  const findVisible = (label, kind) => visible.find((item) => item.label === label && (!kind || item.control_kind === kind));
  const findShot = (label) => shot.find((item) => item.label === label || item.placeholder === label);
  assert.ok(findVisible("标题"), `标题 should stay a leaf fact, got ${visible.map((item) => item.label).join(",")}`);
  assert.ok(findVisible("工作总结"), "工作总结 should stay a leaf fact");
  assert.equal(findVisible("工作内容")?.region, "table");
  assert.equal(findVisible("完成进度")?.region, "table");
  assert.ok(!visible.some((item) => /请输入 → 请输入|请选择年月/.test(String(item.placeholder || ""))));
  assert.ok(findShot("标题") || findShot("请输入标题"), "snapshot must advertise the title field");
  assert.ok(findShot("工作内容") || findShot("请输入工作内容"), "snapshot must advertise the row content field");
  const progress = findShot("完成进度");
  assert.ok(progress, `snapshot must advertise the progress column, got ${shot.map((item) => item.label || item.placeholder).join(",")}`);
  assert.equal(progress.region, "table");
  assert.match(String(progress.selector || ""), /label=完成进度|placeholder=/);
  const shotJson = JSON.stringify(shot);
  assert.ok(!shotJson.includes("请输入 →"), "snapshot must not join every nested placeholder into one control");

  const filled = await handle.actBySelector({
    selector: "label=标题",
    action: "fill",
    text: "测试标题",
  });
  assert.equal(filled.ok, true, filled.error || "fill 标题 failed");
  const rowFill = await handle.actBySelector({
    selector: "placeholder=请输入工作内容",
    action: "fill",
    text: "写完这一行",
  });
  assert.equal(rowFill.ok, true, rowFill.error || "fill 工作内容 failed");
  const chosen = await handle.actBySelector({
    selector: "label=完成进度",
    action: "choose",
    text: "100%",
  });
  assert.equal(chosen.ok, true, chosen.error || "choose 完成进度 failed");
  assert.equal(await handle.page.evaluate(() => window.__progress), "100%");
});

test("按钮上 fill 回报 not_writable，不改口", async (t) => {
  const html = `<!doctype html><html><body><button type="button">保存</button></body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_fill_button", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence: evidenceSink(),
  });
  t.after(async () => {
    await handle.close().catch(() => {});
    await new Promise((resolve) => fixture.close(resolve));
  });
  await handle.inspect();
  const filled = await handle.actBySelector({
    selector: 'role=button[name="保存"]',
    action: "fill",
    text: "2026-09-01",
  });
  assert.equal(filled.ok, false);
  assert.equal(filled.code, "not_writable");
  assert.doesNotMatch(String(filled.error || ""), /这是下拉/);
});
