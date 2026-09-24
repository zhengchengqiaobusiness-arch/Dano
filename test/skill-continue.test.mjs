import assert from "node:assert/strict";
import test from "node:test";
import { nextRecordingPrompt } from "../src/agent/pi-session.mjs";

test("a finished page turn continues until the skill is verified", () => {
  const again = nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 0 });
  assert.match(again, /SKILL\.md/);
  assert.match(again, /verify_skill/);
  const afterWrite = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 1 });
  assert.match(afterWrite, /verify_skill/);
  assert.equal(nextRecordingPrompt({ finished: true, paused: false, progressed: false, continues: 0 }), null);
  assert.equal(nextRecordingPrompt({ finished: false, paused: true, progressed: false, continues: 0 }), null);
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 4 }), null);
});
