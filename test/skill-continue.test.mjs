import assert from "node:assert/strict";
import test from "node:test";
import { nextRecordingPrompt } from "../src/agent/pi-session.mjs";

test("a finished page turn continues until the skill is verified", () => {
  const again = nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 0 });
  assert.match(again, /SKILL\.md/);
  assert.match(again, /verify_skill/);
  assert.match(again, /filled/);
  assert.doesNotMatch(again, /key_omitted|follows_write|sample_literal/);
  const afterWrite = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 1 });
  assert.match(afterWrite, /verify_skill/);
  assert.match(afterWrite, /requests/);
  assert.equal(nextRecordingPrompt({ finished: true, paused: false, progressed: false, continues: 0 }), null);
  assert.equal(nextRecordingPrompt({ finished: false, paused: true, progressed: false, continues: 0 }), null);
  assert.ok(nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 4, verifyErrors: [] }));
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 8 }), null);
});
