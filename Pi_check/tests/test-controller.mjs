/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { publicFailureMessage } from "../src/policy.mjs";

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

test("6. 证据未冻结时提交最终结果必须失败", async () => {
  const harness = await createHarness({ piBehavior: "submit_unfrozen" });
  try {
    const started = await harness.controller.start({ targetUrl: "http://example.com", goal: "目标" });
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(harness.getPi().unfrozenRejected, true);
    assert.equal(await harness.files.hasPiResult(started.id), false);
    await assert.rejects(() => harness.controller.stop(started.id), /失败/);
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
