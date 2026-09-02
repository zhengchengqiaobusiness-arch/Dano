import assert from "node:assert/strict";
import test from "node:test";
import { completeRecordingSession } from "../web/recording-workflow.js";

test("stopping a recording only saves evidence", async () => {
  const calls: Array<{ path: string; options: any }> = [];
  const result = await completeRecordingSession(async (path: string, options: any) => {
    calls.push({ path, options });
    return { id: "recording-1" };
  });

  assert.deepEqual(calls.map(call => call.path), ["/api/browser/stop"]);
  assert.equal(result.session.id, "recording-1");
});
