/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  createPiTrace,
  thoughtFromAgentEvent,
  thoughtFromEvidence,
} from "../src/pi-trace.mjs";
import { LivePiSession } from "../src/pi-session.mjs";

test("模型事件和证据会变成助手 thought，指针移动和轮询不发", () => {
  assert.equal(thoughtFromAgentEvent({ type: "turn_start" }), null);
  assert.equal(thoughtFromAgentEvent({
    type: "message_end",
    message: { role: "user", content: "用户刚在预览里操作了页面。先 snapshot" },
  }), null);
  assert.deepEqual(thoughtFromAgentEvent({ delta_type: "thinking_delta", delta: "在对请求" }), {
    kind: "thinking",
    text: "在对请求",
    stream: true,
  });
  assert.equal(thoughtFromEvidence("interaction", { kind: "mousemove" }), null);
  assert.equal(thoughtFromEvidence("interaction", { kind: "input", text: "请输入单据编号" }), null);
  assert.equal(thoughtFromEvidence("network_request", { resource_type: "script", url: "/app.js" }), null);
  assert.equal(thoughtFromEvidence("network_request", {
    resource_type: "xhr",
    method: "GET",
    path: "/prod-api/im/chatMessage/getChatNotReadMessageCount",
  }), null);
  assert.equal(thoughtFromEvidence("visible_control", { count: 10, reason: "interaction" }), null);
  assert.match(thoughtFromEvidence("network_request", { resource_type: "xhr", method: "GET", path: "/api/page" }).text, /GET \/api\/page/);
  assert.match(thoughtFromEvidence("interaction", { kind: "click", text: "提交" }).text, /提交/);
});

test("trace 把工具调用实时交给 onThought", () => {
  const received = [];
  const trace = createPiTrace({ onThought: (item) => received.push(item) });
  trace.recordToolStart("list_recording_index", {});
  trace.recordTool("list_recording_index", {}, "items=2", true);
  trace.handleEvent({ type: "message_end", message: { role: "assistant", content: "已看到列表" } });
  assert.equal(received[0].kind, "tool");
  assert.equal(received[0].phase, "start");
  assert.equal(received[1].phase, "end");
  assert.equal(received[1].ok, true);
  assert.match(received[2].text, /已看到列表/);
});

test("最终分析生命周期会推到助手", async () => {
  const thoughts = [];
  const session = {
    async prompt() {},
  };
  const pi = new LivePiSession({
    session,
    sessionId: "pi_thoughts",
    dispose: () => {},
    onThought: (item) => thoughts.push(item),
  });
  await pi.requestFinalAnalysis({ timeoutMs: 200 });
  assert.ok(thoughts.some((item) => /开始最终分析/.test(item.text)));
  assert.ok(thoughts.some((item) => /分析结束/.test(item.text)));
});
