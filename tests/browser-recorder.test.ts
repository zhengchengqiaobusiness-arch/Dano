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

function parseFieldInstant(value?: string) {
  const text = String(value || "");
  const match = text.match(/(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?/);
  if (!match) return undefined;
  const time = match[2] ? (match[2].length === 5 ? `${match[2]}:00` : match[2]) : "00:00:00";
  const date = new Date(`${match[1]}T${time}`);
  return Number.isNaN(date.getTime()) ? undefined : date.getTime();
}
