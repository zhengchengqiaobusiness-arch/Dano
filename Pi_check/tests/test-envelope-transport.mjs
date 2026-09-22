/**
 * 信封运输：merge 覆盖、unresolved 省略、buildRoutes 拓扑、展示契约反方向、监控换页。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { mergeCapabilityIntoDraft } from "../src/result-merge.mjs";
import { buildRoutes } from "../src/skill-export/contract-materialize.mjs";
import { assertPageDisplayContract, SubmitRejectedError } from "../src/result-gate.mjs";
import {
  pageIdentityUrl,
  isMonitorPageEvent,
  wrapContextSkillContent,
  CONTEXT_SKILL_META_START,
} from "../src/monitor-page.mjs";
import { createExportToolHost, createMonitorPiToolHost } from "../src/pi-tools.mjs";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { buildMonitorDrivePrompt, buildMonitorContinuePrompt } from "../src/pi-session.mjs";

test("省略 unresolved 保留上次，传入空数组才清空", () => {
  let draft = mergeCapabilityIntoDraft({}, {
    capability: {
      capability_id: "cap_a",
      request_refs: [{ step_id: "step_a", usage: "execute" }],
    },
    steps: [{ step_id: "step_a", params: [] }],
    unresolved: [{ key: "createTime", reason: "未识别" }],
  });
  assert.equal(draft.unresolved.length, 1);
  draft = mergeCapabilityIntoDraft(draft, {
    capability: {
      capability_id: "cap_a",
      request_refs: [{ step_id: "step_a", usage: "execute" }],
    },
    steps: [{ step_id: "step_a", params: [] }],
  });
  assert.equal(draft.unresolved.length, 1);
  assert.equal(draft.unresolved[0].key, "createTime");
  draft = mergeCapabilityIntoDraft(draft, {
    capability: {
      capability_id: "cap_a",
      request_refs: [{ step_id: "step_a", usage: "execute" }],
    },
    steps: [{ step_id: "step_a", params: [] }],
    unresolved: [],
  });
  assert.equal(draft.unresolved.length, 0);
});

test("同 id 再交时该项 links 与 relations 替换不追加", () => {
  let draft = mergeCapabilityIntoDraft({}, {
    capability: {
      capability_id: "cap_write",
      request_refs: [{ step_id: "step_write", usage: "execute" }],
    },
    steps: [{ step_id: "step_write", params: [] }],
    links: [{
      source_step_id: "step_query",
      source_path: "data.id",
      target_step_id: "step_write",
      target_path: "body.id",
    }],
    capability_relations: [{
      from_capability: "cap_query",
      to_capability: "cap_write",
      type: "suggested_call_chain",
    }],
  });
  draft = mergeCapabilityIntoDraft(draft, {
    capability: {
      capability_id: "cap_query",
      request_refs: [{ step_id: "step_query", usage: "execute" }],
    },
    steps: [{ step_id: "step_query", params: [] }],
    links: [{
      source_step_id: "step_query",
      source_path: "data.rows",
      target_step_id: "step_detail",
      target_path: "query.id",
    }],
    capability_relations: [{
      from_capability: "cap_query",
      to_capability: "cap_detail",
    }],
  });
  draft = mergeCapabilityIntoDraft(draft, {
    capability: {
      capability_id: "cap_write",
      request_refs: [{ step_id: "step_write", usage: "execute" }],
    },
    steps: [{ step_id: "step_write", params: [] }],
    links: [{
      source_step_id: "step_detail",
      source_path: "data.id",
      target_step_id: "step_write",
      target_path: "body.bizId",
    }],
    capability_relations: [{
      from_capability: "cap_detail",
      to_capability: "cap_write",
    }],
  });
  assert.equal(draft.links.length, 2);
  assert.equal(draft.links.filter((item) => item.target_step_id === "step_write").length, 1);
  assert.equal(draft.links.find((item) => item.target_step_id === "step_write").target_path, "body.bizId");
  assert.equal(draft.capability_relations.filter((item) => item.to_capability === "cap_write").length, 1);
  assert.equal(draft.capability_relations.find((item) => item.to_capability === "cap_write").from_capability, "cap_detail");
  assert.ok(draft.capability_relations.some((item) => item.to_capability === "cap_detail"));
});

test("菱形关系全部进入 default，孤立写入不丢", () => {
  const diamond = buildRoutes({
    capabilities: [
      { capability_id: "query" },
      { capability_id: "detail" },
      { capability_id: "create" },
    ],
    capability_relations: [
      { from_capability: "query", to_capability: "detail" },
      { from_capability: "query", to_capability: "create" },
      { from_capability: "detail", to_capability: "create" },
    ],
  });
  const defaultSteps = diamond.find((item) => item.route_id === "default").steps;
  assert.deepEqual(defaultSteps, ["query", "detail", "create"]);

  const leftover = buildRoutes({
    capabilities: [
      { capability_id: "query" },
      { capability_id: "detail" },
      { capability_id: "create" },
    ],
    capability_relations: [
      { from_capability: "query", to_capability: "detail" },
    ],
  });
  assert.deepEqual(leftover.find((item) => item.route_id === "default").steps, ["query", "detail", "create"]);

  const self = buildRoutes({
    capabilities: [{ capability_id: "only" }],
    capability_relations: [{ from_capability: "only", to_capability: "only" }],
  });
  assert.deepEqual(self.find((item) => item.route_id === "default").steps, ["only"]);
});

test("闸门反方向：调用方键必须进 schema，对象数组要有 items.properties，禁自指 links", () => {
  assert.throws(
    () => assertPageDisplayContract({
      capabilities: [{
        capability_id: "cap_create",
        request_refs: [{ step_id: "step_submit", usage: "execute" }],
        input_schema: { type: "object", properties: { title: { type: "string" } } },
      }],
      steps: [{
        step_id: "step_submit",
        params: [
          { key: "title", path: "body.title", exposed_to_user: true },
          { key: "items", path: "body.items", exposed_to_user: true },
        ],
      }],
    }),
    (error) => error instanceof SubmitRejectedError && /items/.test(error.message),
  );
  assert.throws(
    () => assertPageDisplayContract({
      capabilities: [{
        capability_id: "cap_create",
        request_refs: [{ step_id: "step_submit", usage: "execute" }],
        input_schema: {
          type: "object",
          properties: {
            items: { type: "array", title: "明细", items: { type: "object", properties: {} } },
          },
        },
      }],
      steps: [{
        step_id: "step_submit",
        params: [{ key: "items", path: "body.items", type: "array", exposed_to_user: true }],
      }],
    }),
    (error) => error instanceof SubmitRejectedError && /items\.properties/.test(error.message),
  );
  assert.doesNotThrow(() => assertPageDisplayContract({
    capabilities: [{
      capability_id: "cap_create",
      request_refs: [{ step_id: "step_submit", usage: "execute" }],
      input_schema: {
        type: "object",
        properties: {
          recipients: {
            type: "array",
            items: { type: "string", format: "name-ref" },
          },
        },
      },
    }],
    steps: [{
      step_id: "step_submit",
      params: [{ key: "recipients", path: "body.recipients", type: "array", exposed_to_user: true }],
    }],
  }));
  assert.throws(
    () => assertPageDisplayContract({
      capabilities: [{ capability_id: "cap_x" }],
      steps: [],
      links: [{
        source_step_id: "step_save",
        target_step_id: "step_save",
        source_path: "data.id",
        target_path: "body.id",
      }],
    }),
    (error) => error instanceof SubmitRejectedError && /不能相同/.test(error.message),
  );
  assert.doesNotThrow(() => assertPageDisplayContract(sampleResult()));
});

test("监控换页只认主文档 URL（含 hash），弹层不算新页", () => {
  assert.equal(
    pageIdentityUrl("https://a.example/app/#/daily"),
    pageIdentityUrl("https://a.example/app/#/daily"),
  );
  assert.notEqual(
    pageIdentityUrl("https://a.example/app/#/daily"),
    pageIdentityUrl("https://a.example/app/#/weekly"),
  );
  assert.equal(isMonitorPageEvent("page_navigated", { url: "https://a.example/app/#/x", is_main: true }), true);
  assert.equal(isMonitorPageEvent("page_navigated", { url: "https://a.example/app/#/x", is_main: false }), false);
  assert.equal(isMonitorPageEvent("visible_control", { url: "https://a.example/app/#/x", reason: "routed" }), true);
  assert.equal(isMonitorPageEvent("visible_control", { url: "https://a.example/app/#/x", reason: "snapshot" }), false);
  const wrapped = wrapContextSkillContent("正文", {
    entryUrl: "https://a.example/app",
    pageUrl: "https://a.example/app/#/daily",
    reconUntilSeq: 12,
  });
  assert.match(wrapped, new RegExp(CONTEXT_SKILL_META_START));
  assert.match(wrapped, /recon_until_seq: 12/);
  assert.match(wrapped, /正文/);
});

test("监控驱动词禁止交能力；出包 host 能只读 context", async () => {
  const drive = buildMonitorDrivePrompt({
    targetUrl: "http://example.com",
    goal: "办理",
    pageUrl: "http://example.com/#/a",
    reconUntilSeq: 3,
  });
  assert.match(drive, /监控 PI/);
  assert.match(drive, /禁止[^\n]*submit_recording_result/);
  assert.match(drive, /禁止[^\n]*submit_recording_capability/);
  assert.doesNotMatch(drive, /立刻 submit_recording_result/);
  assert.match(buildMonitorContinuePrompt(), /write_context_skill/);
  assert.match(buildMonitorContinuePrompt(), /禁止交能力/);

  const harness = await createHarness();
  try {
    const session = await harness.evidence.create({ targetUrl: "http://x", goal: "g" });
    await harness.files.writeContextSkill(session.id, "本场 overlay");
    const exported = createExportToolHost({
      files: harness.files,
      recordingId: session.id,
      draft: sampleResult(),
    });
    const ctx = await exported.read_context_skill();
    assert.equal(ctx.found, true);
    assert.match(ctx.content, /本场 overlay/);
    const monitor = createMonitorPiToolHost({
      recordingId: session.id,
      evidence: harness.evidence,
      files: harness.files,
      getPageMeta: () => ({ entryUrl: "http://x", pageUrl: "http://x/#/p", reconUntilSeq: 4 }),
    });
    const written = await monitor.write_context_skill({ content: "第一页怎么点" });
    assert.equal(written.ok, true);
    const saved = await harness.files.readContextSkill(session.id);
    assert.match(saved, /recon_until_seq: 4/);
    assert.match(saved, /第一页怎么点/);
    assert.match(written.message, /等待下一页/);

    const denied = await monitor.control_in_app_browser({ action: "open_page", url: "http://x/#/other" });
    assert.equal(denied.ok, false);
    assert.match(denied.error, /open_page/);
    const clickDenied = await monitor.control_in_app_browser({ action: "click" });
    assert.equal(clickDenied.ok, false);
    assert.match(clickDenied.error, /click/);

    const withBrowser = createMonitorPiToolHost({
      recordingId: session.id,
      evidence: harness.evidence,
      files: harness.files,
      getBrowser: () => ({
        inspect: async ({ includeScreenshot = false } = {}) => ({
          available: true,
          url: "http://x/#/p",
          controls: [],
          actions: [],
          ...(includeScreenshot ? { screenshot: { data: "QQQQ", width: 8, height: 8 } } : {}),
        }),
      }),
    });
    const shot = await withBrowser.control_in_app_browser({ action: "screenshot", as_image: true });
    assert.equal(shot.ok, false);
    assert.match(shot.error, /screenshot/);
    const snap = await withBrowser.control_in_app_browser({ action: "snapshot" });
    assert.equal(snap.url, "http://x/#/p");
  } finally {
    await harness.cleanup();
  }
});
