/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { WebSocket } from "ws";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { ResultsCatalog } from "../src/results-catalog.mjs";
import { attachFrontendBridge, shouldCancelOnFrontendDisconnect } from "../src/frontend-bridge.mjs";

function waitFor(predicate, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const tick = async () => {
      try {
        if (await predicate()) {
          resolve();
          return;
        }
      } catch {
        // 条件未就绪时继续等
      }
      if (Date.now() - started >= timeoutMs) {
        reject(new Error("等待超时"));
        return;
      }
      setTimeout(tick, 20);
    };
    tick();
  });
}

function listenSnapshots(ws) {
  const snapshots = [];
  ws.on("message", (raw) => {
    let message;
    try {
      message = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (message.type === "snapshot" && message.snapshot) {
      snapshots.push(message.snapshot);
    }
  });
  return snapshots;
}

async function openRecorderSocket(port) {
  const ws = new WebSocket(`ws://127.0.0.1:${port}/onboarding/page/record`);
  await new Promise((resolve, reject) => {
    ws.once("open", resolve);
    ws.once("error", reject);
  });
  return ws;
}

test("现有录制页拿到的 draft 就是 PI 提交的能力合同", async () => {
  const result = sampleResult();
  const harness = await createHarness({ result });
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "产出能力",
    });
    const stopped = await harness.controller.stop(started.id);
    assert.equal(stopped.session.capabilityCount, 1);
    assert.equal(stopped.result.capabilities[0].name, "create_leave");
    assert.equal(stopped.result.steps[0].params[0].exposed_to_user, true);
    const catalog = new ResultsCatalog(harness.files);
    const summary = await catalog.remember({
      recordingId: started.id,
      action: "action_1",
      title: "演示",
      goal: "产出能力",
      result: stopped.result,
      evidenceCount: 3,
    });
    assert.equal(summary.capability_count, 1);
    assert.equal(summary.request_count, 1);
    assert.equal(summary.title, "演示");
    const detail = catalog.detail(started.id);
    assert.deepEqual(detail.draft, stopped.result);
    assert.equal(detail.draft.capabilities.length, 1);
  } finally {
    await harness.cleanup();
  }
});

test("证据冻结或正在最终分析时，前台断开不得取消", () => {
  assert.equal(shouldCancelOnFrontendDisconnect({ status: "recording", frozen: false }), true);
  assert.equal(shouldCancelOnFrontendDisconnect({ status: "recording", frozen: false }, { finalizing: true }), false);
  assert.equal(shouldCancelOnFrontendDisconnect({ status: "pi_finalizing", frozen: true }), false);
  assert.equal(shouldCancelOnFrontendDisconnect({ status: "recording", frozen: true }), false);
  assert.equal(shouldCancelOnFrontendDisconnect({ status: "succeeded", hasFinalResult: true }), false);
  assert.equal(shouldCancelOnFrontendDisconnect(null), false);
});

test("点完停止后前台断开，PI 仍能提交并写入历史", async () => {
  const result = sampleResult();
  const harness = await createHarness({
    result,
    piBehavior: "submit_after_delay",
    piDelayMs: 250,
  });
  const catalog = new ResultsCatalog(harness.files);
  const httpServer = createServer();
  const wss = attachFrontendBridge(httpServer, { controller: harness.controller, catalog });
  await new Promise((resolve) => httpServer.listen(0, "127.0.0.1", resolve));
  const { port } = httpServer.address();
  let ws;
  try {
    ws = await openRecorderSocket(port);
    const snapshots = listenSnapshots(ws);
    ws.send(JSON.stringify({
      type: "start",
      start_url: "http://example.com",
      goal_text: "产出能力",
      title: "冻结后断线",
    }));
    await waitFor(() => snapshots.some((item) => String(item.run_id || "").startsWith("rec_")));
    const recordingId = snapshots.findLast((item) => String(item.run_id || "").startsWith("rec_")).run_id;
    ws.send(JSON.stringify({ type: "finish", title: "冻结后断线" }));
    await waitFor(() => snapshots.some((item) => item.status === "processing"));
    ws.close();
    await waitFor(() => harness.controller.view(recordingId).status === "succeeded", 5000);
    await waitFor(() => catalog.list().length === 1);
    const view = harness.controller.view(recordingId);
    assert.equal(view.status, "succeeded");
    assert.equal(view.hasFinalResult, true);
    assert.equal(catalog.detail(recordingId).draft.capabilities[0].name, "create_leave");
  } finally {
    ws?.terminate();
    wss.close();
    await new Promise((resolve) => httpServer.close(resolve));
    await harness.cleanup();
  }
});

