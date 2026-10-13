import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import test from "node:test";
import path from "node:path";
import { packageRoot } from "../src/paths.mjs";
import { controlLooksEmpty, goalExactLines, nearestNamedAncestor, overlayExactLines, overlayFactLists, overlayHasRef, overlayItemLabel, stripSnapshotDecorators, yamlFingerprint } from "../src/browser/snapshot.mjs";
import { compactNetworkIndex, networkBodyForModel } from "../src/browser/network.mjs";
import { responseKeyPreview } from "../src/skillpack/request-keys.mjs";
import { writeOnlySkill } from "../src/skillpack/verify.mjs";
import { raceWall, withPage } from "../src/browser/session.mjs";
import { GUIDE_NAMES } from "../src/agent/guides.mjs";
import { hostTools, packedAuthAssist, sessionAssistBlocked } from "../src/agent/tools.mjs";
import { listEvidence } from "../src/evidence/store.mjs";

test("the method does not ban number cells or treat filled controls as caller-required", async () => {
  const prompt = await readFile(path.join(packageRoot(), "src", "agent", "prompt.md"), "utf8");
  assert.ok(prompt.trim().split(/\n/).length <= 80);
  assert.match(prompt, /代码只运输/);
  assert.match(prompt, /listed/);
  assert.match(prompt, /不要发明 GET/);
  assert.match(prompt, /actor=human/);
  assert.match(prompt, /查找表/);
  assert.match(prompt, /fill_fields/);
  assert.match(prompt, /必须以 --- 包住/);
  assert.match(prompt, /关掉这层/);
  assert.match(prompt, /layer_idle/);
  assert.match(prompt, /needs_click/);
  assert.match(prompt, /credential\.headers/);
  assert.match(prompt, /overlay_fields/);
  assert.match(prompt, /不要把凭证写进对话/);
  assert.match(prompt, /嵌套数组/);
  assert.match(prompt, /options 留空/);
  assert.match(prompt, /一行一项、列之间用「\|」/);
  assert.match(prompt, /不写 JSON/);
  assert.doesNotMatch(prompt, /textarea 写 JSON/);
  assert.match(prompt, /调用方怎么问/);
  assert.match(prompt, /已知值放 default 不再另问/);
  assert.match(prompt, /不另造确认题/);
  assert.match(prompt, /整袋替换/);
  assert.match(prompt, /读命令也不要写死本场 listed/);
  assert.match(prompt, /身份键从本场身份接口现取/);
  assert.match(prompt, /占位字/);
  assert.match(prompt, /必填空/);
  assert.match(prompt, /argparse/);
  assert.match(prompt, /冻成空数组/);
  assert.match(prompt, /DANO_BASE_URL/);
  assert.match(prompt, /表格单元格/);
  assert.match(prompt, /no_browser/);
  assert.match(prompt, /不要抢点/);
  assert.match(prompt, /overlay_editable/);
  assert.match(prompt, /没有则是树上非 chrome/);
  assert.match(prompt, /python3/);
  assert.match(prompt, /questions\[\]/);
  assert.match(prompt, /dateFormat/);
  assert.match(prompt, /goal_named_unclicked/);
  assert.match(prompt, /也不要 write_skill_file/);
  assert.match(prompt, /不要 dataSource/);
  assert.match(prompt, /formIds/);
  assert.match(prompt, /不要 browser_open/);
  assert.match(prompt, /同源换页可用 browser_open/);
  assert.doesNotMatch(prompt, /已经有页面时不要 browser_open 换地址|already_open/);
  assert.match(prompt, /set_defaults\(func=/);
  assert.match(prompt, /不要写死源站/);
  assert.match(prompt, /listed_write_keys/);
  assert.match(prompt, /指针样式不是能不能点的依据/);
  assert.match(prompt, /只有 auth\.local\.json 的 credential 同时有 url 和 method 时才重放/);
  assert.match(prompt, /不要只认某一个键名/);
  assert.match(prompt, /DANO_AUTH_HEADERS 是 JSON 对象/);
  assert.match(prompt, /仍写完三文件/);
  assert.doesNotMatch(prompt, /失败或 401 停止/);
  assert.doesNotMatch(prompt, /不点旁边只有数字/);
  assert.doesNotMatch(prompt, /各占一行必填/);
  assert.doesNotMatch(prompt, /same_column/);
  assert.doesNotMatch(prompt, /禁止.*取消|不要点取消/);
  assert.doesNotMatch(prompt, /所属系统/);
  assert.doesNotMatch(prompt, /tenant-id|tenant_id/);
  assert.doesNotMatch(prompt, /都可以点/);
  assert.doesNotMatch(prompt, /打开弹层已带上/);
  assert.doesNotMatch(prompt, /页面已有值若 listed/);
});

test("required empty overlay facts include the current ref", () => {
  const facts = overlayFactLists([{ name: "标题", role: "textbox", ref: "f0:e3@1", empty: true, required: true }]);
  assert.equal(facts.required_empty_overlay[0].ref, "f0:e3@1");
  assert.equal(facts.required_empty_overlay[0].role, "textbox");
  assert.match(overlayItemLabel(facts.required_empty_overlay[0]), /textbox 标题 f0:e3@1/);
});

test("a displayed placeholder is still an empty overlay fact", () => {
  assert.equal(controlLooksEmpty(""), true);
  assert.equal(controlLooksEmpty("   "), true);
  assert.equal(controlLooksEmpty("请选择", "请选择"), true);
  assert.equal(controlLooksEmpty("甲", "请选择"), false);
  assert.equal(controlLooksEmpty("请选择所属系统", "", "请选择所属系统"), true);
  assert.equal(controlLooksEmpty("已选系统", "", "请选择所属系统"), false);
  assert.equal(controlLooksEmpty("已选甲", "", "已选甲"), false);
});

test("assist for a packed token is rejected, page login is not", () => {
  assert.equal(packedAuthAssist('请在预览里更新 config/auth.local.json 的 headers.Authorization'), true);
  assert.equal(packedAuthAssist("登录页被验证码挡住"), false);
  assert.equal(sessionAssistBlocked("浏览器会话已断开（browser_open 返回 no_browser"), true);
  assert.equal(sessionAssistBlocked("origin_denied"), true);
  assert.equal(sessionAssistBlocked("登录页被验证码挡住"), false);
});

test("goal lookup keeps table cells whose column is named in the goal, with or without a pointer cursor", () => {
  const hits = goalExactLines([
    { role: "cell", name: "7", column: "应填数量", ariaRef: "f0:e10@1" },
    { role: "cell", name: "31", column: "已填数量", ariaRef: "f0:e11@1" },
    { role: "cell", name: "2", column: "未填数量", ariaRef: "f0:e12@1" },
    { role: "button", name: "导出", column: "", ariaRef: "f0:e13@1" },
  ], "点应填数量、已填数量、未填数量列里的数字");
  assert.ok(hits.some((node) => node.ariaRef === "f0:e10@1"));
  assert.ok(hits.some((node) => node.ariaRef === "f0:e11@1"));
  assert.ok(hits.some((node) => node.ariaRef === "f0:e12@1"));
  assert.equal(hits.some((node) => node.name === "导出"), false);
});

test("pointer cursor is not published, and a page menu is not the overlay when a dialog is open", () => {
  const raw = '- generic "下载" [ref=f0:e1@1] [cursor=pointer]';
  assert.equal(stripSnapshotDecorators(raw).includes("cursor="), false);
  const yaml = [
    '- navigation [ref=f0:e1@1]:',
    '  - menu [ref=f0:e2@1]:',
    '    - menuitem "首页" [ref=f0:e3@1] [cursor=pointer]',
    '- textbox "列表筛选" [ref=f0:e9@1]',
    '- dialog "新建" [ref=f0:e4@1]:',
    '  - textbox "标题" [ref=f0:e5@1]',
    '  - button "确定" [ref=f0:e6@1]',
  ].join("\n");
  const overlay = overlayExactLines(yaml);
  assert.ok(overlay.some((line) => line.includes("标题")));
  assert.equal(overlay.some((line) => line.includes("首页")), false);
  assert.equal(overlay.some((line) => line.includes("列表筛选")), false);
  const dropdown = overlayExactLines([
    '- button "打开" [ref=f0:e1@1]',
    '- menu [ref=f0:e2@1]:',
    '  - menuitem "甲" [ref=f0:e3@1]',
  ].join("\n"));
  assert.ok(dropdown.some((line) => line.includes("甲")));
});

test("without a dialog, overlay_exact is the page value controls, not chrome or buttons", () => {
  const yaml = [
    '- navigation [ref=f0:e1@1]:',
    '  - searchbox "站内搜索" [ref=f0:e2@1]',
    '- main [ref=f0:e3@1]:',
    '  - textbox "标题" [ref=f0:e4@1]',
    '  - combobox "分类" [ref=f0:e5@1]',
    '  - button "提交" [ref=f0:e6@1]',
    '  - complementary [ref=f0:e7@1]:',
    '    - textbox "侧栏备注" [ref=f0:e8@1]',
  ].join("\n");
  const overlay = overlayExactLines(yaml);
  assert.ok(overlay.some((line) => line.includes("标题")));
  assert.ok(overlay.some((line) => line.includes("分类")));
  assert.ok(overlay.some((line) => line.includes("侧栏备注")));
  assert.equal(overlay.some((line) => line.includes("站内搜索")), false);
  assert.equal(overlay.some((line) => line.includes("提交")), false);
  const unnamed = [
    '- tabpanel "日报" [ref=f0:e1@1]:',
    '  - generic [ref=f0:e2@1]:',
    '    - generic [ref=f0:e3@1]',
  ].join("\n").split("\n");
  assert.equal(nearestNamedAncestor(unnamed, 2), "日报");
});

test("overlay lines stay a lookup from the tree, including numeric cells in a row", () => {
  const yaml = [
    '- button "打开" [ref=f0:e1@1]',
    '- dialog "新建" [ref=f0:e2@1]:',
    '  - textbox "标题" [ref=f0:e3@1]',
    '  - button "确定" [ref=f0:e4@1]',
    '- table [ref=f0:e5@1]:',
    '  - row "甲" [ref=f0:e6@1]:',
    '    - cell "18" [ref=f0:e7@1]',
    '    - cell "0" [ref=f0:e8@1]',
  ].join("\n");
  const overlay = overlayExactLines(yaml);
  assert.ok(overlay.some((line) => line.includes("确定")));
  assert.ok(overlay.some((line) => line.includes("标题")));
  assert.equal(overlay.some((line) => line.includes("打开")), false);
  assert.match(yaml, /cell "18"/);
  assert.notEqual(yamlFingerprint(yaml), yamlFingerprint(`${yaml}\n- button "x" [ref=f0:e9@1]`));
  const published = ["overlay_exact", ...overlay, "frame f0 http://127.0.0.1/"].join("\n");
  assert.equal(overlayHasRef(published, "f0:e4@1"), true);
  assert.equal(overlayHasRef(published, "f0:e1@1"), false);
});

test("listed is an index, large bodies are omitted whole, write-only skills do not invent GET", () => {
  const listed = compactNetworkIndex([
    { id: "req_1", method: "GET", path: "/me", status: 200, keys: [], response_keys: ["data.id"] },
    { id: "req_2", method: "POST", path: "/save", status: 200, keys: ["title"], action_id: "act_1" },
  ]);
  assert.equal(listed[0].path, "/me");
  assert.equal(listed[1].keys[0], "title");
  const omitted = networkBodyForModel({
    id: "req_1",
    path: "/me",
    response_body: `{"data":{"id":1,"blob":"${"x".repeat(20000)}"}}`,
  });
  assert.equal(omitted.response_body, undefined);
  assert.equal(omitted.response_body_omitted, true);
  assert.ok(Array.isArray(omitted.response_keys));
  const preview = responseKeyPreview(JSON.stringify({ data: { user: { id: 1, name: "甲" } } }));
  assert.ok(preview.includes("data"));
  assert.ok(preview.includes("data.user"));
  const skill = "---\nname: save\n---\npython scripts/client.py save --confirm\n";
  const client = 'def save():\n    method = "POST"\n    path = "/save"\n';
  assert.equal(writeOnlySkill(skill, client, [{ method: "POST", path: "/save" }]), true);
  assert.equal(writeOnlySkill("python scripts/client.py list\n", client, [{ method: "GET", path: "/list" }]), false);
  assert.equal(writeOnlySkill(
    "---\nname: save\n---\npython scripts/client.py save --confirm\n| 列表 | python scripts/client.py list --id <a> |\n",
    `${client}\ndef list():\n    method = "GET"\n    path = "/list"\n`,
    [{ method: "POST", path: "/save" }],
  ), false);
});

test("a hung page lock is released so a later turn can run", async () => {
  const order = [];
  let releaseHung;
  const hung = withPage("lock-wall", () => new Promise((resolve) => { releaseHung = resolve; }), { lockMs: 40 });
  await new Promise((resolve) => setTimeout(resolve, 80));
  await withPage("lock-wall", () => { order.push("second"); }, { lockMs: 40 });
  assert.deepEqual(order, ["second"]);
  const timed = await raceWall(new Promise((resolve) => setTimeout(resolve, 400)), 40, () => ({ error: "act_timeout" }));
  assert.equal(timed.error, "act_timeout");
  releaseHung();
  await hung;
});

test("read_guide all records every method file", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-guide-"));
  const tools = hostTools({ id: "rec_guides", subsystem: "app", emit() {} });
  const listed = await tools.read_guide({ name: "all" });
  assert.equal(listed.ok, true);
  assert.equal(listed.evidence_ids.length, GUIDE_NAMES.length);
  for (const name of GUIDE_NAMES) assert.match(listed.text, new RegExp(name.replaceAll(".", "\\.")));
  const evidence = await listEvidence("rec_guides", { limit: 0 });
  const summaries = new Set(evidence.filter((row) => row.kind === "guide").map((row) => row.summary));
  for (const name of GUIDE_NAMES) assert.ok(summaries.has(name), name);
  const unknown = await tools.read_guide({ name: "nope.md" });
  assert.equal(unknown.ok, false);
  assert.equal(unknown.error, "unknown_guide");
});
