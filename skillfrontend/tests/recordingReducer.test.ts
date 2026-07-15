import assert from "node:assert/strict";
import test from "node:test";
import * as recordingState from "../src/features/recording-v3/state/recordingReducer.ts";

import {
  initialRecordingSessionState,
  nextRecordingRevision,
  recordingReducer,
} from "../src/features/recording-v3/state/recordingReducer.ts";

test("out-of-order server events never move the current revision backwards", () => {
  const current = { ...initialRecordingSessionState, revision: 12, snapshot: { marker: "new" } };
  const stale = recordingReducer(current, {
    type: "server_event",
    event: { type: "flow_spec_updated", current_revision: 9, snapshot: { marker: "old" } },
  });
  assert.equal(stale.revision, 12);
  assert.deepEqual(stale.snapshot, { marker: "new" });

  const newer = recordingReducer(stale, {
    type: "server_event",
    event: { type: "flow_spec_updated", current_revision: 13 },
  });
  assert.equal(newer.revision, 13);
  assert.equal(nextRecordingRevision(13, { current_revision: 7 }), 13);
  assert.equal(nextRecordingRevision(13, { current_revision: 14 }), 14);

  const staleResume = recordingReducer(newer, {
    type: "session_ready",
    reconnecting: true,
    session: {
      recording_id: "recording-id",
      resume_token: "resume-token",
      websocket_ticket: "ticket",
      current_revision: 8,
      snapshot: { marker: "stale resume" },
    },
  });
  assert.equal(staleResume.revision, 13);
  assert.deepEqual(staleResume.snapshot, { marker: "new" });

  const decorateRecordingMutation = (recordingState as Record<string, unknown>).decorateRecordingMutation as
    | ((message: Record<string, unknown>, revision: number, operationId: string) => Record<string, unknown>)
    | undefined;
  assert.equal(typeof decorateRecordingMutation, "function");
  const revisionAfterStaleEvent = nextRecordingRevision(13, { current_revision: 7 });
  assert.deepEqual(
    decorateRecordingMutation?.({ type: "flow_update" }, revisionAfterStaleEvent, "operation-id"),
    { type: "flow_update", expected_revision: 13, operation_id: "operation-id" },
  );

  const isStaleRecordingEvent = (recordingState as Record<string, unknown>).isStaleRecordingEvent as
    | ((revision: number, event: Record<string, unknown>) => boolean)
    | undefined;
  assert.equal(typeof isStaleRecordingEvent, "function");
  assert.equal(isStaleRecordingEvent?.(13, { current_revision: 12 }), true);
  assert.equal(isStaleRecordingEvent?.(13, { current_revision: 13 }), false);
  assert.equal(isStaleRecordingEvent?.(13, { type: "pi_status" }), false);
});
