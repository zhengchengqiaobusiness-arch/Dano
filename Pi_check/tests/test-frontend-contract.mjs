/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { ResultsCatalog } from "../src/results-catalog.mjs";

test("现有录制页拿到的 draft 就是 PI 提交的能力合同", async () => {
  const result = sampleResult();
  const harness = await createHarness({ result });
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "产出能力",
    });
    const stopped = await harness.controller.stop(started.id);
    assert.equal(stopped.session.capabilityCount, 1);
    assert.equal(stopped.result.capabilities[0].name, "create_leave");
    assert.equal(stopped.result.steps[0].params[0].exposed_to_user, true);
    const catalog = new ResultsCatalog(harness.files);
    const summary = await catalog.remember({
      recordingId: started.id,
      action: "action_1",
      title: "演示",
      goal: "产出能力",
      result: stopped.result,
      evidenceCount: 3,
    });
    assert.equal(summary.capability_count, 1);
    const detail = catalog.detail(started.id);
    assert.deepEqual(detail.draft, stopped.result);
    assert.equal(detail.draft.capabilities.length, 1);
  } finally {
    await harness.cleanup();
  }
});
