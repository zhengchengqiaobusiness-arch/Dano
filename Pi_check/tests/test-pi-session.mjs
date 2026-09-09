/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { isAbortResidueTurn, isEmptySpinError, isInstantEmptyTurn, LivePiSession } from "../src/pi-session.mjs";

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

test("beginLiveDrive 立即发出操作者提示，notifyEvidence 仍不打断", async () => {
  const prompts = [];
  let releasePrompt;
  const session = {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer") return;
      await new Promise((resolve) => {
        releasePrompt = resolve;
      });
    },
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_drive",
    dispose: () => {},
  });
  const drive = pi.beginLiveDrive({
    targetUrl: "http://example.com",
    goal: "做成能力",
    timeoutMs: 5000,
    idleSubmitMs: 4000,
    hasResult: async () => false,
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.ok(prompts.length >= 1);
  assert.match(prompts[0].text, /Investigator/);
  assert.match(prompts[0].text, /做成能力/);
  assert.match(prompts[0].text, /http:\/\/example.com/);
  const before = prompts.length;
  pi.notifyEvidence({ seq: 11 });
  pi.notifyEvidence({ seq: 12 });
  assert.equal(prompts.length, before);
  pi.notifyHumanAct({ seq: 13 });
  pi.notifyHumanAct({ seq: 14 });
  pi.notifyHumanAct({ seq: 15 });
  assert.equal(prompts.filter((item) => item.options.streamingBehavior === "steer" && /用户刚在预览/.test(item.text)).length, 1);
  await pi.stopLiveDrive();
  releasePrompt?.();
  await drive;
  assert.equal(pi.status, "ready");
});

test("用户发话在自动点击中走 steer，停掉后要求重新开车", async () => {
  const prompts = [];
  const thoughts = [];
  let releasePrompt;
  const pending = new Promise((resolve) => {
    releasePrompt = resolve;
  });
  const session = {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer") return;
      await pending;
    },
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_user_steer",
    dispose: () => {},
    onThought: (item) => thoughts.push(item),
  });
  const drive = pi.beginLiveDrive({
    targetUrl: "http://example.com",
    goal: "目标",
    timeoutMs: 5000,
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  const during = await pi.notifyUserMessage("继续搜请假");
  assert.equal(during.ok, true);
  assert.equal(during.resumeDrive, false);
  assert.ok(thoughts.some((item) => item.kind === "user" && item.text === "继续搜请假"));
  assert.equal(prompts.filter((item) => item.options.streamingBehavior === "steer" && /继续搜请假/.test(item.text)).length, 1);
  const aborted = await pi.abortLiveWork();
  assert.equal(aborted.ok, true);
  releasePrompt?.();
  await drive;
  assert.equal(pi.status, "ready");
  const after = await pi.notifyUserMessage("去点新增");
  assert.equal(after.ok, true);
  assert.equal(after.resumeDrive, true);
});

