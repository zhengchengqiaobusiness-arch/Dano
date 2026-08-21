import assert from "node:assert/strict";
import test from "node:test";
import {
  normalizeSkillExportDraft,
  routeSummaryFromOutcome,
  serializeSkillExportDraft,
} from "./skillExportDraft.mjs";

test("draft restore keeps all six planning fields", () => {
  const saved = serializeSkillExportDraft({
    title: "销售订单办理",
    description: "先查再改",
    planningMode: "fixed",
    exampleRequests: ["帮我查鲜生的单", "只看看"],
    successCriteria: "指定订单已改完",
    forbiddenActions: "不要删除",
  });
  const restored = normalizeSkillExportDraft(saved);
  assert.equal(restored.title, "销售订单办理");
  assert.equal(restored.description, "先查再改");
  assert.equal(restored.planningMode, "fixed");
  assert.match(restored.exampleRequests, /帮我查鲜生的单/);
  assert.equal(restored.successCriteria, "指定订单已改完");
  assert.equal(restored.forbiddenActions, "不要删除");
});

test("route summary uses business language only", () => {
  const summary = routeSummaryFromOutcome({
    name: "查询后编辑",
    when_to_use: "用户要先找到订单再改",
    steps: ["搜索/筛选销售订单", "修改销售订单"],
    auto_carry: [],
    ask_when: ["请指定要改的那一条"],
    composition: "先办理再请你选定",
    needs_confirm: true,
  });
  assert.equal(summary.name, "查询后编辑");
  assert.deepEqual(summary.steps, ["搜索/筛选销售订单", "修改销售订单"]);
  assert.equal(summary.askWhen[0], "请指定要改的那一条");
  assert.equal(JSON.stringify(summary).includes("capability_id"), false);
  assert.equal(JSON.stringify(summary).includes("binding"), false);
});
