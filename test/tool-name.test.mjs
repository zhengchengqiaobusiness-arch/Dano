import assert from "node:assert/strict";
import test from "node:test";
import { canonicalToolName, normalizeOpenAIToolCallStream } from "../src/agent/openai-stream-compat.mjs";

const known = ["browser_screenshot", "browser_act", "write_skill_file"];

test("streamed tool names drop a function= prefix so the host tool is found", () => {
  assert.equal(canonicalToolName("bfunction=browser_screenshot", known), "browser_screenshot");
  assert.equal(canonicalToolName("function=browser_act", known), "browser_act");
  assert.equal(canonicalToolName("`browser_screenshot`", known), "browser_screenshot");
  assert.equal(canonicalToolName("browser_screenshot", known), "browser_screenshot");

  const chunk = (name) => `data: ${JSON.stringify({
    id: "x",
    object: "chat.completion.chunk",
    created: 1,
    model: "m",
    choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { name, arguments: "" } }] } }],
  })}`;
  const args = `data: ${JSON.stringify({
    id: "x",
    object: "chat.completion.chunk",
    created: 1,
    model: "m",
    choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: "{}" } }] } }],
  })}`;
  const finish = `data: ${JSON.stringify({
    id: "x",
    object: "chat.completion.chunk",
    created: 1,
    model: "m",
    choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
  })}`;
  const body = [chunk("bfunction="), chunk("browser_screenshot"), args, finish, "data: [DONE]", ""].join("\n");
  const result = normalizeOpenAIToolCallStream(body, known);
  assert.equal(result.repaired, true);
  assert.match(result.body, /"name":"browser_screenshot"/);
  assert.doesNotMatch(result.body, /bfunction=browser_screenshot/);
});
