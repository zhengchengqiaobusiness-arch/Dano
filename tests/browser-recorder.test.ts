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
      <button id="login" onclick="fetch('/api',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username:'demo',password:'never-store-this'})})">登录</button>
      <iframe src="/frame"></iframe>
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
    await recorder.control({ action: "click", selector: "#login" });
    await recorder.control({ action: "click", selector: "#frame-button" });
    await recorder.control({ action: "wait", ms: 350 });
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.equal(snapshot.title, "泛化页面");
    assert.equal(snapshot.controls.some((control: any) => control.selector === "#customer" && control.text === "手动录制客户"), true);
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