test("pauseForAssist 必须停掉当前自动点击，用户发话才续跑", async () => {
  const prompts = [];
  const thoughts = [];
  let releasePrompt;
  const pending = new Promise((resolve) => {
    releasePrompt = resolve;
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
  const pi = new LivePiSession({
    session,
    sessionId: "pi_assist_hold",
    dispose: () => {},
    onThought: (item) => thoughts.push(item),
  });
  const drive = pi.beginLiveDrive({
    targetUrl: "http://example.com",
    goal: "正常提交日报",
    timeoutMs: 5000,
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  const paused = await pi.pauseForAssist("请选择完成进度");
  assert.equal(paused.ok, true);
  assert.equal(pi.driveStopped, true);
  assert.equal(pi.lastStopReason, "assist");
  assert.equal(pi.isDriving, false);
  assert.ok(session.aborted >= 1);
  assert.ok(thoughts.some((item) => /已暂停自动操作/.test(item.text || "")));
  releasePrompt?.();
  await drive;
  assert.equal(pi.status, "ready");
  const after = await pi.notifyUserMessage("继续");
  assert.equal(after.ok, true);
  assert.equal(after.resumeDrive, true);
});

test("空转停掉后发话会中止残留轮，再开新对话而不是 followUp 假死", async () => {
  const prompts = [];
  let busy = false;
  const session = {
    prompts,
    aborted: 0,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (busy && options.streamingBehavior !== "steer" && options.streamingBehavior !== "followUp") {
        throw new Error("Agent is already processing. Specify streamingBehavior ('steer' or 'followUp') to queue the message.");
      }
      if (options.streamingBehavior === "followUp") return { queued: true };
      if (options.streamingBehavior === "steer") return;
    },
    async abort() {
      this.aborted += 1;
      busy = false;
    },
  };
  const thoughts = [];
  const pi = new LivePiSession({
    session,
    sessionId: "pi_resume_talk",
    dispose: () => {},
    onThought: (item) => thoughts.push(item),
  });
  await pi.beginLiveDrive({
    targetUrl: "http://example.com",
    goal: "目标",
    timeoutMs: 80,
    idleSubmitMs: 20,
    maxEmptySettles: 1,
    hasResult: async () => false,
  });
  assert.equal(pi.status, "ready");
  busy = true;
  const sent = await pi.notifyUserMessage("继续");
  assert.equal(sent.ok, true);
  assert.equal(sent.resumeDrive, true);
  assert.ok(session.aborted >= 1);
  assert.ok(thoughts.some((item) => item.kind === "user" && item.text === "继续"));
  assert.ok(thoughts.some((item) => item.text === "已收到，正在按你的话继续"));
  const before = prompts.length;
  await pi.beginLiveDrive({
    resumeHint: "继续",
    targetUrl: "http://example.com",
    goal: "目标",
    timeoutMs: 200,
    maxEmptySettles: 1,
    hasResult: async () => false,
  });
  const resumed = prompts.slice(before);
  assert.ok(resumed.some((item) => /用户说：继续/.test(item.text) && item.options.streamingBehavior !== "followUp"));
  assert.equal(resumed.some((item) => item.options.streamingBehavior === "followUp"), false);
});

test("瞬间空轮会标记会话失效，停自动点但不关会话", async () => {
  const session = {
    async prompt() {},
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_instant",
    dispose: () => {},
  });
  await pi.beginLiveDrive({
    targetUrl: "http://example.com",
    goal: "做成能力",
    timeoutMs: 2000,
    idleSubmitMs: 1000,
    maxEmptySettles: 5,
    hasResult: async () => false,
  });
  assert.equal(pi.alive, true);
  assert.equal(pi.status, "ready");
  assert.equal(pi.lastStopReason, "instant_empty");
  assert.equal(pi.needsFreshSession, true);
  assert.equal(isInstantEmptyTurn({ elapsedMs: 80, hadNewTools: false, abortResidue: false }), true);
  assert.equal(isInstantEmptyTurn({ elapsedMs: 80, hadNewTools: true, abortResidue: false }), false);
  assert.equal(isAbortResidueTurn({ interrupted: true, hadNewTools: false, hadModelText: false }), true);
  assert.equal(isAbortResidueTurn({ interrupted: true, hadNewTools: false, hadModelText: true }), false);
  assert.equal(isEmptySpinError(new Error("PI 连续空转未调用工具且未提交")), true);
});

test("卡住中止后的空轮不得因 agent_end 被算成瞬间空转", async () => {
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
      if (this.aborted === 0) {
        await pending;
        return;
      }
    },
    async abort() {
      this.aborted += 1;
      rejectPending?.(new Error("aborted"));
    },
  };
  const { createPiTrace } = await import("../src/pi-trace.mjs");
  const trace = createPiTrace();
  trace.recordTool("control_in_app_browser", { action: "click", selector: "type=checkbox" }, "失败", false);
  const thoughts = [];
  const pi = new LivePiSession({
    session,
    sessionId: "pi_abort_drive",
    dispose: () => {},
    trace,
    onThought: (item) => thoughts.push(item),
  });
  const drive = pi.beginLiveDrive({
    targetUrl: "http://example.com",
    goal: "目标",
    timeoutMs: 2500,
    idleSubmitMs: 40,
    maxEmptySettles: 5,
    hasResult: async () => false,
  });
  const deadline = Date.now() + 1500;
  while (!thoughts.some((item) => /中止后继续点击/.test(item.text || ""))) {
    if (Date.now() >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.ok(thoughts.some((item) => /中止后继续点击/.test(item.text || "")));
  for (let index = 0; index < 8; index += 1) {
    trace.handleEvent({ type: "agent_end" });
  }
  assert.notEqual(pi.lastStopReason, "instant_empty");
  assert.equal(pi.driveStopped, false);
  assert.ok(session.aborted >= 1);
  await pi.stopLiveDrive();
  await drive;
});

test("自动点击空转时只停自动点，不把会话打成失败", async () => {
  const prompts = [];
  const session = {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
    },
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_drive_idle",
    dispose: () => {},
  });
  await pi.beginLiveDrive({
    targetUrl: "http://example.com",
    goal: "做成能力",
    timeoutMs: 80,
    idleSubmitMs: 20,
    maxEmptySettles: 1,
    hasResult: async () => false,
  });
  assert.equal(pi.alive, true);
  assert.notEqual(pi.status, "failed");
  assert.equal(pi.status, "ready");
});

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
  assert.match(session.prompts[1].text, /Skill 1/);
  assert.match(session.prompts[1].text, /不要把 JSON 写在对话里/);
  assert.doesNotMatch(session.prompts[1].text, /current_user/);
  assert.doesNotMatch(session.prompts[1].text, /确认弹层/);
  assert.doesNotMatch(session.prompts[1].text, /x-dano-section-titles/);
  assert.doesNotMatch(session.prompts[1].text, /部门树/);
  assert.doesNotMatch(session.prompts[1].text, /不要读 screenshot/);
  assert.ok(session.prompts[1].text.length < 800, `最终提示过长: ${session.prompts[1].text.length}`);
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
    () => pi.requestFinalAnalysis({ timeoutMs: 90, idleSubmitMs: 50 }),
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

test("已有草稿时最终分析先催 use_draft，不重读证据", async () => {
  const prompts = [];
  const session = {
    async prompt(text, options = {}) {
      prompts.push({ text, options });
    },
    dispose() {},
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_draft",
    dispose: () => {},
  });
  await pi.requestFinalAnalysis({
    timeoutMs: 400,
    hasResult: async () => true,
    hasDraft: async () => true,
  });
  assert.match(prompts[0].text, /use_draft:true/);
  assert.doesNotMatch(prompts[0].text, /证据已冻结/);
});
