/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness } from "./helpers/harness.mjs";
import { createPiToolHost } from "../src/pi-tools.mjs";
import { assignSnapshotRefs } from "../src/browser-snapshot.mjs";

test("Control In App Browser 与人点击共用同一浏览器，协助不锁预览", async () => {
  const harness = await createHarness();
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    const browser = harness.controller.browserOf(started.id);
    const tools = createPiToolHost({
      recordingId: started.id,
      evidence: harness.evidence,
      files: harness.files,
      gate: harness.gate,
      getPiSessionId: () => harness.evidence.snapshot(started.id).piSessionId,
      getBrowser: () => harness.controller.browserOf(started.id),
      onAssist: (payload) => harness.controller.requestAssist(started.id, payload?.reason || ""),
    });
    const opened = await tools.control_in_app_browser({ action: "open_page", url: "http://example.com" });
    assert.equal(opened.available, true);
    const shot = await tools.control_in_app_browser({ action: "snapshot" });
    assert.ok(shot.actions?.length);
    const clicked = await tools.control_in_app_browser({ action: "click", ref: "a1" });
    assert.equal(clicked.ok, true);
    await browser.applyInput({ kind: "pointer_down", nx: 0.4, ny: 0.5 });
    const assist = await tools.control_in_app_browser({ action: "assist", reason: "请输入验证码" });
    assert.equal(assist.assist, true);
    assert.equal(assist.human_can_click, true);
    assert.equal(harness.controller.acceptHumanInput(started.id), true);
    const events = await harness.files.readEvidence(started.id);
    assert.ok(events.some((item) => item.kind === "interaction" && item.payload?.actor === "pi"));
    assert.ok(events.some((item) => item.kind === "interaction" && item.payload?.actor === "human"));
    assert.match(harness.controller.view(started.id).assist.reason, /验证码/);
  } finally {
    await harness.cleanup();
  }
});

test("PI 动作卡住时人手 applyInput 仍立即执行", async () => {
  const harness = await createHarness();
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    const browser = harness.controller.browserOf(started.id);
    browser.enqueue?.(async () => {
      await new Promise(() => {});
    });
    const startedAt = Date.now();
    await browser.applyInput({ kind: "pointer_down", nx: 0.2, ny: 0.2 });
    assert.ok(Date.now() - startedAt < 200, "人手点击被自动点击队列堵住了");
    assert.equal(harness.controller.acceptHumanInput(started.id), true);
  } finally {
    await harness.cleanup();
  }
});

test("snapshot 给控件打稳定 ref", () => {
  const view = assignSnapshotRefs({
    url: "http://x/page",
    controls: [{ label: "关键字" }],
    actions: [{ label: "查询" }, { label: "新增" }],
  });
  assert.equal(view.controls[0].ref, "c1");
  assert.equal(view.actions[0].ref, "a1");
  assert.equal(view.actions[1].ref, "a2");
});
