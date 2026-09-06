/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { LivePiSession } from "../src/pi-session.mjs";

function busySession() {
  let busy = true;
  const prompts = [];
  return {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options, busy });
      if (busy && options.streamingBehavior !== "followUp") {
        throw new Error("Agent is already processing. Specify streamingBehavior ('steer' or 'followUp') to queue the message.");
      }
      busy = true;
    },
  };
}

test("录制中不打断 PI，冻结后只发一次最终提示，忙时改用 followUp", async () => {
  const session = busySession();
  const pi = new LivePiSession({
    session,
    sessionId: "pi_test",
    dispose: () => {},
  });
  pi.notifyEvidence({ seq: 11 });
  pi.notifyEvidence({ seq: 12 });
  assert.equal(session.prompts.length, 0);
  await pi.requestFinalAnalysis({ timeoutMs: 2000 });
  assert.equal(pi.status, "submitted");
  assert.equal(session.prompts.length, 2);
  assert.match(session.prompts[1].text, /证据已冻结/);
  assert.match(session.prompts[1].text, /不要写 capabilities\[\]\.fields/);
  assert.match(session.prompts[1].text, /list_recording_index/);
  assert.match(session.prompts[1].text, /台账/);
  assert.doesNotMatch(session.prompts[1].text, /现有录制页实际读取的合同/);
  assert.doesNotMatch(session.prompts[1].text, /先建动作台账/);
  assert.match(session.prompts[1].text, /不要把 JSON 写在对话里/);
  assert.match(session.prompts[1].text, /不要把 request_id 当 blob_id/);
  assert.match(session.prompts[1].text, /visible_control/);
  assert.match(session.prompts[1].text, /current_user/);
  assert.match(session.prompts[1].text, /确认弹层可填意见/);
  assert.match(session.prompts[1].text, /readonly\/disabled/);
  assert.match(session.prompts[1].text, /表头原文/);
  assert.match(session.prompts[1].text, /每个 exposed_to_user=true 的 param 都必须出现在 schema/);
  assert.match(session.prompts[1].text, /树\/页签\/分段器\/单选组\/日期区间/);
  assert.match(session.prompts[1].text, /不要读 screenshot/);
  assert.ok(session.prompts[1].text.length < 1600, `最终提示过长: ${session.prompts[1].text.length}`);
  assert.equal(session.prompts[1].options.streamingBehavior, "followUp");
});

test("分析停住时催促提交，但不中止当前轮", async () => {
  const prompts = [];
  let rejectPending;
  const pending = new Promise((_, reject) => {
    rejectPending = reject;
  });
  const session = {
    prompts,
    aborted: 0,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer") return;
      await pending;
    },
    async abort() {
      this.aborted += 1;
      rejectPending?.(new Error("aborted"));
    },
  };
  const { createPiTrace } = await import("../src/pi-trace.mjs");
  const trace = createPiTrace();
  trace.recordTool("list_recording_index", {}, "items=2", true);
  const pi = new LivePiSession({
    session,
    sessionId: "pi_idle",
    dispose: () => {},
    trace,
  });
  await assert.rejects(
    () => pi.requestFinalAnalysis({ timeoutMs: 70, idleSubmitMs: 40 }),
    /超时/,
  );
  const steers = prompts.filter((item) => item.options.streamingBehavior === "steer" && /submit_recording_result/.test(item.text));
  assert.equal(steers.length, 1);
  assert.equal(prompts.filter((item) => !item.options.streamingBehavior && /证据已经够了/.test(item.text)).length, 0);
});

