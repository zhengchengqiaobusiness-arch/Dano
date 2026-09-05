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
  assert.equal(session.prompts[1].options.streamingBehavior, "followUp");
});

test("分析停住时催促提交，但不中止当前轮", async () => {
  const prompts = [];
  const session = {
    prompts,
    async prompt(text, options = {}) {
      prompts.push({ text, options });
      if (options.streamingBehavior === "steer") return;
      await new Promise(() => {});
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
    () => pi.requestFinalAnalysis({ timeoutMs: 250, idleSubmitMs: 40 }),
    /超时/,
  );
  const steers = prompts.filter((item) => item.options.streamingBehavior === "steer" && /submit_recording_result/.test(item.text));
  assert.equal(steers.length, 1);
  assert.equal(prompts.filter((item) => !item.options.streamingBehavior && /证据已经够了/.test(item.text)).length, 0);
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
