import assert from "node:assert/strict";
import test from "node:test";
import { armIdleTimer, HUMAN_STEER_IDLE_MS, PREVIEW_DETACH_MS } from "../src/idle-timer.mjs";

test("human steer waits until idle and preview disconnect does not drop the browser immediately", async () => {
  assert.ok(HUMAN_STEER_IDLE_MS >= 2000);
  assert.ok(PREVIEW_DETACH_MS >= 60000);
  const holder = {};
  const fired = [];
  armIdleTimer(holder, "humanSteerTimer", 40, () => fired.push("first"));
  armIdleTimer(holder, "humanSteerTimer", 40, () => fired.push("second"));
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.deepEqual(fired, ["second"]);
});
