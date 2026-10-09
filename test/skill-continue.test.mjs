import assert from "node:assert/strict";
import test from "node:test";
import { factsForContinue, nextRecordingPrompt, resultLine } from "../src/agent/pi-session.mjs";

test("a finished page turn continues until the skill is verified", () => {
  const again = nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 0 });
  assert.match(again, /verify_skill/);
  assert.match(again, /listed/);
  assert.doesNotMatch(again, /各占一行必填/);
  assert.doesNotMatch(again, /key_omitted|follows_write|sample_literal/);
  const afterWrite = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 1 });
  assert.match(afterWrite, /verify_skill/);
  assert.match(afterWrite, /listed/);
  assert.match(afterWrite, /不要发明 GET/);
  assert.match(afterWrite, /嵌套数组/);
  assert.match(afterWrite, /表格/);
  assert.match(afterWrite, /冻成空数组|empty\/\[\]/);
  assert.match(afterWrite, /argparse/);
  assert.equal(nextRecordingPrompt({ finished: true, paused: false, progressed: false, continues: 0 }), null);
  assert.equal(nextRecordingPrompt({ finished: false, paused: true, progressed: false, continues: 0 }), null);
  assert.ok(nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 4, verifyErrors: [] }));
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 8 }), null);
  const failed = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 2, verifyErrors: [{ code: "not_invocable", file: "SKILL.md" }] });
  assert.match(failed, /只改/);
  assert.match(failed, /不要 browser_open 本地路径/);
  const expired = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 2, verifyErrors: [{ code: "auth_expired" }] });
  assert.match(expired, /打包凭证过期/);
  assert.match(expired, /不要 assist/);
  assert.match(expired, /不要把凭证写进对话/);
  assert.doesNotMatch(expired, /更新 config\/auth\.local\.json/);
  const line = resultLine({
    content: [{ type: "text", text: JSON.stringify({ ok: true, status: "skill_written_needs_auth", errors: [{ code: "auth_expired" }] }) }],
  });
  assert.match(line, /完成/);
  assert.doesNotMatch(line, /^失败/);
  const emptyLine = resultLine({
    content: [{ type: "text", text: JSON.stringify({ ok: true, filled: ["a"], empty_overlay: ["联系人", "所属系统"] }) }],
  });
  assert.match(emptyLine, /空=/);
  const requiredLine = resultLine({
    content: [{ type: "text", text: JSON.stringify({ ok: true, clicked: "button", required_empty_overlay: ["标题"], empty_overlay: ["分类"] }) }],
  });
  assert.match(requiredLine, /必填空=/);
  const facts = factsForContinue({
    listed: [{ id: "req_1" }],
    overlay_fields: [{ name: "分类", empty: true, required: true }],
    empty_overlay: ["分类"],
    required_empty_overlay: ["分类"],
  });
  assert.equal(facts.empty_overlay[0], "分类");
  assert.equal(facts.overlay_fields[0].empty, true);
  assert.equal(facts.required_empty_overlay[0], "分类");
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 8, verifyErrors: [{ code: "command_not_run" }] }), null);
});
