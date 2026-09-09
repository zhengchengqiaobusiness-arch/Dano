/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { Type } from "@sinclair/typebox";
import { validateToolArguments } from "@mariozechner/pi-ai";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { createPiToolHost, wrapPiToolsForSdk } from "../src/pi-tools.mjs";

const FakeType = {
  String: () => ({}),
  Integer: () => ({}),
  Boolean: () => ({}),
  Object: () => ({}),
  Array: () => ({}),
  Optional: (value) => value,
};

test("SDK 校验前必须解开被收成字符串的 capability/steps", () => {
  const tools = wrapPiToolsForSdk({
    async submit_recording_capability() {
      return { saved: true };
    },
  }, (spec) => spec, Type);
  const tool = tools.find((item) => item.name === "submit_recording_capability");
  const raw = {
    capability: JSON.stringify({
      capability_id: "cap_write",
      name: "新增并提交",
      request_refs: [{ step_id: "step_submit", usage: "execute" }],
    }),
    steps: JSON.stringify([
      { step_id: "step_submit", params: [{ key: "title", path: "body.title" }] },
    ]),
  };
  assert.throws(
    () => validateToolArguments(tool, { arguments: raw }),
    /capability: must be object/,
  );
  const prepared = tool.prepareArguments(raw);
  const validated = validateToolArguments(tool, { arguments: prepared });
  assert.equal(validated.capability.capability_id, "cap_write");
  assert.equal(validated.steps[0].step_id, "step_submit");
});

test("最终结果校验失败时把具体原因返回给 PI 继续修正", async () => {
  const tools = wrapPiToolsForSdk({
    async submit_recording_result() {
      throw new Error("input_schema.properties.businessId 必须对应 exposed_to_user=true 的 param.key");
    },
  }, (spec) => spec, FakeType);
  const submit = tools.find((item) => item.name === "submit_recording_result");
  const response = await submit.execute("call_1", {
    recording_id: "rec_test",
    final: true,
    result: { capabilities: [{}] },
  });
  const payload = JSON.parse(response.content[0].text);
  assert.equal(payload.accepted, false);
  assert.match(payload.error, /businessId/);
  assert.match(payload.next_action, /修正.*重新调用 submit_recording_result/);
});

test("14. 完成一次真实 PI 最终提交", async () => {
  const result = sampleResult({
    contract: "complete",
    capabilities: [{ id: "pi_only", name: "pi_only", title: "PI 提交" }],
  });
  const harness = await createHarness({ result });
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "真实提交",
    });
    const host = createPiToolHost({
      recordingId: started.id,
      evidence: harness.evidence,
      files: harness.files,
      gate: harness.gate,
      getPiSessionId: () => harness.controller.view(started.id).piSessionId,
    });
    const stopped = await harness.controller.stop(started.id);
    assert.equal(stopped.session.status, "succeeded");
    assert.deepEqual(stopped.result, result);
    assert.equal(stopped.receipt.pi_session_id, "scripted-pi");
    const freeze = await host.get_recording_freeze_state();
    assert.equal(freeze.frozen, true);
    await assert.rejects(
      () => host.submit_recording_result({
        recording_id: started.id,
        final: true,
        result: { capabilities: [{ capability_id: "second", name: "second" }] },
      }),
      /第二个最终结果/,
    );
    assert.deepEqual(await harness.files.readPiResult(started.id), result);
  } finally {
    await harness.cleanup();
  }
});