test("第二次空转时中止当前轮并要求立刻提交", async () => {
  const prompts = [];
  let rejectPending;
  let pending = new Promise((_, reject) => {
    rejectPending = reject;
  });
  const session = {
    prompts,
    aborted: 0,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer") return;
      await pending;
    },
    async abort() {
      this.aborted += 1;
      rejectPending?.(new Error("aborted"));
      pending = new Promise((_, reject) => {
        rejectPending = reject;
      });
    },
  };
  const { createPiTrace } = await import("../src/pi-trace.mjs");
  const trace = createPiTrace();
  trace.recordTool("list_recording_index", {}, "items=2", true);
  const pi = new LivePiSession({
    session,
    sessionId: "pi_idle_abort",
    dispose: () => {},
    trace,
  });
  await pi.requestFinalAnalysis({
    timeoutMs: 400,
    idleSubmitMs: 40,
    hasResult: async () => (
      session.aborted >= 1
      && prompts.some((item) => /证据已经够了/.test(item.text) && !item.options.streamingBehavior)
    ),
  });
  assert.equal(pi.status, "submitted");
  assert.ok(session.aborted >= 1);
  assert.ok(prompts.some((item) => item.options.streamingBehavior === "steer"));
  assert.ok(prompts.some((item) => /证据已经够了/.test(item.text) && !item.options.streamingBehavior));
});

test("关闭会话后立即停止最终分析，不再继续 prompt", async () => {
  const prompts = [];
  let rejectPending;
  const pending = new Promise((_, reject) => {
    rejectPending = reject;
  });
  const session = {
    prompts,
    aborted: 0,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      await pending;
    },
    async abort() {
      this.aborted += 1;
      rejectPending?.(new Error("aborted"));
    },
    dispose() {},
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_close",
    dispose: () => {},
  });
  const analysis = pi.requestFinalAnalysis({
    timeoutMs: 60000,
    idleSubmitMs: 90000,
    hasResult: async () => false,
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  const countBeforeClose = session.prompts.length;
  assert.ok(countBeforeClose >= 1);
  await pi.close();
  await assert.rejects(() => analysis, /会话已关闭/);
  assert.equal(pi.status, "closed");
  assert.equal(pi.alive, false);
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.equal(session.prompts.length, countBeforeClose);
  assert.ok(session.aborted >= 1);
});

test("本轮结束但未提交时继续催促，直到有结果", async () => {
  const prompts = [];
  let rounds = 0;
  const session = {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer") return;
      rounds += 1;
    },
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_retry",
    dispose: () => {},
  });
  await pi.requestFinalAnalysis({
    timeoutMs: 400,
    idleSubmitMs: 1000,
    hasResult: async () => rounds >= 2,
  });
  assert.equal(pi.status, "submitted");
  assert.ok(prompts.some((item) => /证据已经够了/.test(item.text) && !item.options.streamingBehavior));
});

test("PI 空转未提交时停止分析，不刷屏重试", async () => {
  const prompts = [];
  const session = {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options, at: Date.now() });
    },
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_spin",
    dispose: () => {},
  });
  const started = Date.now();
  const analysis = pi.requestFinalAnalysis({
    timeoutMs: 60000,
    idleSubmitMs: 90000,
    hasResult: async () => false,
  });
  const outcome = await Promise.race([
    analysis.then(
      () => ({ kind: "ok" }),
      (error) => ({ kind: "err", message: String(error?.message || error) }),
    ),
    new Promise((resolve) => setTimeout(() => resolve({ kind: "hung" }), 400)),
  ]);
  assert.notEqual(outcome.kind, "hung", `空转未让出事件循环，已 prompt ${session.prompts.length} 次`);
  assert.equal(outcome.kind, "err");
  assert.match(outcome.message, /空转|未调用工具|未提交/);
  assert.ok(session.prompts.length <= 4, `prompt 次数过多: ${session.prompts.length}`);
  assert.ok(Date.now() - started < 1500);
  assert.equal(pi.status, "failed");
});

test("读完证据后生成提交被中止，随后空轮不得立刻判失败", async () => {
  const prompts = [];
  let rejectPending;
  let pending = new Promise((_, reject) => {
    rejectPending = reject;
  });
  let postAbortPrompts = 0;
  let submitted = false;
  const session = {
    prompts,
    aborted: 0,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer") return;
      if (this.aborted === 0) {
        await pending;
        return;
      }
      postAbortPrompts += 1;
      if (postAbortPrompts >= 5) submitted = true;
    },
    async abort() {
      this.aborted += 1;
      rejectPending?.(new Error("aborted"));
      pending = new Promise((_, reject) => {
        rejectPending = reject;
      });
    },
  };
  const { createPiTrace } = await import("../src/pi-trace.mjs");
  const trace = createPiTrace();
  for (const seq of [604, 612, 632, 839, 901, 905]) {
    trace.recordTool("read_evidence_item", { seq }, "ok", true);
  }
  const pi = new LivePiSession({
    session,
    sessionId: "pi_abort_empty",
    dispose: () => {},
    trace,
  });
  await pi.requestFinalAnalysis({
    timeoutMs: 1500,
    idleSubmitMs: 40,
    hasResult: async () => submitted,
  });
  assert.equal(pi.status, "submitted");
  assert.ok(session.aborted >= 1);
  assert.ok(submitted);
  assert.ok(postAbortPrompts >= 5);
});

