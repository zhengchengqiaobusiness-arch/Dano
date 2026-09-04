/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { createPiToolHost } from "../src/pi-tools.mjs";

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
