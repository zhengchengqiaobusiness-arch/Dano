import assert from "node:assert/strict";
import test from "node:test";

import { shouldEmitAgentEvent, summarizeAgentEvent } from "./recording_pi_events.mjs";

test("keeps text_delta tokens for the thought stream", () => {
  const summary = summarizeAgentEvent({
    type: "message_update",
    assistantMessageEvent: { type: "text_delta", delta: "发现 productId 来源未知" },
  });
  assert.equal(summary.event, "message_update");
  assert.equal(summary.delta_type, "text_delta");
  assert.equal(summary.delta, "发现 productId 来源未知");
  assert.equal(shouldEmitAgentEvent(summary), true);
});

test("keeps thinking_delta tokens", () => {
  const summary = summarizeAgentEvent({
    type: "message_update",
    assistantMessageEvent: { type: "thinking_delta", thinking: "先补回读证据" },
  });
  assert.equal(summary.delta_type, "thinking_delta");
  assert.equal(summary.delta, "先补回读证据");
  assert.equal(shouldEmitAgentEvent(summary), true);
});

test("drops empty message_update noise", () => {
  const summary = summarizeAgentEvent({ type: "message_update", assistantMessageEvent: { type: "text_start" } });
  assert.equal(shouldEmitAgentEvent(summary), false);
});

test("keeps tool start and end", () => {
  const start = summarizeAgentEvent({
    type: "tool_execution_start",
    toolName: "get_validation_report",
    args: { recording_id: "r1" },
  });
  assert.equal(start.toolName, "get_validation_report");
  assert.match(String(start.tool_args), /recording_id/);
  assert.equal(shouldEmitAgentEvent(start), true);
});
