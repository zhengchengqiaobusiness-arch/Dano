import test from "node:test";
import assert from "node:assert/strict";
import { isPageSessionId, WorkbenchPage } from "../src/web/workbench-page.js";
import { PiRpcBridge } from "../src/web/pi-rpc.js";

test("page session ids are accepted only in the workbench format", () => {
  assert.equal(isPageSessionId("page_abcdef12"), true);
  assert.equal(isPageSessionId("page_4f2c9b1e8a704c0db2c1e6a7f3d5c819"), true);
  assert.equal(isPageSessionId(""), false);
  assert.equal(isPageSessionId("page_"), false);
  assert.equal(isPageSessionId("shared"), false);
  assert.equal(isPageSessionId("page_***"), false);
});

test("Pi RPC exposes processId so workbench abort/dispose does not throw", () => {
  const bridge = new PiRpcBridge(".", "http://127.0.0.1:4310", "token");
  assert.equal(typeof bridge.processId, "function");
  assert.equal(bridge.processId(), undefined);
});

test("manual takeover waits for the person and then restores automatic execution", async () => {
  const events: any[] = [];
  const page = new WorkbenchPage("page_takeover123", {
    rootDir: ".",
    dataDir: ".business-skill-studio",
    recordingsDir: ".business-skill-studio/recordings",
    catalogDir: ".business-skill-studio/catalog",
    profileDir: ".business-skill-studio/browser-profile",
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  }, "http://127.0.0.1:4310", value => value, () => {});
  page.broadcast = payload => { events.push(payload); };

  const waiting = page.requestManualTakeover("自动填表已连续失败 3 次");
  assert.equal(page.mode, "manual");
  assert.equal(page.manualTakeoverState()?.reason, "自动填表已连续失败 3 次");
  assert.equal(events.some(event => event.type === "manual_takeover_required"), true);

  assert.equal(page.completeManualTakeover(page.manualTakeoverState()!.id), true);
  assert.deepEqual(await waiting, { completed: true });
  assert.equal(page.mode, "automatic");
  assert.equal(page.manualTakeoverState(), undefined);
  assert.equal(events.some(event => event.type === "manual_takeover_completed"), true);
});
