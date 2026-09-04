/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  PI_ONLY_NOTICE,
  PI_ONLY_POLICY,
  assertNeverStartLegacy,
  publicFailureMessage,
} from "../src/policy.mjs";

test("策略常量把 PI 定为唯一语义权威，并禁止旧逻辑启动", () => {
  assert.equal(PI_ONLY_NOTICE, "PI 是唯一语义决策者；旧录制逻辑绝不启动。");
  assert.equal(PI_ONLY_POLICY.semanticAuthority, "pi");
  assert.equal(PI_ONLY_POLICY.legacyRecordingMustNeverStart, true);
  assert.equal(PI_ONLY_POLICY.codeMayCreateFallbackOutput, false);
  assertNeverStartLegacy();
  assert.equal(publicFailureMessage(), "PI 未完成，本次录制失败，没有产出能力");
});
