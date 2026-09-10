import assert from "node:assert/strict";
import test from "node:test";
import { historyLifecycleView } from "./skillLifecycle.ts";

test("导出后显示已导出，不回退成阶段文案", () => {
  const view = historyLifecycleView({
    skill_lifecycle: "exported",
    published: true,
    skill_export_status: "exported",
  });
  assert.equal(view.label, "Skill 已导出");
  assert.equal(view.label.includes("阶段"), false);
});

test("只交能力未导出时提示待产出", () => {
  const view = historyLifecycleView({
    skill_lifecycle: "stage_six_done",
    published: true,
  });
  assert.equal(view.label, "已有能力，待产出 Skill");
});

test("重新导出中显示最新能力导出中", () => {
  assert.equal(
    historyLifecycleView({ skill_lifecycle: "generating" }).label,
    "正在按最新能力导出 Skill",
  );
});
