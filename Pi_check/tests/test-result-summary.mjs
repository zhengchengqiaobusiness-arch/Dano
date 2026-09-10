/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { RecordingFiles, parseLeadingJson } from "../src/fs-store.mjs";
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
  assert.equal(summary.recording_id, "rec_demo");
});

test("已交能力从磁盘列出，导出进程也能打开", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "dano-disk-results-"));
  const files = new RecordingFiles(root);
  const recordingId = "rec_disk_list";
  await files.writeDraft(recordingId, {
    recording_id: recordingId,
    saved_at: "2026-09-10T10:00:00.000Z",
    title: "招聘计划",
    draft: {
      title: "招聘计划",
      capabilities: [
        { capability_id: "search", name: "查询", title: "查询招聘计划" },
        { capability_id: "create", name: "新增", title: "新增招聘计划" },
      ],
      steps: [{ step_id: "s1", method: "GET", path: "/api/list" }],
    },
  });
  const catalog = new ResultsCatalog(files);
  const rows = await catalog.listPublished("oa");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, recordingId);
  assert.equal(rows[0].recording_id, recordingId);
  assert.equal(rows[0].capability_count, 2);
  const detail = await catalog.detailOf(recordingId);
  assert.equal(detail.draft.capabilities.length, 2);
  assert.equal(detail.recording_id, recordingId);
});

test("草稿多段 JSON 只读第一段", () => {
  const parsed = parseLeadingJson("{\"title\":\"招聘计划\",\"capabilities\":[1]}\n{}");
  assert.equal(parsed.title, "招聘计划");
});
