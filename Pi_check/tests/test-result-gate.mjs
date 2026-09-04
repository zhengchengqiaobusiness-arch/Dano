/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import {
  assertPageDisplayContract,
  assertCapabilityIdentityContract,
  SubmitRejectedError,
} from "../src/result-gate.mjs";

test("证据未冻结时提交最终结果必须失败", async () => {
  const harness = await createHarness();
  try {
    const session = await harness.evidence.create({ targetUrl: "http://x", goal: "g" });
    await harness.evidence.setStatus(session.id, { piSessionId: "pi-1" });
    await assert.rejects(
      () => harness.gate.submitRecordingResult({
        recordingId: session.id,
        expectedRecordingId: session.id,
        callerSessionId: "pi-1",
        expectedSessionId: "pi-1",
        final: true,
        result: sampleResult(),
        frozen: false,
      }),
      /尚未冻结/,
    );
    assert.equal(await harness.files.hasPiResult(session.id), false);
  } finally {
    await harness.cleanup();
  }
});

test("空结果、错误编号、非最终结果都必须失败", async () => {
  const harness = await createHarness();
  try {
    const session = await harness.evidence.create({ targetUrl: "http://x", goal: "g" });
    await harness.evidence.setStatus(session.id, { piSessionId: "pi-1" });
    await harness.evidence.freeze(session.id);
    await assert.rejects(
      () => harness.gate.submitRecordingResult({
        recordingId: session.id,
        expectedRecordingId: session.id,
        callerSessionId: "pi-1",
        expectedSessionId: "pi-1",
        final: true,
        result: {},
        frozen: true,
      }),
      /空结果/,
    );
    await assert.rejects(
      () => harness.gate.submitRecordingResult({
        recordingId: session.id,
        expectedRecordingId: session.id,
        callerSessionId: "pi-1",
        expectedSessionId: "pi-1",
        final: true,
        result: { analysis: "只有分析没有能力" },
        frozen: true,
      }),
      /未提交任何能力/,
    );
    await assert.rejects(
      () => harness.gate.submitRecordingResult({
        recordingId: "rec_other",
        expectedRecordingId: session.id,
        callerSessionId: "pi-1",
        expectedSessionId: "pi-1",
        final: true,
        result: sampleResult(),
        frozen: true,
      }),
      /编号不匹配/,
    );
    await assert.rejects(
      () => harness.gate.submitRecordingResult({
        recordingId: session.id,
        expectedRecordingId: session.id,
        callerSessionId: "pi-1",
        expectedSessionId: "pi-1",
        final: false,
        result: sampleResult(),
        frozen: true,
      }),
      /非最终结果/,
    );
    assert.equal(await harness.files.hasPiResult(session.id), false);
  } finally {
    await harness.cleanup();
  }
});

test("成功提交原样保存，且拒绝第二次提交和任何改写", async () => {
  const harness = await createHarness();
  try {
    const session = await harness.evidence.create({ targetUrl: "http://x", goal: "g" });
    await harness.evidence.setStatus(session.id, { piSessionId: "pi-1" });
    await harness.evidence.freeze(session.id);
    const result = sampleResult({ keep_this_exact_value: "π", capabilities: [{ id: "only" }] });
    await harness.gate.submitRecordingResult({
      recordingId: session.id,
      expectedRecordingId: session.id,
      callerSessionId: "pi-1",
      expectedSessionId: "pi-1",
      final: true,
      result,
      frozen: true,
    });
    const stored = await harness.files.readPiResult(session.id);
    assert.deepEqual(stored, result);
    assert.equal(stored.keep_this_exact_value, "π");
    await assert.rejects(
      () => harness.gate.submitRecordingResult({
        recordingId: session.id,
        expectedRecordingId: session.id,
        callerSessionId: "pi-1",
        expectedSessionId: "pi-1",
        final: true,
        result: { capabilities: [{ id: "second" }] },
        frozen: true,
      }),
      /第二个最终结果/,
    );
    assert.deepEqual(await harness.files.readPiResult(session.id), result);
    const receipt = await harness.files.readReceipt(session.id);
    assert.equal(receipt.recording_id, session.id);
    assert.equal(Object.hasOwn(stored, "accepted_at"), false);
  } finally {
    await harness.cleanup();
  }
});

test("拒收页面读不到的私有字段袋、字符串 request_refs 和键值映射 params", () => {
  assert.throws(
    () => assertPageDisplayContract({
      capabilities: [{
        capability_id: "cap_x",
        fields: [{ key: "keyword", label: "关键字" }],
      }],
    }),
    (error) => error instanceof SubmitRejectedError && /不读 capabilities\[\]\.fields/.test(error.message),
  );
  assert.throws(
    () => assertPageDisplayContract({
      capabilities: [{
        capability_id: "cap_x",
        request_refs: ["req_abc"],
      }],
    }),
    (error) => error instanceof SubmitRejectedError && /step_id/.test(error.message),
  );
  assert.throws(
    () => assertPageDisplayContract({
      capabilities: [{ capability_id: "cap_x", request_refs: [{ step_id: "step_1", usage: "execute" }] }],
      steps: [{ step_id: "step_1", params: { keyword: "1" } }],
    }),
    (error) => error instanceof SubmitRejectedError && /字段对象数组/.test(error.message),
  );
  assert.doesNotThrow(() => assertPageDisplayContract(sampleResult()));
  assert.doesNotThrow(() => assertCapabilityIdentityContract(sampleResult()));
});

test("拒收重复 capability_id 和共用 execute", () => {
  assert.throws(
    () => assertCapabilityIdentityContract({
      capabilities: [
        {
          capability_id: "cap_delete_hotel_apply",
          request_refs: [{ step_id: "step_a", usage: "execute" }],
        },
        {
          capability_id: "cap_delete_hotel_apply",
          request_refs: [{ step_id: "step_b", usage: "execute" }],
        },
      ],
      steps: [{ step_id: "step_a", params: [] }, { step_id: "step_b", params: [] }],
    }),
    (error) => error instanceof SubmitRejectedError && /capability_id 不得重复/.test(error.message),
  );
  assert.throws(
    () => assertCapabilityIdentityContract({
      capabilities: [
        {
          capability_id: "cap_create",
          request_refs: [{ step_id: "step_submit", usage: "execute" }],
        },
        {
          capability_id: "cap_edit",
          request_refs: [{ step_id: "step_submit", usage: "execute" }],
        },
      ],
      steps: [{ step_id: "step_submit", params: [] }],
    }),
    (error) => error instanceof SubmitRejectedError && /不能共用同一个 execute/.test(error.message),
  );
});

test("错误信封不得落盘", async () => {
  const harness = await createHarness();
  try {
    const session = await harness.evidence.create({ targetUrl: "http://x", goal: "g" });
    await harness.evidence.setStatus(session.id, { piSessionId: "pi-1" });
    await harness.evidence.freeze(session.id);
    await assert.rejects(
      () => harness.gate.submitRecordingResult({
        recordingId: session.id,
        expectedRecordingId: session.id,
        callerSessionId: "pi-1",
        expectedSessionId: "pi-1",
        final: true,
        result: {
          capabilities: [{
            capability_id: "cap_search",
            request_refs: ["req_1"],
            fields: [{ key: "keyword", exposed_to_user: true }],
          }],
          steps: [{ step_id: "step_1", params: { keyword: "1" } }],
        },
        frozen: true,
      }),
      /现有录制页不读/,
    );
    assert.equal(await harness.files.hasPiResult(session.id), false);
  } finally {
    await harness.cleanup();
  }
});
