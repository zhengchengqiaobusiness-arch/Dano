import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import path from "node:path";
import { packageRoot } from "../src/paths.mjs";
import { controlLooksEmpty, overlayExactLines, overlayHasRef, yamlFingerprint } from "../src/browser/snapshot.mjs";
import { compactNetworkIndex, networkBodyForModel } from "../src/browser/network.mjs";
import { responseKeyPreview } from "../src/skillpack/request-keys.mjs";
import { writeOnlySkill } from "../src/skillpack/verify.mjs";
import { raceWall, withPage } from "../src/browser/session.mjs";
import { packedAuthAssist } from "../src/agent/tools.mjs";

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
  assert.doesNotMatch(prompt, /不点旁边只有数字/);
  assert.doesNotMatch(prompt, /各占一行必填/);
  assert.doesNotMatch(prompt, /same_column/);
  assert.doesNotMatch(prompt, /禁止.*取消|不要点取消/);
});

test("a displayed placeholder is still an empty overlay fact", () => {
  assert.equal(controlLooksEmpty(""), true);
  assert.equal(controlLooksEmpty("   "), true);
  assert.equal(controlLooksEmpty("请选择", "请选择"), true);
  assert.equal(controlLooksEmpty("甲", "请选择"), false);
});

test("assist for a packed token is rejected, page login is not", () => {
  assert.equal(packedAuthAssist('请在预览里更新 config/auth.local.json 的 headers.Authorization'), true);
  assert.equal(packedAuthAssist("登录页被验证码挡住"), false);
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
