/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { publicFailureMessage } from "../src/policy.mjs";

function waitUntil(predicate, timeoutMs = 2000) {
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
      setTimeout(tick, 15);
    };
    tick();
  });
}

test("1. PI 启动失败时，浏览器录制不启动", async () => {
  const harness = await createHarness({ piFailStart: true });
  try {
    await assert.rejects(
      () => harness.controller.start({ targetUrl: "http://example.com", goal: "目标" }),
      /PI/,
    );
    assert.equal(harness.browserCalls.length, 0);
    const sessions = harness.evidence.list();
    assert.equal(sessions[0].status, "failed");
    assert.equal(sessions[0].browserStatus, "idle");
    assert.equal(await harness.files.hasPiResult(sessions[0].id), false);
    assert.equal(harness.controller.view(sessions[0].id).capabilityCount, 0);
  } finally {
    await harness.cleanup();
  }
});

test("2. PI 中途失败时，录制失败且没有最终结果", async () => {
  const harness = await createHarness({ piBehavior: "die_on_notify" });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    await new Promise((resolve) => setTimeout(resolve, 50));
    const view = harness.controller.view(started.id);
    assert.equal(view.status, "failed");
    assert.equal(view.publicMessage, publicFailureMessage());
    assert.equal(await harness.files.hasPiResult(started.id), false);
    assert.equal(view.capabilityCount, 0);
  } finally {
    await harness.cleanup();
  }
});

test("3. PI 未调用最终提交工具时，停止录制必须失败", async () => {
  const harness = await createHarness({ piBehavior: "never_submit" });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    await assert.rejects(() => harness.controller.stop(started.id), /失败/);
    assert.equal(harness.controller.view(started.id).status, "failed");
    assert.equal(await harness.files.hasPiResult(started.id), false);
    assert.equal(harness.controller.view(started.id).capabilityCount, 0);
  } finally {
    await harness.cleanup();
  }
});

test("4. PI 提交空结果时必须失败", async () => {
  const harness = await createHarness({ piBehavior: "empty_result" });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    await assert.rejects(() => harness.controller.stop(started.id), /失败/);
    assert.equal(await harness.files.hasPiResult(started.id), false);
  } finally {
    await harness.cleanup();
  }
});

test("5. PI 提交错误录制编号时必须失败", async () => {
  const harness = await createHarness({ piBehavior: "wrong_id" });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    await assert.rejects(() => harness.controller.stop(started.id), /失败/);
    assert.equal(await harness.files.hasPiResult(started.id), false);
  } finally {
    await harness.cleanup();
  }
});

test("6. PI 可通过工具自行冻结并定稿", async () => {
  const harness = await createHarness({ piBehavior: "submit_unfrozen" });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    await new Promise((resolve) => setTimeout(resolve, 80));
    assert.equal(harness.getPi().unfrozenRejected, false);
    assert.equal(await harness.files.hasPiResult(started.id), true);
    const stopped = await harness.controller.stop(started.id);
    assert.equal(stopped.session.status, "succeeded");
    assert.equal(stopped.session.hasFinalResult, true);
  } finally {
    await harness.cleanup();
  }
});

test("6c. 自动点击失败不得结束录制，人手点预览和停录分析仍可用", async () => {
  const harness = await createHarness({ piBehavior: "drive_fail" });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    await new Promise((resolve) => setTimeout(resolve, 40));
    assert.equal(harness.controller.view(started.id).status, "recording");
    assert.equal(harness.controller.acceptHumanInput(started.id), true);
    const browser = harness.controller.browserOf(started.id);
    await browser.applyInput({ kind: "pointer_down", nx: 0.3, ny: 0.4 });
    const events = await harness.files.readEvidence(started.id);
    assert.ok(events.some((item) => item.kind === "interaction" && item.payload?.actor === "human"));
    const stopped = await harness.controller.stop(started.id);
    assert.equal(stopped.session.status, "succeeded");
  } finally {
    await harness.cleanup();
  }
});

test("瞬间空转后发话会新开 PI 再继续，不复用失效会话", async () => {
  const harness = await createHarness();
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    const firstId = harness.getPi().sessionId;
    assert.equal(harness.getPiCreateCount(), 1);
    harness.getPi().lastStopReason = "instant_empty";
    harness.getPi().driveStopped = true;
    harness.getPi().status = "ready";
    const sent = await harness.controller.steer(started.id, "继续");
    assert.equal(sent.ok, true);
    assert.equal(harness.getPiCreateCount(), 2);
    assert.notEqual(harness.getPi().sessionId, firstId);
    assert.equal(harness.controller.view(started.id).status, "recording");
    assert.ok(harness.getPi().userMessages.includes("继续"));
  } finally {
    await harness.cleanup();
  }
});

