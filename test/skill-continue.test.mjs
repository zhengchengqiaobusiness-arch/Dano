import assert from "node:assert/strict";
import test from "node:test";
import { guideText } from "../src/agent/guides.mjs";
import { modelContextPacket, nextRecordingPrompt } from "../src/agent/pi-session.mjs";
import { snapshotFromEvidence } from "../src/agent/tools.mjs";

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
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 1, verifyErrors: [{ code: "auth_expired" }] }), null);
  assert.match(nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 1, verifyErrors: [{ code: "auth_unproven" }] }), /非写入命令/);
  const failed = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 1, verifyErrors: [{ code: "credential_not_used" }] });
  assert.match(failed, /^校验没过/);
  assert.match(failed, /credential\["url"\]/);
  assert.doesNotMatch(failed, /按字段名/);
  const guide = guideText("skill-generator-auth-and-token.md", "headers only");
  assert.match(guide, /headers only/);
  assert.match(guide, /credential/);
  assert.match(guide, /method/);
  assert.equal(guideText("writing-for-agents.md", "file"), "SKILL.md 开头写 name 和 description。description 写明调用方在什么意图下启用，以及这份 Skill 能做的事。");
});

test("the writing turn still sees the latest snapshot and the value the control kept", () => {
  const fromAction = snapshotFromEvidence({ id: "ev_action", body: { snapshot: { text: "frame f0\n- button \"确定\" ref=f0:e1@2" } } });
  assert.equal(fromAction.evidence_id, "ev_action");
  assert.match(fromAction.text, /f0:e1@2/);
  const fromSnapshot = snapshotFromEvidence({ id: "ev_snap", body: { text: "epoch 1 snap 3" } });
  assert.equal(fromSnapshot.text, "epoch 1 snap 3");
  assert.equal(snapshotFromEvidence({ id: "ev_empty", body: { ok: true } }), null);
  const packet = modelContextPacket({
    goal: { goal_text: "保存" },
    index: [{ id: "req_1" }],
    requests: [{ method: "POST", path: "/save" }],
    filled: ["textbox \"名称\""],
    filled_value: [{ label: "textbox \"名称\"", value: "甲" }],
    also_changed: [{ summary: "fill:spinbutton \"数量\"", controls: ["spinbutton \"单价\""] }],
    auth_file: { method: "POST", path: "/auth", query: true },
    snapshot: fromAction,
  }, { index: true });
  assert.equal(packet.snapshot.text, fromAction.text);
  assert.deepEqual(packet.filled_value, [{ label: "textbox \"名称\"", value: "甲" }]);
  assert.deepEqual(packet.also_changed, [{ summary: "fill:spinbutton \"数量\"", controls: ["spinbutton \"单价\""] }]);
  assert.equal(packet.auth_file.query, true);
  assert.deepEqual(packet.index, [{ id: "req_1" }]);
  assert.equal(packet.requests[0].path, "/save");
});
