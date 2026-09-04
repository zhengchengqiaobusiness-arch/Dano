/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createHarness } from "./helpers/harness.mjs";
import { publicFailureMessage } from "../src/policy.mjs";

test("15. 人为关闭 PI 后系统只能失败，不能产出能力", async () => {
  const harness = await createHarness({ emitOnStart: false });
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "关闭 PI",
    });
    assert.equal(started.status, "recording");
    harness.getPi().kill();
    await new Promise((resolve) => setTimeout(resolve, 50));
    const view = harness.controller.view(started.id);
    assert.equal(view.status, "failed");
    assert.equal(view.publicMessage, publicFailureMessage());
    assert.equal(view.capabilityCount, 0);
    assert.equal(await harness.files.hasPiResult(started.id), false);
    await assert.rejects(() => harness.controller.stop(started.id), /失败/);
  } finally {
    await harness.cleanup();
  }
});
