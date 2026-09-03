import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import os from "node:os";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { BrowserRecorder } from "../src/browser/recorder.js";
import { SNAPSHOT_IN_PAGE } from "../src/browser/page-script.js";
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
    assert.equal(Array.isArray(snapshot.recordedManualSteps), true);
    assert.equal(snapshot.recordedManualSteps.some((item: any) => item.action === "fill" && String(item.value).includes("手动录制客户")), true);
    await recorder.control({ action: "click", selector: 'role=button[name="登录"]' });
    assert.equal(snapshot.frames.length, 1);
    assert.equal(snapshot.frames[0].controls.some((control: any) => control.text === "框架内操作"), true);
    await recorder.stop();
    assert.ok(session.manualStepsFile);
    const manualGuide = await readFile(session.manualStepsFile!, "utf8");
    assert.match(manualGuide, /手动录制步骤/);
    assert.match(manualGuide, /手动录制客户|客户名称/);
    const events = await readJsonl<EvidenceEvent>(session.eventsFile);
    const pageInventory = events.find(event => event.kind === "ui" && event.eventType === "snapshot");
    assert.equal(pageInventory?.kind, "ui");
    if (pageInventory?.kind === "ui") {
      assert.equal(pageInventory.form?.some(field => field.label === "客户名称" || field.name === "customerName"), true);
    }
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

