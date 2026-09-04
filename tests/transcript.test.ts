import test from "node:test";
import assert from "node:assert/strict";
import { PiTranscript } from "../src/web/transcript.js";

test("keeps Pi thinking, tool arguments, tool results and final text in session order", () => {
  const transcript = new PiTranscript(value => value);
  transcript.handle({ type: "agent_start" });
  transcript.handle({ type: "message_update", assistantMessageEvent: { type: "thinking_delta", delta: "先读取页面" } });
  transcript.handle({ type: "tool_execution_start", toolCallId: "call-1", toolName: "business_browser_control", args: { action: "snapshot" } });
  transcript.handle({ type: "tool_execution_end", toolCallId: "call-1", toolName: "business_browser_control", result: { content: [{ type: "text", text: "页面标题" }] }, isError: false });
  transcript.handle({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "已识别页面。" } });
  transcript.handle({ type: "message_end", message: { role: "assistant", content: [{ type: "thinking", thinking: "先读取页面" }, { type: "text", text: "已识别页面。" }] } });

  assert.deepEqual(transcript.items.map(item => item.kind), ["thinking", "tool", "message"]);
  assert.equal(transcript.items[0]!.kind === "thinking" && transcript.items[0]!.text, "先读取页面");
  assert.deepEqual(transcript.items[1]!.kind === "tool" && transcript.items[1]!.args, { action: "snapshot" });
  assert.equal(transcript.items[1]!.kind === "tool" && transcript.items[1]!.phase, "complete");
  assert.equal(transcript.items[2]!.kind === "message" && transcript.items[2]!.text, "已识别页面。");
});

test("manual page fills are appended to the session log", () => {
  const transcript = new PiTranscript(value => value);
  const event = transcript.addManual({ eventType: "input", label: "备注", value: "测试采购订单" });
  assert.equal(event.type, "session_item");
  assert.equal(event.item.kind, "tool");
  assert.equal(event.item.toolName, "manual_page_input");
  assert.equal(event.item.phase, "complete");
});

test("manual page clicks are appended to the session log", () => {
  const transcript = new PiTranscript(value => value);
  const event = transcript.addManual({ eventType: "click", label: "高级筛选", text: "高级筛选" });
  assert.equal(event.item.kind, "tool");
  assert.equal(event.item.toolName, "manual_page_click");
  assert.equal(event.item.phase, "complete");
});

test("clearing the transcript empties the workbench session", () => {
  const transcript = new PiTranscript(value => value);
  transcript.addUser("上次录制");
  transcript.handle({ type: "agent_start" });
  transcript.handle({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "旧内容" } });
  transcript.clear();
  assert.deepEqual(transcript.items, []);
  transcript.addUser("新录制");
  assert.equal(transcript.items.length, 1);
  const latest = transcript.items.at(0) as unknown as { kind: string; text?: string };
  assert.equal(latest.kind === "message" && latest.text, "新录制");
});
