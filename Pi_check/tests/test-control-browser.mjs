/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness } from "./helpers/harness.mjs";
import { createPiToolHost } from "../src/pi-tools.mjs";
import { assignSnapshotRefs } from "../src/browser-snapshot.mjs";
import {
  parseLocator,
  snapshotSelector,
  isNoiseNetworkPath,
  resolveInteractionActor,
  shouldSteerHumanAct,
  expectedClickLabel,
  clickLabelMatches,
} from "../src/browser-actions.mjs";
import { stripImageFromToolResult } from "../src/pi-tools.mjs";

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

test("snapshot 给控件打稳定 ref 和语义 selector", () => {
  const view = assignSnapshotRefs({
    url: "http://x/page",
    controls: [{ label: "关键字", placeholder: "请输入单据编号" }, { label: "请假类型" }],
    actions: [{ label: "查询" }, { label: "新增" }],
  });
  assert.equal(view.controls[0].ref, "c1");
  assert.equal(view.controls[0].selector, "placeholder=请输入单据编号");
  assert.equal(view.controls[1].selector, "label=请假类型");
  assert.equal(view.actions[0].ref, "a1");
  assert.equal(view.actions[0].selector, 'role=button[name="查询"]');
  assert.equal(view.actions[1].ref, "a2");
  assert.equal(snapshotSelector({ label: "新增" }, "action"), 'role=button[name="新增"]');
  assert.deepEqual(parseLocator("placeholder=请选择请假类型"), {
    kind: "placeholder",
    value: "请选择请假类型",
  });
  assert.equal(parseLocator('role=button[name="搜索"]').kind, "role");
  assert.equal(expectedClickLabel('role=button[name="甲"]').expected, "甲");
  assert.equal(expectedClickLabel('role=button[name="甲"]').enforce, true);
  assert.equal(clickLabelMatches("搜 索", "搜索"), true);
  assert.equal(clickLabelMatches("甲", "乙"), false);
  assert.equal(expectedClickLabel("label=开始日期").enforce, false);
  assert.equal(parseLocator("c2").kind, "ref");
  assert.deepEqual(parseLocator("type=checkbox"), { kind: "role", role: "checkbox", name: "", value: "checkbox" });
  assert.equal(snapshotSelector({ label: "张三", kind: "checkbox" }, "action"), 'role=checkbox[name="张三"]');
  assert.equal(snapshotSelector({ label: "张三", kind: "row" }, "action"), "text=张三");
});

test("choose 用语义选择器一次选中，不必再 snapshot", async () => {
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
      getBrowser: () => browser,
    });
    const chosen = await tools.control_in_app_browser({
      action: "choose",
      selector: "placeholder=请选择请假类型",
      text: "事假",
    });
    assert.equal(chosen.ok, true);
    assert.equal(chosen.selector, "placeholder=请选择请假类型");
    assert.ok(browser.acts.some((item) => item.action === "choose" && item.text === "事假"));
    const clicked = await tools.control_in_app_browser({
      action: "click",
      selector: 'role=button[name="搜索"]',
    });
    assert.equal(clicked.ok, true);
    const shot = await tools.control_in_app_browser({ action: "snapshot", include_screenshot: true });
    assert.equal(shot.screenshot?.data, undefined);
    assert.equal(shot.__image, undefined);
    assert.equal(shot.data, undefined);
    assert.equal(shot.image_in_conversation, false);
    assert.ok(Array.isArray(shot.recentUserActions) || shot.controls || shot.url);
    const pictured = await tools.control_in_app_browser({ action: "screenshot" });
    assert.equal(pictured.__image, undefined);
    assert.equal(pictured.data, undefined);
    assert.equal(pictured.image_in_conversation, false);
    assert.match(String(pictured.note || ""), /不写入对话/);
    assert.ok(pictured.controls || pictured.url);
  } finally {
    await harness.cleanup();
  }
});

test("PI 自己点出的页面 hook 不得当成人手去打断", () => {
  const acting = { isPiActing: () => true };
  assert.equal(resolveInteractionActor(acting, { kind: "click", text: "新增" }), "pi");
  assert.equal(resolveInteractionActor({ isPiActing: () => false }, { kind: "click" }), "human");
  assert.equal(shouldSteerHumanAct("interaction", { actor: "pi", kind: "click" }), false);
  assert.equal(shouldSteerHumanAct("interaction", { actor: "human", kind: "input" }), false);
  assert.equal(shouldSteerHumanAct("interaction", { actor: "human", kind: "click" }), true);
  assert.equal(isNoiseNetworkPath("http://x/prod-api/im/chatMessage/getChatNotReadMessageCount"), true);
  assert.equal(isNoiseNetworkPath("http://x/prod-api/oa/dutyApply/list"), false);
});

test("PI 点击后页面 hook 不得累计人手打断", async () => {
  const harness = await createHarness();
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    const browser = harness.controller.browserOf(started.id);
    browser.beginPiAct?.();
    await browser.appendEvidence("interaction", { kind: "click", text: "新增" });
    browser.endPiAct?.();
    assert.equal(harness.getPi().humanActs, 0);
    await browser.applyInput({ kind: "pointer_down", nx: 0.2, ny: 0.3 });
    assert.equal(harness.getPi().humanActs, 1);
  } finally {
    await harness.cleanup();
  }
});

test("工具结果里的截图二进制不得进入 PI 对话", () => {
  const stripped = stripImageFromToolResult({
    __image: true,
    data: "AAAA",
    mimeType: "image/jpeg",
    url: "http://fixture.local/demo",
    width: 10,
    height: 10,
  });
  assert.equal(stripped.__image, undefined);
  assert.equal(stripped.data, undefined);
  assert.equal(stripped.image_in_conversation, false);
  assert.equal(stripped.url, "http://fixture.local/demo");
  const plain = stripImageFromToolResult({ ok: true, path: "/api/leave" });
  assert.deepEqual(plain, { ok: true, path: "/api/leave" });
});