test("非协助时人点预览不会被丢弃，并与 PI 点进同一证据", async () => {
  const harness = await createHarness({ result: sampleResult() });
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "产出能力",
    });
    const browser = harness.controller.browserOf(started.id);
    assert.equal(harness.controller.acceptHumanInput(started.id), true);
    await browser.applyInput({ kind: "pointer_down", nx: 0.2, ny: 0.3 });
    await browser.actByRef({ ref: "a1", action: "click" });
    const events = await harness.files.readEvidence(started.id);
    const human = events.find((item) => item.kind === "interaction" && item.payload?.actor === "human");
    const pi = events.find((item) => item.kind === "interaction" && item.payload?.actor === "pi");
    assert.ok(human, "应留下人的交互");
    assert.ok(pi, "应留下 PI 的交互");
    assert.equal(harness.controller.view(started.id).human_can_click, true);
  } finally {
    await harness.cleanup();
  }
});

test("录制助手发话后能看到用户消息并交给 PI", async () => {
  const harness = await createHarness({ result: sampleResult() });
  const catalog = new ResultsCatalog(harness.files);
  const httpServer = createServer();
  const wss = attachFrontendBridge(httpServer, { controller: harness.controller, catalog });
  await new Promise((resolve) => httpServer.listen(0, "127.0.0.1", resolve));
  const { port } = httpServer.address();
  let ws;
  try {
    ws = await openRecorderSocket(port);
    const snapshots = listenSnapshots(ws);
    const thoughts = [];
    ws.on("message", (raw) => {
      let message;
      try {
        message = JSON.parse(String(raw));
      } catch {
        return;
      }
      if (message.type === "thought") thoughts.push(message);
    });
    ws.send(JSON.stringify({
      type: "start",
      start_url: "http://example.com",
      goal_text: "产出能力",
      title: "助手发话",
    }));
    await waitFor(() => snapshots.some((item) => String(item.run_id || "").startsWith("rec_")));
    ws.send(JSON.stringify({ type: "steer", text: "继续搜请假" }));
    await waitFor(() => harness.getPi()?.userMessages.includes("继续搜请假"));
    await waitFor(() => thoughts.some((item) => item.kind === "user" && item.text === "继续搜请假"));
    ws.send(JSON.stringify({ type: "abort" }));
    await waitFor(() => harness.getPi()?.aborted === true);
    assert.equal(harness.controller.view(snapshots.findLast((item) => String(item.run_id || "").startsWith("rec_")).run_id).status, "recording");
  } finally {
    ws?.terminate();
    wss.close();
    await new Promise((resolve) => httpServer.close(resolve));
    await harness.cleanup();
  }
});

test("采集中前台断开仍会取消且没有能力", async () => {
  const harness = await createHarness({
    result: sampleResult(),
    piBehavior: "submit_after_delay",
    piDelayMs: 400,
  });
  const catalog = new ResultsCatalog(harness.files);
  const httpServer = createServer();
  const wss = attachFrontendBridge(httpServer, { controller: harness.controller, catalog });
  await new Promise((resolve) => httpServer.listen(0, "127.0.0.1", resolve));
  const { port } = httpServer.address();
  let ws;
  try {
    ws = await openRecorderSocket(port);
    const snapshots = listenSnapshots(ws);
    ws.send(JSON.stringify({
      type: "start",
      start_url: "http://example.com",
      goal_text: "产出能力",
      title: "采集中断线",
    }));
    await waitFor(() => snapshots.some((item) => String(item.run_id || "").startsWith("rec_")));
    const recordingId = snapshots.findLast((item) => String(item.run_id || "").startsWith("rec_")).run_id;
    ws.close();
    await waitFor(() => harness.controller.view(recordingId).status === "failed");
    assert.equal(await harness.files.hasPiResult(recordingId), false);
    assert.equal(catalog.list().length, 0);
  } finally {
    ws?.terminate();
    wss.close();
    await new Promise((resolve) => httpServer.close(resolve));
    await harness.cleanup();
  }
});
