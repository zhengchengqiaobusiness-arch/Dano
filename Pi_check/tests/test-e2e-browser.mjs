/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { createPlaywrightBrowser } from "../src/browser-capture.mjs";
import { createPiToolHost } from "../src/pi-tools.mjs";
import { playwrightStateFromTokens, saveStorageState } from "../src/session-store.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

test("13. 完成一次真实浏览器录制", async (t) => {
  const html = await readFile(path.join(ROOT, "tests", "fixtures", "demo.html"));
  const fixture = createServer((req, res) => {
    if (req.url === "/api/leave") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const targetUrl = `http://127.0.0.1:${port}/`;
  let browserRef = null;
  const harness = await createHarness({
    emitOnStart: false,
    result: sampleResult({ e2e: "browser" }),
    createBrowser: async ({ recording, appendEvidence }) => {
      browserRef = await createPlaywrightBrowser({ recording, appendEvidence });
      return browserRef;
    },
  });
  t.after(async () => {
    try {
      await browserRef?.close();
    } catch {
      // ignore
    }
    await harness.cleanup();
    await new Promise((resolve) => fixture.close(resolve));
  });

  const started = await harness.controller.start({
    targetUrl,
    goal: "在演示页提交一次表单",
  });
  assert.ok(browserRef?.page);
  await browserRef.page.fill("#days", "2");
  await browserRef.page.click("#submit");
  await browserRef.page.waitForTimeout(300);
  const stopped = await harness.controller.stop(started.id);
  assert.equal(stopped.session.status, "succeeded");
  assert.ok(stopped.session.evidenceCount > 0);
  const events = await harness.files.readEvidence(started.id);
  assert.ok(events.some((event) => event.kind === "network_request"));
  assert.ok(events.some((event) => event.kind === "interaction" || event.kind === "screenshot"));
  const controls = events.find((event) => event.kind === "visible_control");
  assert.ok(controls);
  assert.ok((controls.payload?.controls || []).some((item) => item.name === "days" || item.label.includes("天数")));
  assert.deepEqual(stopped.result.e2e, "browser");
});

test("同站点下次打开时恢复登录态，预览页能读到已存 token", async (t) => {
  const previousAutoLogin = process.env.PI_CHECK_AUTO_LOGIN;
  process.env.PI_CHECK_AUTO_LOGIN = "0";
  t.after(() => {
    if (previousAutoLogin == null) delete process.env.PI_CHECK_AUTO_LOGIN;
    else process.env.PI_CHECK_AUTO_LOGIN = previousAutoLogin;
  });
  const html = `<!doctype html><html><body>
    <div id="out"></div>
    <script>
      document.getElementById("out").textContent = localStorage.getItem("ACCESS_TOKEN") ? "authed" : "anon";
    </script>
  </body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const origin = `http://127.0.0.1:${port}`;
  await saveStorageState(`${origin}/`, playwrightStateFromTokens(origin, {
    accessToken: "restored-token",
    refreshToken: "restored-refresh",
    tenantId: "1",
  }));
  const events = [];
  const appendEvidence = async (kind, payload) => {
    events.push({ kind, payload });
    return { seq: events.length };
  };
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_test", byteLength: bytes.byteLength });
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_session_restore", targetUrl: `${origin}/` },
    appendEvidence,
  });
  t.after(async () => {
    try {
      await handle.close();
    } catch {
      // ignore
    }
    await new Promise((resolve) => fixture.close(resolve));
  });
  const text = await handle.page.locator("#out").innerText();
  assert.equal(text, "authed");
  assert.ok(events.some((event) => event.kind === "network_request"));
});

