import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import os from "node:os";
import { mkdtemp, rm } from "node:fs/promises";
import { BrowserRecorder } from "../src/browser/recorder.js";
import { readJsonl } from "../src/utils.js";
import type { EvidenceEvent } from "../src/domain.js";

test("snapshots and controls component forms and iframes in one embedded browser", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-browser-"));
  const server = http.createServer((request, response) => {
    if (request.url === "/api" && request.method === "POST") {
      request.resume();
      response.setHeader("content-type", "application/json");
      response.end('{"success":true}');
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    if (request.url === "/frame") {
      response.end('<!doctype html><button id="frame-button">框架内操作</button>');
      return;
    }
    response.end(`<!doctype html><html><head><title>泛化页面</title></head><body>
      <div role="form" class="ant-form">
        <div class="ant-form-item is-required"><label>客户名称</label><input id="customer" data-field="customerName" aria-required="true" style="position:fixed;left:20px;top:100px;width:180px;height:30px"></div>
      </div>
      <div class="el-select" style="position:fixed;left:20px;top:150px;width:180px;height:36px">
        <div class="el-select__selected-item el-select__placeholder is-transparent" style="position:absolute;inset:0;pointer-events:auto">
          <span>请选择产品</span>
        </div>
        <input id="product" role="combobox" aria-label="产品" style="width:180px;height:36px">
      </div>
      <ul id="product-options" hidden>
        <li role="option">华为matebook14</li>
      </ul>
      <button id="login" onclick="fetch('/api',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username:'demo',password:'never-store-this'})})">登录</button>
      <iframe src="/frame"></iframe>
      <script>
        document.querySelector(".el-select").addEventListener("click", () => {
          setTimeout(() => { document.getElementById("product-options").hidden = false; }, 400);
        });
      </script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    const session = await recorder.start(`http://127.0.0.1:${address.port}/`, "general-page");
    await recorder.manualControl({ action: "click", x: 50, y: 115 });
    await recorder.manualControl({ action: "text", value: "手动录制客户" });
    const [firstFrame, secondFrame] = await Promise.all([recorder.preview(), recorder.preview()]);
    assert.equal(firstFrame[0], 0xff);
    assert.equal(firstFrame[1], 0xd8);
    assert.equal(firstFrame.equals(secondFrame), true);
    await recorder.control({ action: "choose", selector: "label=产品", value: "华为matebook14" });
    await recorder.control({ action: "click", selector: "#login" });
    await recorder.control({ action: "click", selector: "#frame-button" });
    await recorder.control({ action: "wait", ms: 350 });
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.equal(snapshot.title, "泛化页面");
    assert.equal(snapshot.controls.some((control: any) => control.selector === "#customer" && control.text === "手动录制客户"), true);
    assert.equal(Array.isArray(snapshot.recentUserActions), true);
    assert.equal(snapshot.recentUserActions.some((item: any) => item.name === "customerName" || item.value === "手动录制客户"), true);
    await recorder.control({ action: "click", selector: 'role=button[name="登录"]' });
    assert.equal(snapshot.frames.length, 1);
    assert.equal(snapshot.frames[0].controls.some((control: any) => control.text === "框架内操作"), true);
    await recorder.stop();
    const events = await readJsonl<EvidenceEvent>(session.eventsFile);
    const input = events.find(event => event.kind === "ui" && event.eventType === "input");
    const network = events.find(event => event.kind === "network" && event.request.url.endsWith("/api"));
    assert.equal(input?.kind, "ui");
    if (input?.kind === "ui") {
      assert.equal(input.name, "customerName");
      assert.equal(input.form?.[0]?.label, "客户名称");
      assert.equal(input.form?.[0]?.required, true);
    }
    assert.equal(network?.kind, "network");
    if (network?.kind === "network") {
      assert.equal((network.request.body as any).username, "demo");
      assert.equal((network.request.body as any).password, "[REDACTED]");
    }
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("dialog clicks stay on the form and dates do not hit the page background", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-dialog-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>弹窗页</title></head><body>
      <button id="open">新增</button>
      <table><tr><td>2</td></tr></table>
      <div class="el-overlay" id="mask" hidden style="position:fixed;inset:0;background:rgba(0,0,0,.4)">
        <div class="el-dialog" role="dialog" style="position:absolute;left:80px;top:60px;width:360px;background:#fff;padding:16px">
          <label>订单时间</label>
          <div class="el-date-editor"><input aria-label="订单时间" id="order-time"></div>
          <div class="el-picker-panel el-date-picker" id="calendar" hidden>
            <span class="el-date-table-cell__text">2</span>
          </div>
          <label>供应商</label>
          <div class="el-select">
            <input role="combobox" aria-label="供应商">
          </div>
          <ul id="supplier-options" hidden><li role="option">泉源鱼家</li></ul>
          <p id="status">open</p>
        </div>
      </div>
      <script>
        const mask = document.getElementById("mask");
        const calendar = document.getElementById("calendar");
        document.getElementById("open").onclick = () => { mask.hidden = false; };
        mask.addEventListener("click", event => {
          if (event.target === mask) {
            mask.hidden = true;
            document.getElementById("status").textContent = "closed";
          }
        });
        document.querySelector(".el-date-editor").addEventListener("click", () => { calendar.hidden = false; });
        calendar.addEventListener("click", event => {
          if (event.target.classList.contains("el-date-table-cell__text")) {
            document.getElementById("order-time").value = "2026-09-02";
            calendar.hidden = true;
          }
        });
        document.querySelector(".el-select").addEventListener("click", () => {
          document.getElementById("supplier-options").hidden = false;
        });
        document.getElementById("supplier-options").addEventListener("click", () => {
          document.getElementById("status").textContent = "chosen";
        });
      </script>
    </body></html>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "dialog-page");
    await recorder.control({ action: "click", selector: "text=新增" });
    await recorder.control({ action: "click", selector: "label=订单时间" });
    await recorder.control({ action: "click", selector: "text=2" });
    await recorder.control({ action: "choose", selector: "label=供应商", value: "泉源鱼家" });
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.equal(snapshot.controls.some((control: any) => control.selector === "#order-time" && control.text === "2026-09-02"), true);
    assert.equal(snapshot.text.includes("closed"), false);
    await assert.rejects(
      () => recorder.control({ action: "click", selector: "#mask" }),
      /modal mask|behind an open dialog/
    );
    await recorder.stop();
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});
