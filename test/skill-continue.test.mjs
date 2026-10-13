import assert from "node:assert/strict";
import test from "node:test";
import { hostTools } from "../src/agent/tools.mjs";
import { MAX_STALLS, factsForContinue, listedWriteKeys, listedWrites, nextRecordingPrompt, recordingStayOnPage, resultLine, stallVerdict, unmatchedGoalNames } from "../src/agent/pi-session.mjs";

test("a silent model turn is interrupted after the stall budget, and only repeated stalls fail the recording", () => {
  const stallMs = 180000;
  assert.equal(stallVerdict({ prompting: true, streaming: true, idleMs: 60000, stalls: 0, stallMs }), null);
  assert.equal(stallVerdict({ prompting: false, streaming: false, idleMs: 900000, stalls: 0, stallMs }), null);
  assert.equal(stallVerdict({ prompting: true, streaming: false, idleMs: 900000, stalls: 0, stallMs }), null);
  const first = stallVerdict({ prompting: true, streaming: true, idleMs: 630000, stalls: 0, stallMs });
  assert.equal(first.stalls, 1);
  assert.equal(first.fail, false);
  assert.match(first.line, /630 秒无响应/);
  assert.match(first.line, /重发/);
  const last = stallVerdict({ prompting: true, streaming: true, idleMs: stallMs, stalls: MAX_STALLS - 1, stallMs });
  assert.equal(last.fail, true);
  assert.match(last.line, /录制失败/);
});

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
  assert.match(afterWrite, /仍要写完 SKILL\.md/);
  assert.match(afterWrite, /不是产出失败/);
  assert.doesNotMatch(afterWrite, /失败或 401 再停止/);
  assert.match(afterWrite, /嵌套数组/);
  assert.match(afterWrite, /options 留空/);
  assert.match(afterWrite, /textarea/);
  assert.match(afterWrite, /整袋替换/);
  assert.match(afterWrite, /读命令不要写死本场 listed/);
  assert.match(afterWrite, /表格/);
  assert.match(afterWrite, /冻成空数组|empty\/\[\]/);
  assert.match(afterWrite, /argparse/);
  assert.match(afterWrite, /questions/);
  assert.match(afterWrite, /--confirm/);
  assert.equal(nextRecordingPrompt({ finished: true, paused: false, progressed: false, continues: 0 }), null);
  assert.equal(nextRecordingPrompt({ finished: false, paused: true, progressed: false, continues: 0 }), null);
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, skillReady: true, progressed: true, continues: 1, verifyErrors: [] }), null);
  const afterSteer = nextRecordingPrompt({ finished: false, paused: false, skillReady: false, progressed: true, continues: 0, verifyErrors: [] });
  assert.match(afterSteer, /写三文件|verify_skill/);
  assert.ok(nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 4, verifyErrors: [] }));
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 8 }), null);
  const failed = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 2, verifyErrors: [{ code: "not_invocable", file: "SKILL.md" }] });
  assert.match(failed, /只改/);
  assert.match(failed, /不要 browser_act/);
  assert.match(failed, /字段、绑定、未解决、已验证读命令/);
  assert.match(failed, /不要用接口 path 当标题/);
  assert.match(failed, /overlay_editable_names/);
  assert.match(failed, /parsed_page_names/);
  assert.match(failed, /question_ids/);
  assert.doesNotMatch(failed, /禁止.*取消|不要点取消/);
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
  const expiredRun = resultLine({
    content: [{ type: "text", text: JSON.stringify({ ok: false, error: "auth_expired" }) }],
  });
  assert.match(expiredRun, /完成/);
  assert.match(expiredRun, /继续写三文件/);
  assert.doesNotMatch(expiredRun, /^失败/);
  const emptyLine = resultLine({
    content: [{ type: "text", text: JSON.stringify({ ok: true, filled: ["a"], empty_overlay: ["联系人", "所属系统"] }) }],
  });
  assert.match(emptyLine, /空=/);
  const pageEmpty = resultLine({
    content: [{ type: "text", text: JSON.stringify({ ok: true, clicked: "generic", overlay_surface: "page", empty_overlay: ["请输入部门名称"], required_empty_overlay: [] }) }],
  });
  assert.doesNotMatch(pageEmpty, /空=/);
  const dialogEmpty = resultLine({
    content: [{ type: "text", text: JSON.stringify({ ok: true, clicked: "button", overlay_surface: "dialog", empty_overlay: ["标题"] }) }],
  });
  assert.match(dialogEmpty, /空=/);
  const requiredLine = resultLine({
    content: [{ type: "text", text: JSON.stringify({ ok: true, clicked: "button", required_empty_overlay: [{ name: "标题", ref: "f0:e3@1" }], empty_overlay: ["分类"] }) }],
  });
  assert.match(requiredLine, /必填空=/);
  assert.match(requiredLine, /f0:e3@1/);
  assert.ok(requiredLine.indexOf("必填空=") < requiredLine.indexOf("button"));
  const facts = factsForContinue({
    listed: [{ id: "req_1", method: "POST", keys: ["title"] }],
    overlay_fields: [{ name: "分类", empty: true, required: true, ref: "f0:e3@1" }],
    overlay_editable: [{ name: "分类", empty: true, required: true, ref: "f0:e3@1" }],
    empty_overlay: ["分类"],
    required_empty_overlay: ["分类"],
    snapshot: { text: "goal_exact\n- cell \"7\" col \"未填数量\" [ref=f0:e12@1]\nframe f0 http://example.test/\n" },
    clicked: ["cell \"7\" col \"应填数量\""],
  });
  assert.equal(facts.empty_overlay[0], "分类");
  assert.equal(facts.overlay_fields[0].empty, true);
  assert.equal(facts.required_empty_overlay[0], "分类");
  assert.equal(facts.overlay_editable[0].ref, "f0:e3@1");
  const aligned = factsForContinue({
    listed: [{ id: "req_1", method: "POST", keys: ["title"] }],
    alignment: { overlay_editable_names: ["标题"], parsed_page_names: ["标题"], question_ids: ["title"] },
  });
  assert.deepEqual(aligned.overlay_editable_names, ["标题"]);
  assert.deepEqual(aligned.parsed_page_names, ["标题"]);
  assert.deepEqual(aligned.question_ids, ["title"]);
  assert.deepEqual(facts.listed_write_keys, ["title"]);
  assert.ok(facts.goal_named.includes("未填数量"));
  assert.equal(facts.goal_named.includes("导出"), false);
  assert.ok(facts.goal_named_unclicked.includes("未填数量"));
  assert.equal(facts.goal_named_unclicked.includes("7"), false);
  const factsLater = factsForContinue({
    seen_goal_names: ["月报", "未填数量"],
    clicked: ["cell \"7\" col \"应填数量\""],
    snapshot: { text: "goal_exact\n- cell \"7\" col \"未填数量\" [ref=f0:e12@1]\nframe f0 http://example.test/\n" },
    goal: { goal_text: "点应填数量、已填数量、未填数量、月报" },
  });
  assert.ok(factsLater.goal_named.includes("月报"));
  assert.equal(factsLater.goal_named_unclicked.includes("月报"), false);
  assert.ok(factsLater.goal_named_unclicked.includes("未填数量"));
  assert.deepEqual(unmatchedGoalNames(["未填数量", "7", "导出"], ["cell \"7\" col \"应填数量\""], "点应填数量、已填数量、未填数量"), ["未填数量"]);
  assert.equal(recordingStayOnPage({ pageProgressed: false, progressed: true, unmatchedGoalNames: ["未填数量"] }), true);
  assert.equal(recordingStayOnPage({ verifyErrors: [{ code: "auth_expired" }], unmatchedGoalNames: ["未填数量"] }), false);
  assert.equal(recordingStayOnPage({ pageProgressed: false, progressed: true, unmatchedGoalNames: [], requiredEmptyOverlay: ["标题"] }), true);
  assert.equal(recordingStayOnPage({ pageProgressed: false, progressed: true, unmatchedGoalNames: [], requiredEmptyOverlay: [] }), false);
  assert.equal(recordingStayOnPage({ pageProgressed: false, progressed: true, unmatchedGoalNames: [], requiredEmptyOverlay: ["标题"], recordedWrite: true }), false);
  assert.equal(recordingStayOnPage({ pageProgressed: false, progressed: true, unmatchedGoalNames: ["未填数量"], requiredEmptyOverlay: ["标题"], recordedWrite: true }), true);
  assert.equal(nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 8, verifyErrors: [{ code: "command_not_run" }] }), null);
  const stillPage = nextRecordingPrompt({ finished: false, paused: false, progressed: false, continues: 8, pageProgressed: true });
  assert.match(stillPage, /write_skill_file 或 verify_skill|不要 verify_skill/);
  assert.match(stillPage, /overlay_editable/);
  assert.match(stillPage, /指针样式不是能不能点的依据/);
  assert.match(stillPage, /goal_named/);
  assert.match(stillPage, /同源换页可用 browser_open/);
  assert.match(stillPage, /clicked 里已有的不要重复点/);
  assert.match(stillPage, /只看当前树/);
  assert.match(stillPage, /empty=true 是没值/);
  assert.doesNotMatch(stillPage, /empty=true 的还要填/);
  assert.doesNotMatch(stillPage, /已经有页面时不要 browser_open 换地址|already_open/);
  assert.doesNotMatch(stillPage, /禁止.*取消|不要点取消/);
  const skippedWrite = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 1, unmatchedGoalNames: ["未填数量"] });
  assert.match(skippedWrite, /goal_named_unclicked/);
  assert.match(skippedWrite, /required_empty_overlay/);
  assert.match(skippedWrite, /verify_skill/);
  const skippedRequired = nextRecordingPrompt({ finished: false, paused: false, progressed: true, continues: 1, requiredEmptyOverlay: ["标题"] });
  assert.match(skippedRequired, /required_empty_overlay/);
  assert.doesNotMatch(skippedRequired, /对照 listed（本场全部 xhr\/fetch 索引）、overlay_editable、listed_write_keys 和目标原文写三文件/);
  assert.doesNotMatch(skippedWrite, /对照 listed（本场全部 xhr\/fetch 索引）、overlay_editable、listed_write_keys 和目标原文写三文件/);
  assert.deepEqual(listedWriteKeys([
    { method: "GET", keys: ["id"] },
    { method: "POST", keys: ["refreshToken"], issues_credential: true },
    { method: "POST", keys: ["title", "deptId"] },
  ]), ["title", "deptId"]);
  assert.deepEqual(listedWrites([
    { id: "req_3", method: "POST", path: "/auth/refresh", issues_credential: true },
    { id: "req_50", method: "POST", path: "/biz/submit", keys: ["title"] },
  ]), [{ id: "req_50", method: "POST", path: "/biz/submit" }]);
  const afterRecordedWrite = nextRecordingPrompt({
    finished: false, paused: false, progressed: true, continues: 1,
    unmatchedGoalNames: [], requiredEmptyOverlay: ["标题"], recordedWrite: true,
  });
  assert.match(afterRecordedWrite, /写三文件/);
  assert.doesNotMatch(afterRecordedWrite, /不要 write_skill_file/);
  const commandFail = resultLine({
    content: [{
      type: "text",
      text: JSON.stringify({
        ok: false,
        code: 1,
        stdout: JSON.stringify({ code: 0, msg: "", data: [{ id: 1 }] }, null, 2),
        stderr: "",
      }),
    }],
  });
  assert.match(commandFail, /失败/);
  assert.match(commandFail, /exit=1/);
  assert.match(commandFail, /business_code=0/);
  assert.doesNotMatch(commandFail, /\"data\"/);
  const headingLine = resultLine({
    content: [{
      type: "text",
      text: JSON.stringify({
        ok: false,
        errors: [
          { code: "not_invocable", file: "references/api.md", heading: "字段", hint: "references/api.md 标题必须有「字段」" },
          { code: "not_invocable", file: "references/api.md", hint: "字段段要逐条写出 page_name 与 source" },
        ],
      }),
    }],
  });
  assert.match(headingLine, /字段/);
  assert.match(headingLine, /page_name/);
});

test("a snapshot without a live browser is no_browser", async () => {
  const snap = await hostTools({ id: "rec_no_browser_snap", subsystem: "app" }).browser_snapshot();
  assert.equal(snap.ok, false);
  assert.equal(snap.error, "no_browser");
  const line = resultLine({ content: [{ type: "text", text: JSON.stringify(snap) }] });
  assert.match(line, /失败/);
  assert.match(line, /no_browser/);
});