test("同页跳转和新窗口都会成为当前录制页", async (t) => {
  process.env.PI_CHECK_AUTO_LOGIN = "0";
  const home = `<!doctype html><html><body>
    <a id="same" href="/other">same tab</a>
    <button id="popup">popup</button>
    <script>
      document.getElementById("popup").onclick = () => window.open("/other", "_blank");
    </script>
  </body></html>`;
  const other = `<!doctype html><html><body><h1 id="dest">other page</h1></body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(req.url?.startsWith("/other") ? other : home);
  });
  const port = await listen(fixture);
  const origin = `http://127.0.0.1:${port}`;
  const events = [];
  const appendEvidence = async (kind, payload) => {
    events.push({ kind, payload });
    return { seq: events.length };
  };
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_nav", byteLength: bytes.byteLength });
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_nav", targetUrl: `${origin}/` },
    appendEvidence,
  });
  t.after(async () => {
    try {
      await handle.close();
    } catch {
      // ignore
    }
    await new Promise((resolve) => fixture.close(resolve));
  });

  await handle.page.click("#same");
  await handle.page.waitForURL(/\/other/);
  assert.match(handle.page.url(), /\/other/);
  assert.equal(await handle.page.locator("#dest").innerText(), "other page");

  await handle.applyInput({ kind: "goto", url: `${origin}/` });
  await handle.page.waitForURL((url) => url.pathname === "/");
  const first = handle.page;
  const popupWait = first.waitForEvent("popup");
  await first.click("#popup");
  const popup = await popupWait;
  await popup.waitForURL(/\/other/);
  assert.match(handle.page.url(), /\/other/);
  assert.equal(handle.page, popup);
  assert.ok(events.filter((event) => event.kind === "page_created").length >= 2);
  await popup.close();
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(handle.livePage(), first);
});

test("录制动作和可见控件都进入 iframe 业务页", async (t) => {
  process.env.PI_CHECK_AUTO_LOGIN = "0";
  const shell = await readFile(path.join(ROOT, "tests", "fixtures", "iframe-oa.html"));
  const inner = await readFile(path.join(ROOT, "tests", "fixtures", "iframe-oa-inner.html"));
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(req.url?.startsWith("/inner") ? inner : shell);
  });
  const port = await listen(fixture);
  const events = [];
  const appendEvidence = async (kind, payload) => {
    events.push({ kind, payload });
    return { seq: events.length };
  };
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_iframe", byteLength: bytes.byteLength });
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_iframe_oa", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence,
  });
  t.after(async () => {
    try {
      await handle.close();
    } catch {
      // ignore
    }
    await new Promise((resolve) => fixture.close(resolve));
  });

  const controls = events.find((event) => event.kind === "visible_control")?.payload?.controls || [];
  assert.ok(controls.some((item) => String(item.label || "").includes("申请部门")), JSON.stringify(controls));

  await handle.act({ action: "fill_placeholder", selector: "单据编号", text: "SQ-1" });
  await handle.act({ action: "click_text", text: "搜索" });
  await handle.act({ action: "click_text", text: "新增" });
  await handle.act({ action: "click", selector: "#item" });
  await handle.act({ action: "click_role", selector: "button", text: "确认" });

  const frame = handle.page.frames().find((item) => item.url().includes("/inner"));
  assert.ok(frame);
  assert.equal(await frame.locator("body").getAttribute("data-searched"), "SQ-1");
  assert.equal(await frame.locator("body").getAttribute("data-confirmed"), "1");
});