test("模型仍在流式产出时不算空闲，不得中止当前轮", async () => {
  const prompts = [];
  let resolvePending;
  const pending = new Promise((resolve) => {
    resolvePending = resolve;
  });
  const session = {
    prompts,
    aborted: 0,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer") return;
      await pending;
    },
    async abort() {
      this.aborted += 1;
    },
  };
  const { createPiTrace } = await import("../src/pi-trace.mjs");
  const trace = createPiTrace();
  trace.recordTool("list_recording_index", {}, "items=2", true);
  const pi = new LivePiSession({
    session,
    sessionId: "pi_stream_idle",
    dispose: () => {},
    trace,
  });
  const started = Date.now();
  const pump = setInterval(() => {
    trace.handleEvent({ type: "text_delta", delta: "{" });
  }, 15);
  try {
    await pi.requestFinalAnalysis({
      timeoutMs: 400,
      idleSubmitMs: 40,
      hasResult: async () => Date.now() - started > 160,
    });
  } finally {
    clearInterval(pump);
    resolvePending?.();
  }
  assert.equal(pi.status, "submitted");
  assert.equal(session.aborted, 0);
  assert.equal(prompts.filter((item) => item.options.streamingBehavior === "steer").length, 0);
});

test("运行时没有 abort 时不得另开一轮空转", async () => {
  const prompts = [];
  let resolvePending;
  const pending = new Promise((resolve) => {
    resolvePending = resolve;
  });
  const session = {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer" || options.streamingBehavior === "followUp") {
        return;
      }
      await pending;
    },
  };
  const { createPiTrace } = await import("../src/pi-trace.mjs");
  const trace = createPiTrace();
  trace.recordTool("read_evidence_item", { seq: 901 }, "ok", true);
  const pi = new LivePiSession({
    session,
    sessionId: "pi_no_abort",
    dispose: () => {},
    trace,
  });
  const started = Date.now();
  try {
    await pi.requestFinalAnalysis({
      timeoutMs: 400,
      idleSubmitMs: 40,
      hasResult: async () => Date.now() - started > 200,
    });
  } finally {
    resolvePending?.();
  }
  assert.equal(pi.status, "submitted");
  const blocking = prompts.filter((item) => !item.options.streamingBehavior);
  assert.equal(blocking.length, 1);
  assert.equal(prompts.filter((item) => item.options.streamingBehavior === "followUp").length, 0);
});

test("PI prompt 抛错后停止分析，不再立刻重开一轮", async () => {
  const prompts = [];
  const session = {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      throw new Error("model request failed");
    },
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_error",
    dispose: () => {},
  });
  const analysis = pi.requestFinalAnalysis({
    timeoutMs: 60000,
    idleSubmitMs: 90000,
    hasResult: async () => false,
  });
  const outcome = await Promise.race([
    analysis.then(
      () => ({ kind: "ok" }),
      (error) => ({ kind: "err", message: String(error?.message || error) }),
    ),
    new Promise((resolve) => setTimeout(() => resolve({ kind: "hung" }), 400)),
  ]);
  assert.notEqual(outcome.kind, "hung", `异常后仍在空转，已 prompt ${session.prompts.length} 次`);
  assert.equal(outcome.kind, "err");
  assert.match(outcome.message, /model request failed/);
  assert.ok(session.prompts.length <= 2, `异常后仍在重试: ${session.prompts.length}`);
  assert.equal(pi.status, "failed");
});