test("exercise-form fills every visible field except upload on mixed kits", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-form-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>混合表单</title></head><body>
      <form class="el-form" id="search">
        <div class="el-form-item is-required">
          <label class="el-form-item__label">客户名称</label>
          <input id="customer-name">
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">状态</label>
          <div class="el-select">
            <div class="el-select__wrapper"><span class="el-select__placeholder is-transparent">请选择状态</span></div>
            <input role="combobox" placeholder="请选择状态">
          </div>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">开始日期</label>
          <div class="el-date-editor"><input placeholder="开始日期"></div>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">附件</label>
          <div class="el-upload"><input type="file"></div>
        </div>
      </form>
      <ul id="status-options" hidden>
        <li class="el-select-dropdown__item">未审核</li>
        <li class="el-select-dropdown__item">已审核</li>
      </ul>
      <script>
        document.querySelector(".el-select").addEventListener("click", () => {
          document.getElementById("status-options").hidden = false;
        });
        document.getElementById("status-options").addEventListener("click", event => {
          const item = event.target.closest(".el-select-dropdown__item");
          if (!item) return;
          document.querySelector(".el-select__placeholder").classList.remove("is-transparent");
          document.querySelector(".el-select__placeholder").textContent = item.textContent;
          document.querySelector('[role="combobox"]').value = item.textContent;
          document.getElementById("status-options").hidden = true;
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "mixed-form");
    const before: any = await recorder.control({ action: "snapshot" });
    assert.equal(before.todoFields.some((field: any) => field.label === "客户名称"), true);
    assert.equal(before.todoFields.some((field: any) => field.label === "状态"), true);
    assert.equal(before.todoFields.some((field: any) => field.label === "开始日期"), true);
    assert.equal(before.todoFields.some((field: any) => field.label === "附件"), false);
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields));
    assert.equal(result.todoCount, 0);
    assert.equal(result.formFields.find((field: any) => field.label === "附件")?.skip, true);
    assert.equal(result.formFields.find((field: any) => field.label === "状态")?.filled, true);
    assert.match(String(result.formFields.find((field: any) => field.label === "客户名称")?.value || ""), /样例/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("dropdown choose does not click sidebar or pagination and keeps filled values", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-nav-"));
  const server = http.createServer((request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    if (request.url === "/other") {
      response.end("<!doctype html><title>已跳走</title><p>navigated</p>");
      return;
    }
    response.end(`<!doctype html><html><head><title>保字段</title></head><body>
      <nav class="el-menu"><a class="el-menu-item" href="/other">未审核</a></nav>
      <table><tr><td><a href="/other">2</a></td></tr></table>
      <div class="arco-spin" style="position:fixed;left:0;top:0;width:4px;height:4px"></div>
      <label>备注</label><input id="remark" value="已填写">
      <label>状态</label>
      <div class="el-select"><input role="combobox" aria-label="状态"></div>
      <ul class="el-select-dropdown" hidden><li class="el-select-dropdown__item">未审核</li></ul>
      <script>
        document.querySelector(".el-select").addEventListener("click", () => {
          document.querySelector(".el-select-dropdown").hidden = false;
        });
        document.querySelector(".el-select-dropdown").addEventListener("click", () => {
          document.querySelector('[role="combobox"]').value = "未审核";
          document.querySelector(".el-select-dropdown").hidden = true;
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "keep-fields");
    await recorder.control({ action: "fill", selector: "label=备注", value: "已填写" });
    const started = Date.now();
    await recorder.control({ action: "choose", selector: "label=状态", value: "未审核" });
    assert.equal(Date.now() - started < 2_500, true);
    await assert.rejects(() => recorder.control({ action: "click", selector: "text=2" }), /date field first|bare day number/);
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.equal(snapshot.url.includes("/other"), false);
    assert.equal(snapshot.text.includes("navigated"), false);
    assert.equal(snapshot.controls.some((control: any) => control.selector === "#remark" && control.value === "已填写"), true);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("element plus readonly selects and date dialogs fill without leftover poppers or empty snapshots", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-ep-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>点狮全业务管理平台 - 采购订单</title>
      <style>
        body{font-family:sans-serif}
        .el-select-dropdown,.el-picker-panel{position:fixed;left:80px;top:180px;z-index:4000;background:#fff;border:1px solid #ccc;padding:8px}
        .el-overlay{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:2000}
        .el-dialog{position:absolute;left:60px;top:30px;width:680px;background:#fff;padding:16px;z-index:2001}
      </style></head><body>
      <h1>采购订单</h1>
      <form class="el-form" id="search">
        <div class="el-form-item"><label class="el-form-item__label">订单单号</label><input id="no"></div>
        <div class="el-form-item"><label class="el-form-item__label">产品</label>
          <div class="el-select" data-options="华为matebook14,键盘">
            <div class="el-select__wrapper"><div class="el-select__selection">
              <div class="el-select__selected-item el-select__placeholder"><span>请选择</span></div>
              <div class="el-select__selected-item el-select__input-wrapper"><input class="el-select__input" role="combobox" readonly></div>
            </div></div>
          </div>
        </div>
        <div class="el-form-item"><label class="el-form-item__label">订单时间</label>
          <div class="el-date-editor"><input class="el-input__inner" readonly placeholder="订单时间"></div>
        </div>
        <div class="el-form-item"><label class="el-form-item__label">供应商</label>
          <div class="el-select" data-options="泉源鱼家,丽丽">
            <div class="el-select__wrapper"><div class="el-select__selection">
              <div class="el-select__selected-item el-select__placeholder"><span>请选择</span></div>
              <div class="el-select__selected-item el-select__input-wrapper"><input class="el-select__input" role="combobox" readonly></div>
            </div></div>
          </div>
        </div>
        <div class="el-form-item"><label class="el-form-item__label">状态</label>
          <div class="el-select" data-options="未审核,已审核">
            <div class="el-select__wrapper"><div class="el-select__selection">
              <div class="el-select__selected-item el-select__placeholder"><span>请选择</span></div>
              <div class="el-select__selected-item el-select__input-wrapper"><input class="el-select__input" role="combobox" readonly></div>
            </div></div>
          </div>
        </div>
        <div class="el-form-item"><label class="el-form-item__label">备注</label><input id="remark"></div>
        <div class="el-form-item"><label class="el-form-item__label">入库数量</label><input id="in-qty" disabled></div>
      </form>
      <button id="search-btn">搜索</button>
      <button id="create">新增</button>
      <div class="el-overlay" id="mask" hidden>
        <div class="el-dialog" role="dialog">
          <div class="el-form">
            <div class="el-form-item"><label class="el-form-item__label">订单单号</label><input id="new-no" disabled value="自动生成"></div>
            <div class="el-form-item is-required"><label class="el-form-item__label">订单时间</label>
              <div class="el-date-editor"><input class="el-input__inner" readonly placeholder="订单时间"></div>
            </div>
            <div class="el-form-item is-required"><label class="el-form-item__label">供应商</label>
              <div class="el-select" data-options="泉源鱼家,丽丽">
                <div class="el-select__wrapper"><div class="el-select__selection">
                  <div class="el-select__selected-item el-select__placeholder"><span>请选择</span></div>
                  <div class="el-select__selected-item el-select__input-wrapper"><input class="el-select__input" role="combobox" readonly></div>
                </div></div>
              </div>
            </div>
            <div class="el-form-item"><label class="el-form-item__label">结算账户</label>
              <div class="el-select" data-options="公司基本户,现金">
                <div class="el-select__wrapper"><div class="el-select__selection">
                  <div class="el-select__selected-item el-select__placeholder"><span>请选择</span></div>
                  <div class="el-select__selected-item el-select__input-wrapper"><input class="el-select__input" role="combobox" readonly></div>
                </div></div>
              </div>
            </div>
            <div class="el-form-item"><label class="el-form-item__label">备注</label><textarea id="new-remark"></textarea></div>
            <div class="el-form-item"><label class="el-form-item__label">附件</label><div class="el-upload"><input type="file"></div></div>
          </div>
          <div class="el-table">
            <div class="el-table__header-wrapper"><table class="el-table__header"><thead><tr>
              <th class="el-table__cell">产品名称</th><th class="el-table__cell">数量</th>
            </tr></thead></table></div>
            <div class="el-table__body-wrapper"><table class="el-table__body"><tbody><tr class="el-table__row">
              <td class="el-table__cell">
                <div class="el-select" data-options="华为matebook14,键盘">
                  <div class="el-select__wrapper is-filterable"><div class="el-select__selection">
                    <div class="el-select__selected-item el-select__placeholder"><span>请选择产品</span></div>
                    <div class="el-select__selected-item el-select__input-wrapper">
                      <input class="el-select__input is-default" role="combobox" placeholder="请选择产品">
                    </div>
                  </div></div>
                </div>
              </td>
              <td class="el-table__cell"><input id="qty"></td>
            </tr></tbody></table></div>
          </div>
          <button id="ok">确定</button>
        </div>
      </div>
      <div id="poppers"></div>
      <script>
        let drop, panel;
        const closeDrop = () => { drop?.remove(); drop = null; document.querySelectorAll(".el-select__wrapper").forEach(w => w.classList.remove("is-focused")); };
        const closePanel = () => { panel?.remove(); panel = null; };
        document.querySelectorAll(".el-select__input").forEach(input => {
          input.addEventListener("click", event => event.stopPropagation());
        });
        document.querySelectorAll(".el-select").forEach(select => {
          select.addEventListener("click", event => {
            event.stopPropagation();
            if (drop && select.querySelector(".is-focused")) { closeDrop(); return; }
            closeDrop(); closePanel();
            const box = document.createElement("div");
            box.className = "el-select-dropdown";
            box.innerHTML = "<ul>" + (select.dataset.options || "").split(",").map(name =>
              '<li class="el-select-dropdown__item" role="option">' + name + "</li>").join("") + "</ul>";
            document.getElementById("poppers").appendChild(box);
            drop = box;
            select.querySelector(".el-select__wrapper").classList.add("is-focused");
            box.addEventListener("click", clickEvent => {
              const item = clickEvent.target.closest(".el-select-dropdown__item");
              if (!item) return;
              const placeholder = select.querySelector(".el-select__placeholder");
              placeholder.classList.add("is-transparent");
              placeholder.querySelector("span").textContent = item.textContent;
              closeDrop();
            });
          });
        });
        document.querySelectorAll(".el-date-editor").forEach(editor => {
          editor.addEventListener("click", event => {
            event.stopPropagation();
            closeDrop(); closePanel();
            const box = document.createElement("div");
            box.className = "el-picker-panel el-date-picker";
            box.setAttribute("role", "dialog");
            box.innerHTML = '<span class="el-date-table-cell__text today">2</span>';
            document.getElementById("poppers").appendChild(box);
            panel = box;
            box.addEventListener("click", clickEvent => {
              if (!clickEvent.target.classList.contains("el-date-table-cell__text")) return;
              editor.querySelector("input").value = "2026-09-02";
              closePanel();
            });
          });
        });
        document.getElementById("create").onclick = () => { document.getElementById("mask").hidden = false; };
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "purchase-order");
    const before: any = await recorder.control({ action: "snapshot" });
    assert.equal(before.scope, "page");
    assert.equal(before.formFields.find((field: any) => field.label === "状态")?.disabled, false);
    assert.equal(before.formFields.find((field: any) => field.label === "状态")?.kind, "select");
    assert.equal(before.formFields.find((field: any) => field.label === "入库数量")?.disabled, true);
    assert.equal(before.todoFields.some((field: any) => field.label === "产品"), true);
    assert.equal(before.todoFields.some((field: any) => field.label === "状态"), true);
    const search: any = await recorder.control({ action: "exercise-form" });
    assert.equal(search.ok, true, JSON.stringify(search.failed || search.todoFields));
    assert.equal(search.scope, "page");
    assert.equal(search.todoCount, 0);
    assert.match(String(search.formFields.find((field: any) => field.label === "产品")?.value || ""), /华为matebook14|键盘/);
    assert.match(String(search.formFields.find((field: any) => field.label === "供应商")?.value || ""), /泉源鱼家|丽丽/);
    assert.match(String(search.formFields.find((field: any) => field.label === "状态")?.value || ""), /审核/);
    assert.match(String(search.formFields.find((field: any) => field.label === "订单时间")?.value || ""), /\d{4}-\d{2}-\d{2}/);
    await recorder.control({ action: "click", selector: "text=新增" });
    const opened: any = await recorder.control({ action: "snapshot" });
    assert.equal(opened.scope, "dialog");
    assert.equal(opened.todoFields.some((field: any) => field.label === "产品名称"), true);
    assert.equal(opened.todoFields.some((field: any) => field.label === "数量"), true);
    const dialog: any = await recorder.control({ action: "exercise-form" });
    assert.equal(dialog.ok, true, JSON.stringify(dialog.failed || dialog.todoFields));
    assert.equal(dialog.scope, "dialog");
    assert.equal(dialog.todoCount, 0);
    assert.match(String(dialog.formFields.find((field: any) => field.label === "供应商")?.value || ""), /泉源鱼家|丽丽/);
    assert.match(String(dialog.formFields.find((field: any) => field.label === "结算账户")?.value || ""), /公司基本户|现金/);
    assert.match(String(dialog.formFields.find((field: any) => field.label === "产品名称")?.value || ""), /华为matebook14|键盘/);
    assert.equal(dialog.formFields.find((field: any) => field.label === "附件")?.skip, true);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("required number 0 is empty and date picker chrome is not a form field", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-zero-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>时长表单</title></head><body>
      <form class="el-form">
        <div class="el-form-item is-required">
          <label class="el-form-item__label">请假天数</label>
          <input id="days" type="number" value="0" required>
        </div>
      </form>
      <div class="el-picker-panel" style="display:block;width:240px;height:120px">
        <div class="el-form-item">
          <label class="el-form-item__label">选择日期</label>
          <input value="2026-09-02">
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">选择时间</label>
          <input value="00:00:00">
        </div>
      </div>
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "zero-days");
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.equal(snapshot.todoFields.some((field: any) => field.label === "请假天数"), true);
    assert.equal(snapshot.formFields.some((field: any) => field.label === "选择日期"), false);
    assert.equal(snapshot.formFields.some((field: any) => field.label === "选择时间"), false);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form keeps later dates after earlier ones and submit-form repairs zero duration", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-span-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/leave/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0,"data":{"id":1}}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>发起申请</title></head><body>
      <form class="el-form" id="create">
        <div class="el-form-item is-required">
          <label class="el-form-item__label">开始时间</label>
          <div class="el-date-editor el-date-editor--datetime"><input id="start" placeholder="开始时间"></div>
        </div>
        <div class="el-form-item is-required">
          <label class="el-form-item__label">结束时间</label>
          <div class="el-date-editor el-date-editor--datetime"><input id="end" placeholder="结束时间"></div>
        </div>
        <div class="el-form-item is-required">
          <label class="el-form-item__label">请假天数</label>
          <input id="days" type="number" value="0" required>
        </div>
        <div class="el-form-item is-required">
          <label class="el-form-item__label">原因</label>
          <textarea id="reason"></textarea>
        </div>
        <button type="submit">提交</button>
      </form>
      <div id="err" class="el-form-item__error" hidden></div>
      <script>
        const start = document.getElementById("start");
        const end = document.getElementById("end");
        const days = document.getElementById("days");
        const err = document.getElementById("err");
        const recalc = () => {
          if (!start.value || !end.value) return;
          const diff = (new Date(end.value) - new Date(start.value)) / 86400000;
          days.value = String(Math.max(0, diff));
        };
        start.addEventListener("change", recalc);
        end.addEventListener("change", recalc);
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          recalc();
          if (Number(days.value) <= 0) {
            err.hidden = false;
            err.textContent = "天数必须大于 0";
            return;
          }
          err.hidden = true;
          fetch("/api/leave/create", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ startTime: start.value, endTime: end.value, days: Number(days.value), reason: document.getElementById("reason").value })
          });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "span-form");
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    const start = parseFieldInstant(result.formFields.find((field: any) => field.label === "开始时间")?.value);
    const end = parseFieldInstant(result.formFields.find((field: any) => field.label === "结束时间")?.value);
    assert.ok(start && end && end > start, JSON.stringify(result.formFields));
    assert.ok(Number(result.formFields.find((field: any) => field.label === "请假天数")?.value) > 0, JSON.stringify(result.formFields));
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(created.length, 1, JSON.stringify({ created, submitted }));
    const payload = created[0] as { days?: number; startTime?: string; endTime?: string };
    assert.ok(Number(payload.days) > 0, JSON.stringify(payload));
    assert.ok(new Date(String(payload.endTime)).getTime() > new Date(String(payload.startTime)).getTime(), JSON.stringify(payload));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("native pages fill range dates, commit change events, and submit only in the active scope", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-generic-"));
  const searched: unknown[] = [];
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/search" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        searched.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0,"data":[]}');
      });
      return;
    }
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0,"data":{"id":1}}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>通用表单</title></head><body>
      <form id="query">
        <div class="form-item">
          <label>客户</label>
          <input id="customer" name="customer">
        </div>
        <div class="form-item">
          <label>下单时间</label>
          <div class="picker-range">
            <input id="q-start" placeholder="开始日期">
            <input id="q-end" placeholder="结束日期">
          </div>
        </div>
        <div class="form-item" prop="ignoredVueProp">
          <label>编码</label>
          <input id="code" data-field="bizCode">
        </div>
        <button type="submit" id="search">搜索</button>
      </form>
      <button type="button" id="open">新增</button>
      <div id="dlg" role="dialog" hidden style="position:fixed;inset:10px;background:#fff;z-index:20;padding:16px">
        <form id="create">
          <div class="form-item">
            <label>名称</label>
            <input id="title" name="title">
          </div>
          <div class="form-item">
            <label>开始日期</label>
            <input id="c-start" type="date" required>
          </div>
          <div class="form-item">
            <label>结束日期</label>
            <input id="c-end" type="date" required>
          </div>
          <div class="form-item is-required">
            <label>数量</label>
            <input id="qty" type="number" required value="0">
          </div>
          <button type="button" id="ok">确定</button>
        </form>
      </div>
      <script>
        window.__bound = {};
        document.getElementById("customer").addEventListener("change", event => {
          window.__bound.customer = event.target.value;
        });
        const recalc = () => {
          const start = document.getElementById("c-start").value;
          const end = document.getElementById("c-end").value;
          if (!start || !end) return;
          document.getElementById("qty").value = String(Math.max(0, (new Date(end) - new Date(start)) / 86400000));
        };
        document.getElementById("c-start").addEventListener("change", recalc);
        document.getElementById("c-end").addEventListener("change", recalc);
        document.getElementById("query").addEventListener("submit", event => {
          event.preventDefault();
          fetch("/api/search", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              customer: window.__bound.customer || "",
              start: document.getElementById("q-start").value,
              end: document.getElementById("q-end").value,
              code: document.getElementById("code").value
            })
          });
        });
        document.getElementById("open").addEventListener("click", () => {
          document.getElementById("dlg").hidden = false;
        });
        document.getElementById("ok").addEventListener("click", () => {
          recalc();
          fetch("/api/create", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              title: document.getElementById("title").value,
              start: document.getElementById("c-start").value,
              end: document.getElementById("c-end").value,
              qty: Number(document.getElementById("qty").value)
            })
          });
          document.getElementById("dlg").hidden = true;
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "generic-form");
    const listed: any = await recorder.control({ action: "snapshot" });
    assert.equal(listed.scope, "page");
    assert.equal(listed.formFields.find((field: any) => field.label === "编码")?.name, "bizCode");
    assert.notEqual(listed.formFields.find((field: any) => field.label === "编码")?.name, "ignoredVueProp");
    const query: any = await recorder.control({ action: "exercise-form" });
    assert.equal(query.ok, true, JSON.stringify(query.failed || query.todoFields || query.formFields));
    const start = String(query.formFields.find((field: any) => field.rangeIndex === 0 || field.label === "开始日期")?.value || "");
    const end = String(query.formFields.find((field: any) => field.rangeIndex === 1 || field.label === "结束日期")?.value || "");
    assert.ok(start && end && start !== end, JSON.stringify(query.formFields));
    const searchedResult: any = await recorder.control({ action: "submit-form" });
    assert.equal(searchedResult.ok, true, JSON.stringify(searchedResult));
    assert.equal(searchedResult.submitted, "搜索");
    assert.equal(searched.length, 1, JSON.stringify({ searched, searchedResult }));
    const queryBody = searched[0] as { customer?: string; start?: string; end?: string };
    assert.match(String(queryBody.customer || ""), /样例/);
    assert.ok(queryBody.start && queryBody.end && queryBody.start !== queryBody.end, JSON.stringify(queryBody));
    await recorder.control({ action: "click", selector: "text=新增" });
    const opened: any = await recorder.control({ action: "snapshot" });
    assert.equal(opened.scope, "dialog");
    assert.equal(opened.controls.some((control: any) => /搜索/.test(String(control.text || ""))), false);
    const dialog: any = await recorder.control({ action: "exercise-form" });
    assert.equal(dialog.ok, true, JSON.stringify(dialog.failed || dialog.todoFields || dialog.formFields));
    const dialogStart = parseFieldInstant(dialog.formFields.find((field: any) => field.label === "开始日期")?.value);
    const dialogEnd = parseFieldInstant(dialog.formFields.find((field: any) => field.label === "结束日期")?.value);
    assert.ok(dialogStart && dialogEnd && dialogEnd > dialogStart, JSON.stringify(dialog.formFields));
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(submitted.submitted, "确定");
    assert.equal(created.length, 1, JSON.stringify({ created, submitted }));
    const payload = created[0] as { qty?: number; start?: string; end?: string };
    assert.ok(Number(payload.qty) > 0, JSON.stringify(payload));
    assert.ok(new Date(String(payload.end)).getTime() > new Date(String(payload.start)).getTime(), JSON.stringify(payload));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form fills sibling inputs and empty picker slots, not just the first control", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-slots-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0,"data":{"id":1}}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>复合表单</title></head><body>
      <form class="el-form" id="create">
        <div class="el-form-item is-required">
          <label class="el-form-item__label">归属</label>
          <input id="code" placeholder="编号">
          <input id="name" placeholder="名称">
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">对象</label>
          <div class="el-select">
            <input id="object" role="combobox" readonly aria-label="对象" placeholder="请选择">
            <span class="el-select__placeholder">请选择</span>
          </div>
        </div>
        <div class="el-form-item is-required">
          <label class="el-form-item__label">说明</label>
          <textarea id="memo" placeholder="请输入说明"></textarea>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">附件</label>
          <div class="el-upload"><button type="button">上传附件</button><input type="file"></div>
        </div>
        <button type="submit" id="save">提交</button>
      </form>
      <aside class="process-panel">
        <div class="process-node">
          <div class="node-title">复核</div>
          <span class="user-tag">已指定甲</span>
        </div>
        <div class="process-node">
          <div class="node-title">会签</div>
          <button type="button" class="add-user" aria-label="选择会签">+</button>
        </div>
      </aside>
      <div id="picker" role="dialog" hidden style="position:fixed;inset:20px;background:#fff;z-index:30;padding:16px">
        <table><tbody><tr data-user="乙"><td>乙</td></tr><tr data-user="丙"><td>丙</td></tr></tbody></table>
        <button type="button" id="pick-ok">确定</button>
      </div>
      <ul id="object-options" class="el-select-dropdown" hidden>
        <li role="option">甲对象</li>
        <li role="option">乙对象</li>
      </ul>
      <script>
        const bound = { code: "", name: "", memo: "", object: "", assignee: "" };
        const bind = (id, key) => {
          const node = document.getElementById(id);
          Object.defineProperty(node, "value", {
            get() { return this.getAttribute("data-bound") || ""; },
            set(next) { this.setAttribute("data-bound", String(next)); bound[key] = String(next); }
          });
          node.addEventListener("input", () => { bound[key] = node.value; });
          node.addEventListener("change", () => { bound[key] = node.value; });
        };
        bind("code", "code");
        bind("name", "name");
        bind("memo", "memo");
        document.querySelector(".el-select").addEventListener("click", () => {
          document.getElementById("object-options").hidden = false;
        });
        document.getElementById("object-options").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          bound.object = option.textContent;
          document.querySelector(".el-select__placeholder").textContent = option.textContent;
          document.getElementById("object-options").hidden = true;
        });
        document.querySelector(".add-user").addEventListener("click", () => {
          document.getElementById("picker").hidden = false;
        });
        document.querySelector("#picker table").addEventListener("click", event => {
          const row = event.target.closest("tr");
          if (row) bound.assignee = row.getAttribute("data-user");
        });
        document.getElementById("pick-ok").addEventListener("click", () => {
          if (bound.assignee) {
            const node = document.querySelectorAll(".process-node")[1];
            const tag = document.createElement("span");
            tag.className = "user-tag";
            tag.textContent = bound.assignee;
            node.querySelector(".add-user")?.remove();
            node.appendChild(tag);
          }
          document.getElementById("picker").hidden = true;
        });
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          fetch("/api/create", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(bound)
          });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "slot-form");
    const before: any = await recorder.control({ action: "snapshot" });
    const labels = (before.todoFields || []).map((field: any) => field.label);
    assert.equal(labels.includes("编号"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.includes("名称"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.includes("说明"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.includes("对象"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /会签/.test(label)), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /复核|附件/.test(label)), false, JSON.stringify(before.todoFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    assert.equal(result.todoCount, 0, JSON.stringify(result.todoFields));
    const code = String(result.formFields.find((field: any) => field.label === "编号")?.value || "");
    const name = String(result.formFields.find((field: any) => field.label === "名称")?.value || "");
    assert.match(code, /样例/);
    assert.match(name, /样例/);
    assert.notEqual(code, name);
    assert.match(String(result.formFields.find((field: any) => field.label === "对象")?.value || ""), /对象/);
    assert.match(String(result.formFields.find((field: any) => /会签/.test(String(field.label || "")))?.value || ""), /乙|丙/);
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(created.length, 1, JSON.stringify({ created, submitted }));
    const payload = created[0] as { code?: string; name?: string; memo?: string; object?: string; assignee?: string };
    assert.match(String(payload.code || ""), /样例/);
    assert.match(String(payload.name || ""), /样例/);
    assert.notEqual(payload.code, payload.name);
    assert.match(String(payload.memo || ""), /样例/);
    assert.match(String(payload.object || ""), /对象/);
    assert.match(String(payload.assignee || ""), /乙|丙/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form fills a shared-label select plus prompt input and only accepts a real write request", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-shared-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0,"data":{"id":2}}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>共享组表单</title></head><body>
      <form class="el-form" id="create">
        <div class="el-form-item is-required">
          <label class="el-form-item__label">所属</label>
          <div class="el-select">
            <div class="el-select__wrapper">
              <span class="el-select__placeholder">请选择</span>
              <input id="owner-select" role="combobox" readonly placeholder="请选择">
            </div>
          </div>
          <input id="project-name" placeholder="请输入项目名称">
        </div>
        <div class="el-form-item is-required">
          <label class="el-form-item__label">说明</label>
          <textarea id="memo" placeholder="请输入说明"></textarea>
        </div>
        <button type="submit" id="save">提交</button>
      </form>
      <aside class="process-panel">
        <div class="process-node">
          <div class="node-title">复核</div>
          <span class="user-tag">已指定甲</span>
        </div>
        <div class="process-node">
          <div class="node-title">会签</div>
          <button type="button" class="add-user" aria-label="选择会签">+</button>
        </div>
      </aside>
      <ul id="owner-options" class="el-select-dropdown" hidden>
        <li role="option">甲项目</li>
        <li role="option">乙项目</li>
      </ul>
      <div id="picker" role="dialog" hidden style="position:fixed;inset:20px;background:#fff;z-index:30;padding:16px">
        <table><tbody><tr data-user="乙"><td>乙</td></tr></tbody></table>
        <button type="button" id="pick-ok">确定</button>
      </div>
      <script>
        const bound = { owner: "", projectName: "", memo: "", assignee: "" };
        document.querySelector(".el-select").addEventListener("click", () => {
          document.getElementById("owner-options").hidden = false;
        });
        document.getElementById("owner-options").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          bound.owner = option.textContent;
          document.querySelector(".el-select__placeholder").textContent = option.textContent;
          document.getElementById("owner-select").value = option.textContent;
          document.getElementById("owner-options").hidden = true;
        });
        document.getElementById("project-name").addEventListener("input", event => { bound.projectName = event.target.value; });
        document.getElementById("memo").addEventListener("input", event => { bound.memo = event.target.value; });
        document.querySelector(".add-user").addEventListener("click", () => {
          document.getElementById("picker").hidden = false;
        });
        document.querySelector("#picker table").addEventListener("click", event => {
          const row = event.target.closest("tr");
          if (row) bound.assignee = row.getAttribute("data-user");
        });
        document.getElementById("pick-ok").addEventListener("click", () => {
          if (bound.assignee) {
            const node = document.querySelectorAll(".process-node")[1];
            const tag = document.createElement("span");
            tag.className = "user-tag";
            tag.textContent = bound.assignee;
            node.querySelector(".add-user")?.remove();
            node.appendChild(tag);
          }
          document.getElementById("picker").hidden = true;
        });
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          bound.projectName = bound.projectName || document.getElementById("project-name").value;
          bound.memo = bound.memo || document.getElementById("memo").value;
          if (!bound.owner || !bound.projectName || !bound.assignee) {
            const err = document.createElement("div");
            err.className = "el-form-item__error";
            err.textContent = "请补全必填项";
            document.getElementById("create").appendChild(err);
            return;
          }
          fetch("/api/create", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(bound)
          });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "shared-group");
    const before: any = await recorder.control({ action: "snapshot" });
    const labels = (before.todoFields || []).map((field: any) => String(field.label || ""));
    assert.equal(labels.includes("请输入项目名称"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /所属/.test(label)), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /会签/.test(label)), true, JSON.stringify(before.todoFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    assert.equal(result.todoCount, 0, JSON.stringify(result.todoFields));
    const owner = String((result.formFields || []).find((field: any) => /所属/.test(String(field.label || "")))?.value || "");
    const project = String((result.formFields || []).find((field: any) => field.label === "请输入项目名称")?.value || "");
    assert.match(owner, /项目/);
    assert.equal(/^样例-/.test(owner), false, owner);
    assert.match(project, /样例/);
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(created.length, 1, JSON.stringify({ created, submitted }));
    const payload = created[0] as { owner?: string; projectName?: string; memo?: string; assignee?: string };
    assert.match(String(payload.owner || ""), /项目/);
    assert.match(String(payload.projectName || ""), /样例/);
    assert.match(String(payload.memo || ""), /样例/);
    assert.match(String(payload.assignee || ""), /乙/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form covers kit-agnostic choosers, nearby labels, shadow fields and hidden tabs", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-anykit-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0}');
      });
      return;
    }
    if (request.url === "/inner") {
      response.setHeader("content-type", "text/html; charset=utf-8");
      response.end("<!doctype html><html><body><span>来源</span><input id='source'></body></html>");
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>任意套件</title></head><body>
      <form id="create">
        <div><span>客户</span><input id="customer"></div>
        <div><span>归属</span><input id="owner" readonly placeholder="请选择" aria-haspopup="dialog"></div>
        <div id="shadow-host"></div>
        <div>
          <span>优先级</span>
          <label><input type="radio" name="prio" value="高">高</label>
          <label><input type="radio" name="prio" value="低">低</label>
        </div>
        <div>
          <span>地区</span>
          <input id="region" role="combobox" readonly aria-label="地区">
        </div>
        <div role="tablist">
          <button type="button" role="tab" id="tab-base" aria-selected="true">基本</button>
          <button type="button" role="tab" id="tab-more" aria-selected="false">更多</button>
        </div>
        <div id="panel-more" hidden><span>备注</span><textarea id="note"></textarea></div>
        <div class="card">
          <div class="heading">经办</div>
          <button type="button" aria-haspopup="dialog">+</button>
        </div>
        <label>附件<input type="file"></label>
        <iframe src="/inner" style="width:240px;height:80px;border:0"></iframe>
        <button type="submit">提交</button>
      </form>
      <ul id="region-drop" role="listbox" hidden>
        <li role="option" id="east">东部</li>
      </ul>
      <ul id="region-drop-2" role="listbox" hidden>
        <li role="option">市区</li>
      </ul>
      <div id="owner-dlg" role="dialog" hidden style="position:fixed;inset:10px;background:#fff;z-index:20">
        <table><tbody><tr data-v="组A"><td>组A</td></tr></tbody></table>
        <button type="button" id="owner-ok">确定</button>
      </div>
      <div id="agent-dlg" role="dialog" hidden style="position:fixed;inset:10px;background:#fff;z-index:21">
        <ul><li role="option">经办甲</li></ul>
        <button type="button" id="agent-ok">确定</button>
      </div>
      <script>
        const bound = { customer: "", owner: "", secret: "", prio: "", region: "", note: "", agent: "" };
        const host = document.getElementById("shadow-host");
        const shadow = host.attachShadow({ mode: "open" });
        shadow.innerHTML = '<span>密级</span><input id="secret">';
        shadow.getElementById("secret").addEventListener("input", event => { bound.secret = event.target.value; });
        document.getElementById("customer").addEventListener("change", event => { bound.customer = event.target.value; });
        document.getElementById("owner").addEventListener("click", () => { document.getElementById("owner-dlg").hidden = false; });
        document.querySelector("#owner-dlg table").addEventListener("click", event => {
          const row = event.target.closest("tr");
          if (row) bound.owner = row.getAttribute("data-v");
        });
        document.getElementById("owner-ok").onclick = () => {
          document.getElementById("owner").value = bound.owner;
          document.getElementById("owner-dlg").hidden = true;
        };
        document.querySelectorAll("input[name=prio]").forEach(node => node.addEventListener("change", () => { bound.prio = node.value; }));
        document.getElementById("region").addEventListener("click", () => { document.getElementById("region-drop").hidden = false; });
        document.getElementById("east").onclick = () => {
          document.getElementById("region-drop").hidden = true;
          document.getElementById("region-drop-2").hidden = false;
        };
        document.getElementById("region-drop-2").onclick = event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          bound.region = "东部/" + option.textContent;
          document.getElementById("region").value = bound.region;
          document.getElementById("region-drop-2").hidden = true;
        };
        document.getElementById("tab-more").onclick = () => {
          document.getElementById("tab-more").setAttribute("aria-selected", "true");
          document.getElementById("tab-base").setAttribute("aria-selected", "false");
          document.getElementById("panel-more").hidden = false;
        };
        document.getElementById("note").addEventListener("change", event => { bound.note = event.target.value; });
        document.querySelector(".card button").onclick = () => { document.getElementById("agent-dlg").hidden = false; };
        document.querySelector("#agent-dlg [role=option]").onclick = () => { bound.agent = "经办甲"; };
        document.getElementById("agent-ok").onclick = () => {
          const tag = document.createElement("span");
          tag.className = "selected-tag";
          tag.textContent = bound.agent;
          document.querySelector(".card button")?.replaceWith(tag);
          document.getElementById("agent-dlg").hidden = true;
        };
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          bound.customer = bound.customer || document.getElementById("customer").value;
          bound.note = bound.note || document.getElementById("note").value;
          const frame = document.querySelector("iframe");
          const source = frame && frame.contentDocument && frame.contentDocument.getElementById("source");
          bound.source = source ? source.value : "";
          fetch("/api/create", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(bound) });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "any-kit");
    const before: any = await recorder.control({ action: "snapshot" });
    const labels = (before.todoFields || []).map((field: any) => String(field.label || ""));
    assert.equal(labels.includes("客户"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.includes("归属"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.includes("密级"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.includes("来源"), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /经办/.test(label)), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /附件/.test(label)), false, JSON.stringify(before.todoFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(created.length, 1, JSON.stringify({ created, submitted }));
    const payload = created[0] as Record<string, string>;
    assert.match(String(payload.customer || ""), /样例/);
    assert.match(String(payload.secret || ""), /样例/);
    assert.match(String(payload.source || ""), /样例/);
    assert.equal(payload.owner, "组A");
    assert.ok(payload.prio === "高" || payload.prio === "低", JSON.stringify(payload));
    assert.match(String(payload.region || ""), /市区/);
    assert.match(String(payload.note || ""), /样例/);
    assert.equal(payload.agent, "经办甲");
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("form contract: unique locate, chooser is not a typed sample, submit needs a write request", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-contract-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0,"data":{"id":3}}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>合同页</title></head><body>
      <form class="el-form" id="create">
        <div class="el-form-item is-required">
          <label class="el-form-item__label">名称</label>
          <input id="name-a" placeholder="请输入名称甲">
        </div>
        <div class="el-form-item is-required">
          <label class="el-form-item__label">名称</label>
          <input id="name-b" placeholder="请输入名称乙">
        </div>
        <div class="el-form-item is-required">
          <label class="el-form-item__label">状态</label>
          <div class="el-select">
            <div class="el-select__wrapper">
              <span class="el-select__placeholder">请选择</span>
              <input id="status" role="combobox" readonly placeholder="请选择">
            </div>
          </div>
        </div>
        <button type="submit" id="save">提交</button>
      </form>
      <ul id="status-options" class="el-select-dropdown" hidden>
        <li role="option">启用</li>
        <li role="option">停用</li>
      </ul>
      <script>
        const bound = { nameA: "", nameB: "", status: "" };
        document.getElementById("status").addEventListener("click", event => event.stopPropagation());
        document.querySelector(".el-select").addEventListener("click", () => {
          document.getElementById("status-options").hidden = false;
        });
        document.getElementById("status-options").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          bound.status = option.textContent;
          document.querySelector(".el-select__placeholder").textContent = option.textContent;
          document.getElementById("status").value = option.textContent;
          document.getElementById("status-options").hidden = true;
        });
        document.getElementById("name-a").addEventListener("input", event => { bound.nameA = event.target.value; });
        document.getElementById("name-b").addEventListener("input", event => { bound.nameB = event.target.value; });
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          bound.nameA = bound.nameA || document.getElementById("name-a").value;
          bound.nameB = bound.nameB || document.getElementById("name-b").value;
          if (!bound.nameA || !bound.nameB || !bound.status) return;
          fetch("/api/create", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(bound)
          });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "contract");
    const before: any = await recorder.control({ action: "snapshot" });
    const labels = (before.todoFields || []).map((field: any) => String(field.label || ""));
    assert.equal(labels.filter((label: string) => /^名称/.test(label)).length >= 2, true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /状态/.test(label)), true, JSON.stringify(before.todoFields));
    await assert.rejects(() => recorder.control({ action: "click", selector: "label=名称" }));
    const blocked: any = await recorder.control({ action: "submit-form" });
    assert.equal(blocked.ok, false, JSON.stringify(blocked));
    assert.equal(created.length, 0);
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    const status = String((result.formFields || []).find((field: any) => /状态/.test(String(field.label || "")))?.value || "");
    assert.match(status, /用/);
    assert.equal(/^样例-/.test(status), false, status);
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(submitted.sawRequest, true, JSON.stringify(submitted));
    assert.equal(created.length, 1, JSON.stringify({ created, submitted }));
    const payload = created[0] as { nameA?: string; nameB?: string; status?: string };
    assert.match(String(payload.nameA || ""), /样例/);
    assert.match(String(payload.nameB || ""), /样例/);
    assert.notEqual(payload.nameA, payload.nameB);
    assert.match(String(payload.status || ""), /用/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("snapshot sees empty avatar wells in a process rail and exercise-form can open them", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-well-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>流程槽</title></head><body>
      <form id="create">
        <label>说明<input id="memo"></label>
        <button type="submit">提交</button>
      </form>
      <aside class="approval-rail">
        <div class="process-node">
          <div class="node-name">一级审批</div>
          <span class="selected-tag">已指定甲</span>
        </div>
        <div class="process-node">
          <div class="node-name">二级审批</div>
          <div class="el-avatar el-avatar--circle" style="width:36px;height:36px;cursor:pointer">
            <i class="el-icon"><svg width="14" height="14" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"></path></svg></i>
          </div>
        </div>
      </aside>
      <div id="picker" role="dialog" hidden style="position:fixed;inset:20px;background:#fff;z-index:30;padding:16px">
        <table><tbody><tr data-user="乙"><td>乙</td></tr></tbody></table>
        <button type="button" id="pick-ok">确定</button>
      </div>
      <script>
        const bound = { memo: "", assignee: "" };
        document.getElementById("memo").addEventListener("input", event => { bound.memo = event.target.value; });
        document.querySelector(".el-avatar").addEventListener("click", () => {
          document.getElementById("picker").hidden = false;
        });
        document.querySelector("#picker table").addEventListener("click", event => {
          const row = event.target.closest("tr");
          if (row) bound.assignee = row.getAttribute("data-user");
        });
        document.getElementById("pick-ok").onclick = () => {
          if (!bound.assignee) return;
          const node = document.querySelectorAll(".process-node")[1];
          const tag = document.createElement("span");
          tag.className = "selected-tag";
          tag.textContent = bound.assignee;
          node.querySelector(".el-avatar")?.remove();
          node.appendChild(tag);
          document.getElementById("picker").hidden = true;
        };
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          bound.memo = bound.memo || document.getElementById("memo").value;
          if (!bound.assignee) return;
          fetch("/api/create", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(bound) });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "avatar-well");
    const before: any = await recorder.control({ action: "snapshot" });
    const todos = (before.todoFields || []).map((field: any) => String(field.label || ""));
    const fields = (before.formFields || []).map((field: any) => `${field.label}:${field.filled}:${field.value || ""}`);
    assert.equal(todos.some((label: string) => /二级审批/.test(label)), true, JSON.stringify({ todos, fields }));
    assert.equal(todos.some((label: string) => /一级审批/.test(label)), false, JSON.stringify({ todos, fields }));
    const empty = (before.formFields || []).find((field: any) => /二级审批/.test(String(field.label || "")));
    assert.equal(empty?.filled, false, JSON.stringify(empty));
    assert.equal(String(empty?.value || "").includes("二级审批"), false, JSON.stringify(empty));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    const filled = (result.formFields || []).find((field: any) => /二级审批/.test(String(field.label || "")));
    assert.match(String(filled?.value || ""), /乙/);
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(created.length, 1, JSON.stringify({ created, submitted }));
    assert.equal((created[0] as { assignee?: string }).assignee, "乙");
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form fills a search bar with selects and a leftover range calendar in one pass", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-search-"));
  const searched: unknown[] = [];
  const server = http.createServer((request, response) => {
    if ((request.url || "").startsWith("/api/search") && request.method === "GET") {
      searched.push(new URL(request.url, "http://127.0.0.1").search);
      response.setHeader("content-type", "application/json");
      response.end('{"code":0,"rows":[]}');
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>查询栏</title></head><body>
      <form class="el-form" id="search">
        <div class="el-form-item">
          <label class="el-form-item__label">类型</label>
          <div class="el-select">
            <div class="el-select__wrapper">
              <span class="el-select__placeholder">请选择类型</span>
              <input role="combobox" readonly placeholder="请选择类型">
            </div>
          </div>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">申请时间</label>
          <div class="el-date-editor el-date-editor--daterange">
            <input class="el-range-input" readonly placeholder="开始日期">
            <input class="el-range-input" readonly placeholder="结束日期">
          </div>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">结果</label>
          <div class="el-select">
            <div class="el-select__wrapper">
              <span class="el-select__placeholder">请选择结果</span>
              <input role="combobox" readonly placeholder="请选择结果">
            </div>
          </div>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">原因</label>
          <input id="reason" placeholder="请输入原因">
        </div>
        <button type="submit" id="query">搜索</button>
      </form>
      <ul id="type-options" class="el-select-dropdown" hidden>
        <li role="option">事假</li><li role="option">病假</li>
      </ul>
      <ul id="result-options" class="el-select-dropdown" hidden>
        <li role="option">已通过</li><li role="option">未提交</li>
      </ul>
      <div id="range" class="el-popper is-pure el-picker__popper el-date-range-picker" role="dialog" style="position:fixed;inset:40px;background:#fff;z-index:40;padding:12px">
        <span class="el-date-table-cell__text">3</span>
        <span class="el-date-table-cell__text">4</span>
        <button type="button" id="range-ok">确定</button>
      </div>
      <script>
        const bound = { type: "", start: "", end: "", result: "", reason: "" };
        const typeBox = document.querySelectorAll(".el-select")[0];
        const resultBox = document.querySelectorAll(".el-select")[1];
        typeBox.addEventListener("click", () => { document.getElementById("type-options").hidden = false; });
        resultBox.addEventListener("click", () => { document.getElementById("result-options").hidden = false; });
        document.getElementById("type-options").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          bound.type = option.textContent;
          typeBox.querySelector(".el-select__placeholder").textContent = option.textContent;
          typeBox.querySelector("input").value = option.textContent;
          document.getElementById("type-options").hidden = true;
        });
        document.getElementById("result-options").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          bound.result = option.textContent;
          resultBox.querySelector(".el-select__placeholder").textContent = option.textContent;
          resultBox.querySelector("input").value = option.textContent;
          document.getElementById("result-options").hidden = true;
        });
        const start = document.querySelectorAll(".el-range-input")[0];
        const end = document.querySelectorAll(".el-range-input")[1];
        const panel = document.getElementById("range");
        const openRange = () => { panel.hidden = false; };
        document.querySelector(".el-date-editor").addEventListener("click", openRange);
        start.addEventListener("focus", openRange);
        end.addEventListener("focus", openRange);
        start.addEventListener("input", () => { if (!/^\\d{4}-\\d{2}-\\d{2}/.test(start.value)) start.value = ""; });
        end.addEventListener("input", () => { if (!/^\\d{4}-\\d{2}-\\d{2}/.test(end.value)) end.value = ""; });
        panel.addEventListener("click", event => {
          if (!event.target.classList.contains("el-date-table-cell__text")) return;
          const day = event.target.textContent.padStart(2, "0");
          if (!bound.start) {
            bound.start = "2026-09-" + day;
            start.value = bound.start;
          } else {
            bound.end = "2026-09-" + day;
            end.value = bound.end;
          }
        });
        document.getElementById("range-ok").onclick = () => { panel.hidden = true; };
        document.getElementById("reason").addEventListener("input", event => { bound.reason = event.target.value; });
        document.getElementById("search").addEventListener("submit", event => {
          event.preventDefault();
          bound.reason = bound.reason || document.getElementById("reason").value;
          fetch("/api/search?type=" + encodeURIComponent(bound.type) + "&start=" + bound.start + "&end=" + bound.end);
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "search-bar");
    const before: any = await recorder.control({ action: "snapshot" });
    assert.equal(before.scope, "page", JSON.stringify({ scope: before.scope, text: String(before.text || "").slice(0, 80) }));
    const labels = (before.todoFields || []).map((field: any) => String(field.label || ""));
    assert.equal(labels.some((label: string) => /类型/.test(label)), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /开始日期|申请时间/.test(label)), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /结束日期|申请时间/.test(label)), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /结果/.test(label)), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /原因/.test(label)), true, JSON.stringify(before.todoFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    assert.equal(result.todoCount, 0, JSON.stringify(result.todoFields));
    assert.equal((result.failed || []).length, 0, JSON.stringify(result.failed));
    const type = String((result.formFields || []).find((field: any) => /类型/.test(String(field.label || "")))?.value || "");
    const reason = String((result.formFields || []).find((field: any) => /原因/.test(String(field.label || "")))?.value || "");
    const start = String((result.formFields || []).find((field: any) => field.rangeIndex === 0 || /开始日期/.test(String(field.label || "")))?.value || "");
    const end = String((result.formFields || []).find((field: any) => field.rangeIndex === 1 || /结束日期/.test(String(field.label || "")))?.value || "");
    assert.match(type, /假/);
    assert.equal(/^样例-/.test(type), false, type);
    assert.match(reason, /样例/);
    assert.match(start, /\d{4}-\d{2}-\d{2}/);
    assert.match(end, /\d{4}-\d{2}-\d{2}/);
    const after: any = await recorder.control({ action: "snapshot" });
    assert.equal(Boolean(after.text && /确定/.test(after.text) && after.scope === "dialog"), false, JSON.stringify({ scope: after.scope, text: after.text }));
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form opens a kit select from the wrapper surface, not the outer host", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-wrapper-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>外壳下拉</title></head><body>
      <form class="el-form">
        <div class="el-form-item">
          <label class="el-form-item__label">请假类型</label>
          <div class="el-select">
            <div class="el-select__wrapper">
              <span class="el-select__placeholder">请选择请假类型</span>
              <input role="combobox" readonly placeholder="请选择请假类型">
            </div>
          </div>
        </div>
        <button type="submit">搜索</button>
      </form>
      <ul id="opts" class="el-select-dropdown" hidden>
        <li role="option">事假</li><li role="option">病假</li>
      </ul>
      <script>
        document.querySelector(".el-select").addEventListener("click", event => event.stopPropagation());
        document.querySelector(".el-select__wrapper").addEventListener("mousedown", () => {
          document.getElementById("opts").hidden = false;
        });
        document.getElementById("opts").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          document.querySelector(".el-select__placeholder").textContent = option.textContent;
          document.querySelector("[role=combobox]").value = option.textContent;
          document.getElementById("opts").hidden = true;
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "wrapper-select");
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields));
    assert.match(String((result.formFields || []).find((field: any) => /类型/.test(String(field.label || "")))?.value || ""), /假/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("chooser dialogs do not steal the form and exercise-form picks a person row", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-chooser-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>发起请假</title></head><body>
      <form class="el-form" id="create">
        <div class="el-form-item">
          <label class="el-form-item__label">请假类型</label>
          <div class="el-select">
            <div class="el-select__wrapper">
              <span class="el-select__placeholder">请选择请假类型</span>
              <input role="combobox" readonly placeholder="请选择请假类型">
            </div>
          </div>
        </div>
        <button type="submit">提交</button>
      </form>
      <aside class="approval-rail">
        <div class="process-node">
          <div class="node-name">人力审批</div>
          <div class="el-avatar el-avatar--circle" style="width:36px;height:36px">+</div>
        </div>
      </aside>
      <ul id="opts" class="el-select-dropdown" hidden>
        <li role="option">事假</li>
      </ul>
      <div id="user" class="el-dialog" role="dialog" style="position:fixed;inset:20px;background:#fff;z-index:40;padding:16px">
        <span class="el-dialog__title">选择用户</span>
        <div class="el-tree"><div class="el-tree-node" role="treeitem">科技信息</div></div>
        <table><tbody><tr data-user="duanya"><td>duanya</td></tr></tbody></table>
        <button type="button" id="ok">确定</button>
      </div>
      <script>
        const bound = { type: "", user: "" };
        document.querySelector(".el-select__wrapper").addEventListener("mousedown", () => {
          document.getElementById("opts").hidden = false;
        });
        document.getElementById("opts").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          bound.type = option.textContent;
          document.querySelector(".el-select__placeholder").textContent = option.textContent;
          document.querySelector("[role=combobox]").value = option.textContent;
          document.getElementById("opts").hidden = true;
        });
        document.querySelector(".el-avatar").addEventListener("click", () => {
          document.getElementById("user").hidden = false;
        });
        document.querySelector("#user table").addEventListener("click", event => {
          const row = event.target.closest("tr");
          if (row) bound.user = row.getAttribute("data-user");
        });
        document.getElementById("ok").onclick = () => {
          if (!bound.user) return;
          const node = document.querySelector(".process-node");
          const tag = document.createElement("span");
          tag.className = "selected-tag";
          tag.textContent = bound.user;
          node.querySelector(".el-avatar")?.remove();
          node.appendChild(tag);
          document.getElementById("user").hidden = true;
        };
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          if (!bound.type || !bound.user) return;
          fetch("/api/create", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(bound) });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "chooser-dialog");
    const before: any = await recorder.control({ action: "snapshot" });
    assert.equal(before.scope, "page", JSON.stringify({ scope: before.scope, text: String(before.text || "").slice(0, 80) }));
    assert.equal((before.todoFields || []).some((field: any) => /类型/.test(String(field.label || ""))), true, JSON.stringify(before.todoFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    assert.match(String((result.formFields || []).find((field: any) => /类型/.test(String(field.label || "")))?.value || ""), /假/);
    assert.match(String((result.formFields || []).find((field: any) => /人力/.test(String(field.label || "")))?.value || ""), /duanya/);
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal((created[0] as { user?: string })?.user, "duanya", JSON.stringify(created));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form clicks the visible option node and ignores table-header filters", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-option-"));
  const searched: unknown[] = [];
  const server = http.createServer((request, response) => {
    if ((request.url || "").startsWith("/api/search") && request.method === "GET") {
      searched.push(new URL(request.url, "http://127.0.0.1").search);
      response.setHeader("content-type", "application/json");
      response.end('{"code":0}');
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>选项与表头</title></head><body>
      <form class="el-form" id="search">
        <div class="el-form-item">
          <label class="el-form-item__label">请假类型</label>
          <div class="el-select">
            <div class="el-select__wrapper"><input role="combobox" readonly placeholder="请选择请假类型"></div>
          </div>
        </div>
        <div class="el-form-item">
          <label class="el-form-item__label">所属项目</label>
          <div class="el-select">
            <div class="el-select__wrapper">
              <input role="combobox" readonly placeholder="请选择项目">
              <input class="el-select__input" placeholder="请输入项目名称">
            </div>
          </div>
        </div>
        <button type="submit">搜索</button>
      </form>
      <table class="el-table">
        <thead class="el-table__header"><tr>
          <th class="el-table__cell">入库数量
            <div class="el-select"><input role="combobox" placeholder="入库数量"></div>
          </th>
        </tr></thead>
        <tbody></tbody>
      </table>
      <ul id="opts" class="el-select-dropdown" hidden>
        <li class="el-select-dropdown__item"><span>事假</span><i>x</i></li>
      </ul>
      <ul id="proj" class="el-select-dropdown" hidden>
        <li class="el-select-dropdown__item">内部项目</li>
      </ul>
      <script>
        const bound = { type: "", project: "" };
        const selects = document.querySelectorAll(".el-form .el-select__wrapper");
        selects[0].addEventListener("mousedown", event => {
          if (!event.isTrusted) return;
          document.getElementById("opts").hidden = false;
        });
        selects[1].addEventListener("mousedown", event => {
          if (!event.isTrusted) return;
          document.getElementById("proj").hidden = false;
        });
        document.getElementById("opts").addEventListener("click", event => {
          const option = event.target.closest(".el-select-dropdown__item");
          if (!option) return;
          bound.type = "事假";
          selects[0].querySelector("[role=combobox]").value = "事假";
          document.getElementById("opts").hidden = true;
        });
        document.getElementById("proj").addEventListener("click", event => {
          const option = event.target.closest(".el-select-dropdown__item");
          if (!option) return;
          bound.project = option.textContent;
          selects[1].querySelector("[role=combobox]").value = option.textContent;
          document.getElementById("proj").hidden = true;
        });
        document.getElementById("search").addEventListener("submit", event => {
          event.preventDefault();
          fetch("/api/search?type=" + encodeURIComponent(bound.type));
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "option-header");
    const before: any = await recorder.control({ action: "snapshot" });
    const labels = (before.todoFields || []).map((field: any) => String(field.label || ""));
    assert.equal(labels.some((label: string) => /类型/.test(label)), true, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /项目名称/.test(label)), false, JSON.stringify(before.todoFields));
    assert.equal(labels.some((label: string) => /入库数量/.test(label)), false, JSON.stringify(before.todoFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields));
    assert.match(String((result.formFields || []).find((field: any) => /类型/.test(String(field.label || "")))?.value || ""), /假/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("page snapshot does not force a full-document innerText read", () => {
  const source = Function.prototype.toString.call(SNAPSHOT_IN_PAGE);
  assert.equal(/document\.body\.innerText/.test(source), false, source.slice(0, 200));
});

test("exercise-form opens a trusted-only select from the input wrapper", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-trusted-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>可信下拉</title></head><body>
      <form class="el-form">
        <div class="el-form-item">
          <label class="el-form-item__label">审批结果</label>
          <div class="el-select">
            <div class="el-input">
              <div class="el-input__wrapper">
                <input readonly placeholder="请选择审批结果">
              </div>
            </div>
          </div>
        </div>
        <button type="submit">搜索</button>
      </form>
      <ul id="opts" class="el-select-dropdown" hidden>
        <li role="option">审批中</li><li role="option">已通过</li>
      </ul>
      <script>
        document.querySelector(".el-input__wrapper").addEventListener("mousedown", event => {
          if (!event.isTrusted) return;
          document.getElementById("opts").hidden = false;
        });
        document.getElementById("opts").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          document.querySelector("[placeholder='请选择审批结果']").value = option.textContent;
          document.getElementById("opts").hidden = true;
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "trusted-select");
    const before: any = await recorder.control({ action: "snapshot" });
    const field = (before.todoFields || []).find((item: any) => /审批结果/.test(String(item.label || "")));
    assert.equal(field?.kind, "select", JSON.stringify(before.todoFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields));
    assert.match(String((result.formFields || []).find((item: any) => /审批结果/.test(String(item.label || "")))?.value || ""), /审批中|已通过/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("leftover chooser needs a checkbox and does not block later selects", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-check-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>选人复选</title></head><body>
      <form class="el-form" id="create">
        <div class="el-form-item">
          <label class="el-form-item__label">请假类型</label>
          <div class="el-select">
            <div class="el-select__wrapper">
              <input role="combobox" readonly placeholder="请选择请假类型">
            </div>
          </div>
        </div>
        <button type="submit">提交</button>
      </form>
      <aside class="approval-rail">
        <div class="process-node">
          <div class="node-name">人力审批</div>
          <div class="el-avatar el-avatar--circle" style="width:36px;height:36px">+</div>
        </div>
      </aside>
      <ul id="opts" class="el-select-dropdown" hidden>
        <li role="option">事假</li>
      </ul>
      <div id="user" class="el-dialog" role="dialog" style="position:fixed;inset:20px;background:#fff;z-index:40;padding:16px">
        <span class="el-dialog__title">选择用户</span>
        <div class="el-tree"><div class="el-tree-node" role="treeitem">科技信息</div></div>
        <table><tbody><tr><td><span class="el-checkbox" id="cb" style="display:inline-block;width:16px;height:16px;border:1px solid #333"></span></td><td>duanya</td></tr></tbody></table>
        <button type="button" id="ok">确定</button>
      </div>
      <script>
        const bound = { type: "", user: "" };
        document.querySelector(".el-select__wrapper").addEventListener("mousedown", event => {
          if (!event.isTrusted) return;
          document.getElementById("opts").hidden = false;
        });
        document.getElementById("opts").addEventListener("click", event => {
          const option = event.target.closest("[role=option]");
          if (!option) return;
          bound.type = option.textContent;
          document.querySelector("[role=combobox]").value = option.textContent;
          document.getElementById("opts").hidden = true;
        });
        document.querySelector("tbody tr").addEventListener("click", () => {});
        document.getElementById("cb").addEventListener("click", event => {
          event.stopPropagation();
          bound.user = "duanya";
        });
        document.getElementById("ok").onclick = () => {
          if (!bound.user) return;
          const node = document.querySelector(".process-node");
          const tag = document.createElement("span");
          tag.className = "selected-tag";
          tag.textContent = bound.user;
          node.querySelector(".el-avatar")?.remove();
          node.appendChild(tag);
          document.getElementById("user").hidden = true;
        };
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          if (!bound.type || !bound.user) return;
          fetch("/api/create", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(bound) });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "checkbox-chooser");
    const before: any = await recorder.control({ action: "snapshot" });
    assert.equal(before.scope, "page", JSON.stringify({ scope: before.scope, text: String(before.text || "").slice(0, 80) }));
    assert.equal((before.todoFields || []).some((field: any) => /类型/.test(String(field.label || ""))), true, JSON.stringify(before.todoFields));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    assert.match(String((result.formFields || []).find((field: any) => /类型/.test(String(field.label || "")))?.value || ""), /假/);
    assert.match(String((result.formFields || []).find((field: any) => /人力/.test(String(field.label || "")))?.value || ""), /duanya/);
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal((created[0] as { user?: string })?.user, "duanya", JSON.stringify(created));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("exercise-form fills a form remark and a table remark when placeholders repeat", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-remark-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>重复备注</title></head><body>
      <form class="el-form" id="search" style="opacity:.4">
        <div class="el-form-item"><label class="el-form-item__label">备注</label><input placeholder="请输入备注"></div>
        <button type="submit">搜索</button>
      </form>
      <div class="el-overlay-dialog" role="dialog">
        <div class="el-dialog">
          <span class="el-dialog__title">新增订单</span>
          <form class="el-form" id="create">
            <div class="el-form-item"><label class="el-form-item__label">订单时间</label><input id="when" placeholder="选择订单时间"></div>
            <div class="el-form-item"><label class="el-form-item__label">供应商</label><input id="supplier"></div>
            <div class="el-form-item"><label class="el-form-item__label">结算账户</label><input id="account"></div>
            <div class="el-form-item"><label class="el-form-item__label">备注</label><textarea id="memo" placeholder="请输入备注"></textarea></div>
            <table class="el-table">
              <thead class="el-table__header"><tr><th class="el-table__cell">备注</th></tr></thead>
              <tbody><tr class="el-table__row"><td class="el-table__cell">
                <div class="el-form-item"><input id="line-memo" placeholder="请输入备注"></div>
              </td></tr></tbody>
            </table>
            <button type="submit">确定</button>
          </form>
        </div>
      </div>
      <script>
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          fetch("/api/create", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              supplier: document.getElementById("supplier").value,
              memo: document.getElementById("memo").value,
              line: document.getElementById("line-memo").value
            })
          });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "repeat-remark");
    const before: any = await recorder.control({ action: "snapshot" });
    const remarks = (before.formFields || []).filter((field: any) => String(field.label || "").includes("备注"));
    assert.equal(remarks.some((field: any) => field.kind === "textarea" && /^label=/.test(String(field.selector || ""))), true, JSON.stringify(remarks));
    assert.equal(remarks.some((field: any) => String(field.selector || "").startsWith("column=")), true, JSON.stringify(remarks));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal(created.length, 1, JSON.stringify({ created, submitted }));
    const row = created[0] as { memo?: string; line?: string; supplier?: string };
    assert.match(String(row.memo || ""), /备注/);
    assert.match(String(row.line || ""), /备注/);
    assert.match(String(row.supplier || ""), /供应商|样例/);
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("timeline assigned avatar is filled and empty add-user button is the only picker todo", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-timeline-"));
  const created: unknown[] = [];
  const server = http.createServer((request, response) => {
    if (request.url === "/api/create" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => { body += chunk; });
      request.on("end", () => {
        created.push(JSON.parse(body || "{}"));
        response.setHeader("content-type", "application/json");
        response.end('{"code":0}');
      });
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<!doctype html><html><head><title>流程时间轴</title></head><body>
      <form class="el-form" id="create">
        <div class="el-form-item"><label class="el-form-item__label">原因</label><textarea id="reason" placeholder="请输入请假原因"></textarea></div>
        <button type="submit">确定</button>
      </form>
      <div class="el-card__body">
        <ul class="el-timeline">
          <li class="el-timeline-item">
            <div class="el-timeline-item__dot"><div class="rounded-full"><img alt=""></div></div>
            <div class="el-timeline-item__content">
              <div class="flex flex-col" id="activity-task-leader">
                <div class="font-bold">领导审批</div>
                <div class="flex items-center flex-wrap mt-1 gap2">
                  <span class="el-avatar el-avatar--circle"><img alt="user"></span>
                  管理员
                </div>
              </div>
            </div>
          </li>
          <li class="el-timeline-item">
            <div class="el-timeline-item__dot"><div class="rounded-full"><img alt=""></div></div>
            <div class="el-timeline-item__content">
              <div class="flex flex-col" id="activity-task-hr">
                <div class="font-bold">人力审批</div>
                <div class="flex flex-wrap gap2 items-center">
                  <button type="button" class="el-button el-tooltip__trigger" id="add-hr"><span><img class="w-18px" alt=""></span></button>
                </div>
              </div>
            </div>
          </li>
        </ul>
      </div>
      <div id="picker" class="el-dialog" role="dialog" hidden style="position:fixed;inset:20px;background:#fff;z-index:40;padding:16px">
        <span class="el-dialog__title">选择用户</span>
        <table><tbody><tr data-user="duanya"><td>duanya</td></tr></tbody></table>
        <button type="button" id="ok"> 确 定 </button>
      </div>
      <script>
        const bound = { reason: "", hr: "" };
        document.getElementById("reason").addEventListener("input", event => { bound.reason = event.target.value; });
        document.getElementById("add-hr").addEventListener("click", () => {
          document.getElementById("picker").hidden = false;
        });
        document.querySelector("#picker table").addEventListener("click", event => {
          const row = event.target.closest("tr");
          if (row) bound.hr = row.getAttribute("data-user");
        });
        document.getElementById("ok").onclick = () => {
          if (!bound.hr) return;
          const wrap = document.querySelector("#activity-task-hr .flex-wrap");
          wrap.innerHTML = '<span class="el-avatar el-avatar--circle"></span> ' + bound.hr;
          document.getElementById("picker").hidden = true;
        };
        document.getElementById("create").addEventListener("submit", event => {
          event.preventDefault();
          bound.reason = bound.reason || document.getElementById("reason").value;
          if (!bound.hr) return;
          fetch("/api/create", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(bound) });
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "timeline-slot");
    const before: any = await recorder.control({ action: "snapshot" });
    const leader = (before.formFields || []).find((field: any) => String(field.label || "").includes("领导审批"));
    const hr = (before.todoFields || []).find((field: any) => String(field.label || "").includes("人力审批"));
    assert.equal(leader?.filled, true, JSON.stringify(leader));
    assert.match(String(leader?.value || ""), /管理员/);
    assert.equal(Boolean(hr), true, JSON.stringify(before.todoFields));
    assert.equal(hr?.filled, false, JSON.stringify(hr));
    const result: any = await recorder.control({ action: "exercise-form" });
    assert.equal(result.ok, true, JSON.stringify(result.failed || result.todoFields || result.formFields));
    assert.match(String((result.formFields || []).find((field: any) => /人力/.test(String(field.label || "")))?.value || ""), /duanya/);
    const submitted: any = await recorder.control({ action: "submit-form" });
    assert.equal(submitted.ok, true, JSON.stringify(submitted));
    assert.equal((created[0] as { hr?: string })?.hr, "duanya", JSON.stringify(created));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("follows a new tab after a link click and stays on the old page if the tab dies", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-newpage-"));
  const server = http.createServer((request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    if (request.url === "/next") {
      response.end(`<!doctype html><html><head><title>新页面</title></head><body>
        <h1>已打开新页面</h1>
        <a id="open-dead" href="http://127.0.0.1:1/dead" target="_blank" style="position:fixed;left:20px;top:20px;width:160px;height:32px">打开坏页</a>
        <input id="note" style="position:fixed;left:20px;top:70px;width:180px;height:30px">
      </body></html>`);
      return;
    }
    if (request.url === "/same") {
      response.end("<!doctype html><html><head><title>同页跳转</title></head><body><p>同一标签打开</p></body></html>");
      return;
    }
    response.end(`<!doctype html><html><head><title>首页</title></head><body>
      <a id="open-ok" href="/next" target="_blank" style="position:fixed;left:20px;top:20px;width:160px;height:32px">打开新页</a>
      <a id="go-same" href="/same" style="position:fixed;left:20px;top:70px;width:160px;height:32px">本页跳转</a>
      <a id="go-hash" href="#/detail" style="position:fixed;left:20px;top:120px;width:160px;height:32px">hash跳转</a>
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "new-page");
    const opened: any = await recorder.control({ action: "click", selector: "#open-ok" });
    const afterOpen = await recorder.state();
    assert.match(String(afterOpen.url || opened.url || ""), /\/next/);
    assert.equal((afterOpen as { pageError?: string }).pageError, undefined, JSON.stringify(afterOpen));
    const nextSnap: any = await recorder.control({ action: "snapshot" });
    assert.equal(nextSnap.title, "新页面", JSON.stringify({ url: afterOpen.url, title: nextSnap.title }));

    const dead: any = await recorder.control({ action: "click", selector: "#open-dead" });
    const afterDead = await recorder.state();
    assert.doesNotMatch(String(afterDead.url || ""), /chrome-error|chromewebdata/i);
    assert.match(String(afterDead.url || dead.url || ""), /\/next/);
    assert.equal(Boolean((afterDead as { pageError?: string }).pageError), true, JSON.stringify(afterDead));
    const stillNext: any = await recorder.control({ action: "snapshot" });
    assert.equal(stillNext.title, "新页面", JSON.stringify(stillNext));
    const preview = await recorder.preview();
    assert.equal(preview[0], 0xff);
    assert.equal(preview[1], 0xd8);

    await recorder.control({ action: "goto", url: `http://127.0.0.1:${address.port}/` });
    await recorder.control({ action: "click", selector: "#go-same" });
    const afterSame = await recorder.state();
    assert.match(String(afterSame.url || ""), /\/same/);
    const sameSnap: any = await recorder.control({ action: "snapshot" });
    assert.equal(sameSnap.title, "同页跳转", JSON.stringify(sameSnap));
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});

function parseFieldInstant(value?: string) {
  const text = String(value || "");
  const match = text.match(/(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?/);
  if (!match) return undefined;
  const time = match[2] ? (match[2].length === 5 ? `${match[2]}:00` : match[2]) : "00:00:00";
  const date = new Date(`${match[1]}T${time}`);
  return Number.isNaN(date.getTime()) ? undefined : date.getTime();
}
