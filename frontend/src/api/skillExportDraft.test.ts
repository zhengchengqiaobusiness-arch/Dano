import assert from "node:assert/strict";
import test from "node:test";
import {
  normalizeSkillExportDraft,
  routeSummaryFromOutcome,
  serializeSkillExportDraft,
} from "./skillExportDraft.ts";

test("draft restore keeps the display title", () => {
  const saved = serializeSkillExportDraft({
    title: "销售订单办理",
    description: "先查再改",
    planningMode: "fixed",
  });
  const restored = normalizeSkillExportDraft(saved);
  assert.equal(restored.title, "销售订单办理");
  assert.equal("planningMode" in restored, false);
  assert.equal("description" in restored, false);
});

test("unreadable draft does not invent a title", () => {
  const restored = normalizeSkillExportDraft({});
  assert.equal(restored.title, "");
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
