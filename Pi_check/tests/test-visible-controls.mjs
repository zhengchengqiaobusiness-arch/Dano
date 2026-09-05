/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { projectVisibleControlSnapshot, summarizeVisibleControls } from "../src/visible-controls.mjs";

test("可见控件只投影已有字段，不判断能力", () => {
  const snapshot = projectVisibleControlSnapshot({
    seq: 9,
    kind: "visible_control",
    payload: {
      url: "https://example.com/#/form",
      reason: "navigated",
      controls: [
        { region: "form", name: "title", label: "标题", placeholder: "请输入标题", control_kind: "input" },
        { region: "form", name: "", label: "开始日期", control_kind: "date", required_mark: true },
        { region: "form", name: "", label: "附件", control_kind: "upload" },
      ],
    },
  });
  assert.equal(snapshot.kind, "visible_control");
  assert.equal(snapshot.count, 3);
  assert.equal(snapshot.controls[1].control_kind, "date");
  assert.equal(snapshot.controls[1].required_mark, true);
  assert.equal(summarizeVisibleControls(snapshot.controls), "标题、开始日期、附件");
  assert.ok(!JSON.stringify(snapshot).includes("capability"));
});
