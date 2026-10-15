import assert from "node:assert/strict";
import test from "node:test";
import { createToolCallStreamFixer, installOpenAIToolCallStreamCompatibility, normalizeOpenAIToolCallStream } from "../src/agent/openai-stream-compat.mjs";

const known = ["browser_act", "browser_snapshot"];

function chunk(delta, extra = {}) {
  return `data: ${JSON.stringify({
    id: "x",
    object: "chat.completion.chunk",
    created: 1,
    model: "m",
    choices: [{ index: 0, delta, ...extra }],
  })}`;
}

function dataLines(text) {
  return String(text || "").split(/\r?\n/).filter((line) => line.startsWith("data:"));
}

async function withPatchedFetch(stream, run) {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
  installOpenAIToolCallStreamCompatibility({ baseUrl: "http://gw.test", toolNames: known });
  try {
    return await run();
  } finally {
    globalThis.fetch = original;
  }
}

test("stream patch emits the first data line before the source ends", async () => {
  const encoder = new TextEncoder();
  let release;
  const gated = new Promise((resolve) => {
    release = resolve;
  });
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(`${chunk({ content: "hi" })}\n`));
      gated.then(() => {
        controller.enqueue(encoder.encode("data: [DONE]\n"));
        controller.close();
      });
    },
  });
  await withPatchedFetch(stream, async () => {
    const response = await fetch("http://gw.test/v1/chat/completions");
    const reader = response.body.getReader();
    const first = await reader.read();
    assert.equal(first.done, false);
    assert.match(new TextDecoder().decode(first.value), /data:/);
    release();
    while (!(await reader.read()).done) {
      // drain
    }
  });
});

test("a complete tool call without finish_reason is closed before [DONE]", () => {
  const body = [
    chunk({ tool_calls: [{ index: 0, function: { name: "browser_act", arguments: "" } }] }),
    chunk({ tool_calls: [{ index: 0, function: { arguments: "{}" } }] }),
    "data: [DONE]",
    "",
  ].join("\n");
  const result = normalizeOpenAIToolCallStream(body, known);
  assert.equal(result.repaired, true);
  const lines = dataLines(result.body);
  const doneAt = lines.lastIndexOf("data: [DONE]");
  assert.ok(doneAt > 0);
  const finish = lines.slice(0, doneAt).filter((line) => {
    try {
      return Boolean(JSON.parse(line.slice(5).trim())?.choices?.[0]?.finish_reason);
    } catch {
      return false;
    }
  });
  assert.equal(finish.length, 1);
  assert.equal(JSON.parse(finish[0].slice(5).trim()).choices[0].finish_reason, "tool_calls");
});

test("function= prefix is rewritten and later name fragments are cleared", () => {
  const fixer = createToolCallStreamFixer(known);
  const first = fixer.push(chunk({ tool_calls: [{ index: 0, function: { name: "function=browser_act", arguments: "" } }] }));
  const later = fixer.push(chunk({ tool_calls: [{ index: 0, function: { name: "Extra", arguments: "" } }] }));
  fixer.push(chunk({ tool_calls: [{ index: 0, function: { arguments: "{}" } }] }));
  const named = [...first, ...later].filter((line) => line.startsWith("data:") && !line.includes("[DONE]"));
  const names = named.map((line) => JSON.parse(line.slice(5).trim()).choices[0].delta.tool_calls[0].function.name);
  assert.equal(names[0], "browser_act");
  assert.ok(names.slice(1).every((name) => name === "" || name == null));
});

test("split lines and split CRLF match a whole-body rewrite", async () => {
  const payload = [
    chunk({ tool_calls: [{ index: 0, function: { name: "function=browser_act", arguments: "" } }] }),
    chunk({ tool_calls: [{ index: 0, function: { arguments: "{}" } }] }),
    chunk({}, { finish_reason: "tool_calls" }),
    "data: [DONE]",
    "",
  ].join("\r\n");
  const expected = normalizeOpenAIToolCallStream(payload, known).body;
  const encoder = new TextEncoder();
  const pieces = [];
  for (let i = 0; i < payload.length; i += 7) pieces.push(payload.slice(i, i + 7));
  const stream = new ReadableStream({
    start(controller) {
      for (const piece of pieces) controller.enqueue(encoder.encode(piece));
      controller.close();
    },
  });
  await withPatchedFetch(stream, async () => {
    const response = await fetch("http://gw.test/v1/chat/completions");
    const text = await response.text();
    assert.deepEqual(dataLines(text).map((line) => line.trim()), dataLines(expected).map((line) => line.trim()));
  });
});
