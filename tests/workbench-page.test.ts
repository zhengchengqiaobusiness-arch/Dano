import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { mkdtemp, rm } from "node:fs/promises";
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

test("acceptUserMessage records the user turn without starting Pi", () => {
  const events: any[] = [];
  const page = new WorkbenchPage("page_acceptuser", {
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

  const userEvent = page.acceptUserMessage("打开差旅列表");

  assert.equal(userEvent.item.kind, "message");
  assert.equal(userEvent.item.role, "user");
  assert.equal(userEvent.item.text, "打开差旅列表");
  assert.equal(page.transcript.items.length, 1);
  assert.equal(page.pi.status().ready, false);
  assert.equal(page.pi.status().running, false);
  assert.equal(events.some(event => event.type === "session_item" && event.item?.text === "打开差旅列表"), true);
});

test("reset forgets the last recording so the next chat is independent", async () => {
  const page = new WorkbenchPage("page_clearsession", {
    rootDir: ".",
    dataDir: ".business-skill-studio",
    recordingsDir: ".business-skill-studio/recordings",
    catalogDir: ".business-skill-studio/catalog",
    profileDir: ".business-skill-studio/browser-profile",
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  }, "http://127.0.0.1:4310", value => value, () => {});
  page.lastRecordingSessionId = "rec_previous_trip";
  const events: any[] = [];
  page.broadcast = payload => { events.push(payload); };

  await page.reset();

  assert.equal(page.lastRecordingSessionId, undefined);
  assert.equal(page.transcriptOpen, false);
  assert.equal(page.pi.status().ready, false);
  assert.equal(events.some(event => event.type === "session_reset"), true);
});

test("Pi RPC exposes processId so workbench abort/dispose does not throw", () => {
  const bridge = new PiRpcBridge(".", "http://127.0.0.1:4310", "token");
  assert.equal(typeof bridge.processId, "function");
  assert.equal(bridge.processId(), undefined);
});

test("a settled agent is resumed to stop and export after the live recording audit passes", async () => {
  const page = new WorkbenchPage("page_finalizeaudit", {
    rootDir: ".",
    dataDir: ".business-skill-studio",
    recordingsDir: ".business-skill-studio/recordings",
    catalogDir: ".business-skill-studio/catalog",
    profileDir: ".business-skill-studio/browser-profile",
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  }, "http://127.0.0.1:4310", value => value, () => {});
  const prompts: string[] = [];
  (page.recorder as any).activeSession = () => ({
    id: "rec_finalizeaudit",
    completeFieldCoverage: true,
    completePageCoverage: false,
    expectedOperations: ["query"]
  });
  (page.recorder as any).stopReadiness = async () => ({
    ready: true,
    pageCoverage: undefined,
    missingPageOperations: [],
    missingFields: [],
    nextAction: { action: "record-stop" }
  });
  (page.pi as any).status = () => ({ streaming: false });
  (page.pi as any).prompt = async (prompt: string) => { prompts.push(prompt); };

  (page as any).scheduleCoverageContinuation();
  await new Promise(resolve => setTimeout(resolve, 260));

  assert.equal(prompts.length, 1);
  assert.match(prompts[0]!, /business_skill_record_stop/);
  assert.match(prompts[0]!, /business_skill_export/);
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

test("starting on a login page pauses immediately until the person completes login", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "bss-login-takeover-"));
  const events: any[] = [];
  const page = new WorkbenchPage("page_loginpause", {
    rootDir: temporary,
    dataDir: path.join(temporary, "data"),
    recordingsDir: path.join(temporary, "data", "recordings"),
    catalogDir: path.join(temporary, "data", "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  }, "http://127.0.0.1:4310", value => value, () => {});
  page.broadcast = payload => { events.push(payload); };
  const recorder = page.recorder as any;
  recorder.start = async (startUrl: string, name: string) => ({
    id: "rec_loginpause",
    name,
    startedAt: new Date().toISOString(),
    startUrl,
    eventsFile: path.join(temporary, "events.jsonl"),
    expectedOperations: []
  });
  recorder.loginPageState = async () => ({ detected: true, reason: "检测到账号密码登录页面" });
  recorder.resumeAfterManualTakeover = () => {};

  try {
    let settled = false;
    const starting = page.startRecording("https://example.test/login", "login-page").then(result => {
      settled = true;
      return result;
    });
    for (let attempt = 0; attempt < 20 && !page.manualTakeoverState(); attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }

    assert.equal(settled, false);
    assert.match(page.manualTakeoverState()?.reason || "", /登录/);
    assert.equal(events.some(event => event.type === "manual_takeover_required"), true);

    assert.equal(page.completeManualTakeover(page.manualTakeoverState()!.id), true);
    assert.equal((await starting).id, "rec_loginpause");
    assert.equal(settled, true);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
