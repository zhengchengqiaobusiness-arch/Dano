import assert from "node:assert/strict";
import test from "node:test";

import { normalizeOpenAIToolCallStream } from "./openai_stream_compat.mjs";

function sse(...payloads) {
  return `${payloads.map((payload) => (
    payload === "[DONE]" ? "data: [DONE]" : `data: ${JSON.stringify(payload)}`
  )).join("\n\n")}\n\n`;
}

test("adds tool_calls finish_reason when a complete tool call ends without one", () => {
  const source = sse(
    {
      id: "resp_1",
      object: "chat.completion.chunk",
      model: "claude-opus-4-8",
      choices: [{ index: 0, delta: { tool_calls: [{
        index: 0,
        id: "call_1",
        function: { name: "get_recording_state", arguments: '{"recording_id":"recording_1"}' },
      }] }, finish_reason: null }],
    },
    "[DONE]",
  );

  const result = normalizeOpenAIToolCallStream(source);

  assert.equal(result.repaired, true);
  assert.equal(result.toolCallCount, 1);
  assert.match(result.body, /"finish_reason":"tool_calls"/);
  assert.ok(result.body.indexOf('"finish_reason":"tool_calls"') < result.body.indexOf("data: [DONE]"));
});

test("does not alter a standards-compliant stream", () => {
  const source = sse(
    {
      choices: [{ index: 0, delta: { tool_calls: [{
        index: 0,
        id: "call_1",
        function: { name: "get_recording_state", arguments: "{}" },
      }] }, finish_reason: null }],
    },
    { choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] },
    "[DONE]",
  );

  assert.deepEqual(normalizeOpenAIToolCallStream(source), {
    body: source,
    repaired: false,
    toolCallCount: 1,
  });
});

test("does not hide an incomplete or truncated tool call", () => {
  const source = sse({
    choices: [{ index: 0, delta: { tool_calls: [{
      index: 0,
      id: "call_1",
      function: { name: "get_recording_state", arguments: '{"recording_id":' },
    }] }, finish_reason: null }],
  });

  assert.deepEqual(normalizeOpenAIToolCallStream(source), {
    body: source,
    repaired: false,
    toolCallCount: 1,
  });
});
