/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { createPiToolHost } from "../src/pi-tools.mjs";

test("单项能力写入草稿后可用 use_draft 定稿", async () => {
  const harness = await createHarness();
  try {
    const session = await harness.evidence.create({ targetUrl: "http://x", goal: "g" });
    await harness.evidence.setStatus(session.id, { piSessionId: "pi-1" });
    const tools = createPiToolHost({
      recordingId: session.id,
      evidence: harness.evidence,
      files: harness.files,
      gate: harness.gate,
      getPiSessionId: () => "pi-1",
      freezeEvidence: async () => {
        if (!harness.evidence.snapshot(session.id).frozen) {
          await harness.evidence.freeze(session.id);
        }
      },
    });
    const result = sampleResult();
    const saved = await tools.submit_recording_capability({
      capability: result.capabilities[0],
      steps: result.steps,
      title: "演示目标",
    });
    assert.equal(saved.saved, true);
    assert.equal(saved.capability_count, 1);
    const accepted = await tools.submit_recording_result({
      recording_id: session.id,
      final: true,
      use_draft: true,
    });
    assert.equal(accepted.accepted, true);
    const stored = await harness.files.readPiResult(session.id);
    assert.equal(stored.capabilities[0].capability_id, "cap_create_leave");
    assert.equal(stored.steps[0].params[0].key, "days");
  } finally {
    await harness.cleanup();
  }
});

test("没有 freezeEvidence 的 host 仍拒未冻结提交", async () => {
  const harness = await createHarness();
  try {
    const session = await harness.evidence.create({ targetUrl: "http://x", goal: "g" });
    await harness.evidence.setStatus(session.id, { piSessionId: "pi-1" });
    const tools = createPiToolHost({
      recordingId: session.id,
      evidence: harness.evidence,
      files: harness.files,
      gate: harness.gate,
      getPiSessionId: () => "pi-1",
    });
    await assert.rejects(
      () => tools.submit_recording_result({
        recording_id: session.id,
        final: true,
        result: sampleResult(),
      }),
      /尚未冻结/,
    );
    assert.equal(await harness.files.hasPiResult(session.id), false);
  } finally {
    await harness.cleanup();
  }
});
