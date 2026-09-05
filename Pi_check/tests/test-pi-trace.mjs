/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { formatAgentEvent, summarizeToolArgs, summarizeToolResult } from "../src/pi-trace.mjs";

test("工具参数和结果摘要能看出 PI 走了哪一步", () => {
  assert.match(summarizeToolArgs("read_evidence_item", { seq: 650 }), /seq=650/);
  assert.match(summarizeToolArgs("submit_recording_result", {
    final: true,
    result: { capabilities: [{}, {}], unresolved: [] },
  }), /capabilities=2/);
  assert.match(summarizeToolResult("list_recording_index", { count: 18, items: [] }), /items=18/);
});

test("模型事件格式化成可读分析步骤", () => {
  assert.equal(formatAgentEvent({ type: "turn_start" }), "开始新一轮模型分析");
  assert.match(
    formatAgentEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "本场有搜索和提交两个动作" }] },
    }),
    /本场有搜索和提交两个动作/,
  );
});
