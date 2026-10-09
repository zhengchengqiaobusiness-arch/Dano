import assert from "node:assert/strict";
import test from "node:test";
import { applyVerifyToRecording, publicSessionStatus } from "../src/session.mjs";

test("the session only publishes recording, skill ready, or failed", () => {
  assert.equal(publicSessionStatus({ status: "recording" }), "recording");
  assert.equal(publicSessionStatus({ status: "waiting_operator" }), "recording");
  assert.equal(publicSessionStatus({ status: "skill_ready" }), "skill_ready");
  assert.equal(publicSessionStatus({ status: "skill_written_needs_auth" }), "skill_ready");
  assert.equal(publicSessionStatus({ status: "verify_failed" }), "failed");
  assert.equal(publicSessionStatus({ status: "failed" }), "failed");
  assert.equal(publicSessionStatus({ status: "stopped" }), "failed");
  const recording = { status: "recording", finished: false };
  applyVerifyToRecording(recording, { status: "verify_failed", errors: [{ code: "command_not_run" }] }, "/tmp/skill");
  assert.equal(recording.status, "recording");
  assert.equal(recording.finished, false);
  assert.equal(publicSessionStatus(recording), "recording");
  applyVerifyToRecording(recording, { status: "skill_written_needs_auth", errors: [] }, "/tmp/skill");
  assert.equal(recording.status, "skill_ready");
  assert.equal(recording.finished, true);
  assert.equal(publicSessionStatus(recording), "skill_ready");
});