test("最终分析空转时重建 PI 再提交，不直接关会话", async () => {
  const harness = await createHarness({
    piBehaviorForCreate: (count) => (count === 1 ? "empty_spin" : "submit_on_final"),
  });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    assert.equal(harness.getPiCreateCount(), 1);
    const stopped = await harness.controller.stop(started.id);
    assert.equal(stopped.session.status, "succeeded");
    assert.equal(harness.getPiCreateCount(), 2);
    assert.equal(await harness.files.hasPiResult(started.id), true);
  } finally {
    await harness.cleanup();
  }
});

test("人在录制页发话会交给 PI，终止后能再继续", async () => {
  const harness = await createHarness();
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    const sent = await harness.controller.steer(started.id, "继续搜请假");
    assert.equal(sent.ok, true);
    assert.ok(harness.getPi().userMessages.includes("继续搜请假"));
    await harness.controller.stopPiWork(started.id);
    assert.equal(harness.getPi().aborted, true);
    await harness.controller.steer(started.id, "去点新增");
    assert.ok(harness.getPi().userMessages.includes("去点新增"));
    assert.equal(harness.controller.view(started.id).status, "recording");
  } finally {
    await harness.cleanup();
  }
});

test("6b. 录制中人始终可以点预览", async () => {
  const harness = await createHarness();
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    assert.equal(harness.controller.acceptHumanInput(started.id), true);
    assert.equal(harness.controller.view(started.id).human_can_click, true);
    harness.controller.requestAssist(started.id, "请登录");
    assert.equal(harness.controller.acceptHumanInput(started.id), true);
    assert.match(harness.controller.view(started.id).assist.reason, /请登录/);
    assert.equal(harness.controller.view(started.id).assist_paused, true);
    assert.equal(harness.getPi().driveStopped, true);
    assert.equal(harness.getPi().lastStopReason, "assist");
  } finally {
    await harness.cleanup();
  }
});

test("7-9. 成功提交时保存内容与 PI 原始提交完全一致，代码不得增改", async () => {
  const result = sampleResult({
    keep_me: { nested: true, n: 7 },
    capabilities: [{ id: "only-one", title: "PI 原样" }],
  });
  const harness = await createHarness({ result });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    const stopped = await harness.controller.stop(started.id);
    assert.equal(stopped.session.status, "succeeded");
    assert.deepEqual(stopped.result, result);
    assert.equal(Object.keys(stopped.result).includes("defaultValue"), false);
    assert.equal(stopped.result.capabilities.length, 1);
    assert.equal(stopped.receipt.recording_id, started.id);
    assert.equal(Object.hasOwn(stopped.result, "accepted_at"), false);
  } finally {
    await harness.cleanup();
  }
});

test("自动操作中定稿必须先通知前台，拆现场不得卡住当前工具", async () => {
  let completed = 0;
  const harness = await createHarness({ piBehavior: "submit_on_drive" });
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "目标",
      onComplete: () => {
        completed += 1;
      },
    });
    await waitUntil(() => (
      completed > 0
      && harness.controller.view(started.id).status === "succeeded"
      && harness.getPi()?.submitReturned
    ));
    assert.equal(completed, 1);
    assert.equal(harness.controller.view(started.id).hasFinalResult, true);
    await waitUntil(() => harness.getPi()?.alive === false);
    assert.equal(harness.getPi().closeDuringTool, false);
  } finally {
    await harness.cleanup();
  }
});

test("自动操作超时后定稿仍要收口并通知前台", async () => {
  let completed = 0;
  const harness = await createHarness({ piBehavior: "drive_fail" });
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "目标",
      onComplete: () => {
        completed += 1;
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 40));
    assert.equal(harness.controller.view(started.id).status, "recording");
    const accepted = await harness.getPi().callSubmit(sampleResult());
    assert.equal(accepted.accepted, true);
    assert.equal(harness.getPi().submitReturned, true);
    assert.equal(harness.controller.view(started.id).status, "succeeded");
    assert.equal(harness.controller.view(started.id).hasFinalResult, true);
    assert.equal(completed, 1);
    await waitUntil(() => harness.getPi()?.alive === false);
    assert.equal(harness.getPi().closeDuringTool, false);
  } finally {
    await harness.cleanup();
  }
});

test("10. 同一录制不得接收第二个最终结果", async () => {
  const result = sampleResult({ marker: "first" });
  const harness = await createHarness({ piBehavior: "submit_twice", result });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    const stopped = await harness.controller.stop(started.id);
    assert.deepEqual(stopped.result, result);
    assert.equal(stopped.result.injected_by_second_submit, undefined);
    assert.match(harness.getPi().secondSubmitError, /第二个最终结果/);
  } finally {
    await harness.cleanup();
  }
});
