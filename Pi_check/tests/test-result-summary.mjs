/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { ResultsCatalog } from "../src/results-catalog.mjs";
import {
  displayTitleFromResult,
  looksLikeRecordingGoal,
  requestCountFromResult,
} from "../src/result-summary.mjs";
import { sampleResult } from "./helpers/harness.mjs";

const GOAL = "请将我接下来在页面中实际完成的每项业务操作分别生成一个可调用能力。";

test("录制目标文案不是 Skill 名，请求数只数合同里的步骤", () => {
  const result = sampleResult({
    capabilities: [
      { capability_id: "a", name: "search", title: "查询记录" },
      { capability_id: "b", name: "submit", title: "提交记录" },
    ],
    steps: [
      { step_id: "s1", method: "GET", path: "/api/page" },
      { step_id: "s2", method: "POST", path: "/api/save" },
    ],
  });
  assert.equal(looksLikeRecordingGoal(GOAL), true);
  assert.equal(displayTitleFromResult({ userTitle: GOAL, goal: GOAL, result }), "查询记录、提交记录");
  assert.equal(requestCountFromResult(result), 2);
});

test("目录写入时不用证据条数冒充请求数", async () => {
  const catalog = new ResultsCatalog({ initialize: async () => {} });
  const result = sampleResult();
  const summary = await catalog.remember({
    recordingId: "rec_demo",
    action: "action_1",
    title: GOAL,
    goal: GOAL,
    result,
    evidenceCount: 955,
  });
  assert.equal(summary.title, "创建请假");
  assert.equal(summary.request_count, 1);
  assert.notEqual(summary.request_count, 955);
});