test("真实鼠标点到 iframe 里可见的 .el-button，不点外壳隐藏同名按钮", async (t) => {
  process.env.PI_CHECK_AUTO_LOGIN = "0";
  const shell = await readFile(path.join(ROOT, "tests", "fixtures", "iframe-hidden-search.html"));
  const inner = await readFile(path.join(ROOT, "tests", "fixtures", "iframe-el-button.html"));
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(req.url?.includes("inner-el-button") ? inner : shell);
  });
  const port = await listen(fixture);
  const events = [];
  const appendEvidence = async (kind, payload) => {
    events.push({ kind, payload });
    return { seq: events.length };
  };
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_hidden", byteLength: bytes.byteLength });
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_hidden_search", targetUrl: `http://127.0.0.1:${port}/` },
    appendEvidence,
  });
  t.after(async () => {
    try {
      await handle.close();
    } catch {
      // ignore
    }
    await new Promise((resolve) => fixture.close(resolve));
  });

  const shot = await handle.inspect();
  assert.ok(shot.actions?.some((item) => item.label === "搜索"), JSON.stringify(shot.actions));
  const filled = await handle.actBySelector({
    selector: "placeholder=请输入单据编号",
    action: "fill",
    text: "RQ-9",
  });
  assert.equal(filled.ok, true, JSON.stringify(filled));
  const searched = await handle.actBySelector({
    selector: 'role=button[name="搜索"]',
    action: "click",
  });
  assert.equal(searched.ok, true, JSON.stringify(searched));
  const created = await handle.actBySelector({
    selector: 'role=button[name="新增"]',
    action: "click",
  });
  assert.equal(created.ok, true, JSON.stringify(created));
  const frame = handle.page.frames().find((item) => item.url().includes("inner-el-button"));
  assert.ok(frame);
  assert.equal(await frame.locator("body").getAttribute("data-searched"), "RQ-9");
  assert.equal(await frame.locator("body").getAttribute("data-created"), "1");
});

test("choose 一次选中下拉，不必再 snapshot 点选项", async (t) => {
  const html = await readFile(path.join(ROOT, "tests", "fixtures", "leave-select.html"));
  const fixture = createServer((req, res) => {
    if (req.url.startsWith("/api/")) {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true, path: req.url }));
      return;
    }
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const targetUrl = `http://127.0.0.1:${port}/`;
  let browserRef = null;
  const harness = await createHarness({
    emitOnStart: false,
    result: sampleResult({ e2e: "choose" }),
    createBrowser: async ({ recording, appendEvidence }) => {
      browserRef = await createPlaywrightBrowser({ recording, appendEvidence });
      return browserRef;
    },
  });
  t.after(async () => {
    try {
      await browserRef?.close();
    } catch {
      // ignore
    }
    await harness.cleanup();
    await new Promise((resolve) => fixture.close(resolve));
  });

  const started = await harness.controller.start({
    targetUrl,
    goal: "选请假类型并搜索",
  });
  const tools = createPiToolHost({
    recordingId: started.id,
    evidence: harness.evidence,
    files: harness.files,
    gate: harness.gate,
    getPiSessionId: () => harness.evidence.snapshot(started.id).piSessionId,
    getBrowser: () => browserRef,
  });
  const shot = await tools.control_in_app_browser({ action: "snapshot" });
  assert.ok(shot.controls?.some((item) => String(item.selector || "").includes("请选择请假类型")), JSON.stringify(shot.controls));
  assert.ok(shot.actions?.some((item) => item.selector === 'role=button[name="搜索"]'), JSON.stringify(shot.actions));
  const opened = await tools.control_in_app_browser({
    action: "click",
    selector: "placeholder=请选择请假类型",
  });
  assert.equal(opened.ok, true, JSON.stringify(opened));
  assert.equal(opened.chooser, true);
  assert.ok((opened.options || []).includes("事假"), JSON.stringify(opened));
  const chosen = await tools.control_in_app_browser({
    action: "choose",
    selector: "placeholder=请选择请假类型",
    text: "事假",
  });
  assert.equal(chosen.ok, true, JSON.stringify(chosen));
  assert.equal(await browserRef.page.locator("#leave-type").inputValue(), "事假");
  const searched = await tools.control_in_app_browser({
    action: "click",
    selector: 'role=button[name="搜索"]',
  });
  assert.equal(searched.ok, true, JSON.stringify(searched));
  const events = await harness.files.readEvidence(started.id);
  const piChoose = events.filter((item) => item.kind === "interaction" && item.payload?.actor === "pi");
  assert.ok(piChoose.some((item) => item.payload?.kind === "choose" && item.payload?.text === "事假"));
  const hookAsHuman = events.filter((item) => (
    item.kind === "interaction"
    && item.payload?.actor === "human"
    && /事假|请假类型/.test(`${item.payload?.text || ""} ${item.payload?.label || ""}`)
  ));
  assert.equal(hookAsHuman.length, 0, "PI 点下拉不得再记成人手");
});
