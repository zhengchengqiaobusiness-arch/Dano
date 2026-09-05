import test from "node:test";
import assert from "node:assert/strict";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { materializeHttpRequest } from "../src/execution/http-executor.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { attachCandidateSources } from "../src/inference/candidate-sources.js";
import { exportedCapabilityTitle } from "../src/export/skill-exporter.js";
import { questionKey } from "../src/export/skill-handbook.js";
import { exportableCapabilities, isPageResultQuery, isPrimaryCapability, pageRoleLabel, sessionCatalogSlice } from "../src/inference/export-scope.js";
import { collectUiObservations, collectionLeafUniform, flattenRequestValues, pickerEntity } from "../src/inference/field-resolver.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";

const recordedSubmit = {
  creator: "1",
  creatorName: "擎天柱",
  companyId: null,
  companyName: null,
  deptId: 103,
  deptName: null,
  processStatus: -1,
  createTime: "2026-09-05T11:23:27.977Z",
  reportType: 1,
  startDate: "2026-09-05",
  endDate: "2026-09-05",
  attachments: [],
  title: "1",
  todayContent: "1",
  planContent: "1",
  issueContent: "1",
  remark: "1",
  items: [
    { content: "1", progress: 30, itemType: 1, _X_ROW_KEY: "row_254", sort: 0 },
    { content: "1", itemType: 2, _X_ROW_KEY: "row_255", sort: 0 }
  ],
  startUserSelectAssignees: {}
};

function field(cap: CapabilityContract, path: string) {
  return cap.inputForm.find(item => item.path === path);
}

function fidelityEvents(): EvidenceEvent[] {
  return [{
    id: "ui-list", kind: "ui", sessionId: "doc", at: "2026-09-05T11:20:00.000Z",
    pageUrl: "https://example.test/web/#/oa/doc/list", eventType: "snapshot",
    form: [
      { name: "billCode", label: "单据编号", type: "text", value: "1" },
      { name: "processStatus", label: "单据状态", type: "select", value: "1", options: [{ value: 1, label: "审批中" }] },
      { name: "deptId", label: "申请部门", type: "select", value: "" }
    ]
  }, {
    id: "net-page", kind: "network", sessionId: "doc", at: "2026-09-05T11:20:01.000Z",
    pageUrl: "https://example.test/web/#/oa/doc/list",
    request: {
      method: "GET",
      url: "https://example.test/admin-api/oa/doc/page?pageNo=1&pageSize=20&billCode=1&processStatus=1&companyId=&creator=1&reportType=1",
      resourceType: "xhr",
      headers: {},
      query: {
        pageNo: "1",
        pageSize: "20",
        billCode: "1",
        processStatus: "1",
        companyId: "",
        creator: "1",
        reportType: "1"
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }, {
    id: "net-dept", kind: "network", sessionId: "doc", at: "2026-09-05T11:20:02.000Z",
    pageUrl: "https://example.test/web/#/oa/doc/list",
    request: {
      method: "GET",
      url: "https://example.test/admin-api/system/dept/simple-list",
      resourceType: "xhr",
      headers: {},
      query: {}
    },
    response: {
      status: 200,
      headers: {},
      body: { code: 0, data: [{ id: 103, name: "研发部门" }, { id: 106, name: "市场部" }] }
    }
  }, {
    id: "ui-write", kind: "ui", sessionId: "doc", at: "2026-09-05T11:23:20.000Z",
    pageUrl: "https://example.test/web/#/oa/doc/info", eventType: "input",
    form: [
      { name: "title", label: "标题", type: "text", value: "1" },
      { name: "todayContent", label: "工作总结", type: "textarea", value: "1" },
      { name: "planContent", label: "工作计划", type: "textarea", value: "1" },
      { name: "issueContent", label: "问题/协调事项", type: "textarea", value: "1" },
      { name: "remark", label: "备注", type: "textarea", value: "1" },
      { name: "content", label: "工作内容", type: "text", value: "1" },
      { name: "progress", label: "完成进度", type: "number", value: 30 }
    ]
  }, {
    id: "ui-confirm", kind: "ui", sessionId: "doc", at: "2026-09-05T11:23:26.000Z",
    pageUrl: "https://example.test/web/#/oa/doc/info", eventType: "click",
    text: "确认提交", label: "确认提交", tag: "button", role: "button"
  }, {
    id: "net-submit", kind: "network", sessionId: "doc", at: "2026-09-05T11:23:27.977Z",
    pageUrl: "https://example.test/web/#/oa/doc/info",
    correlatedUiEvidenceId: "ui-confirm",
    request: {
      method: "POST",
      url: "https://example.test/admin-api/oa/doc/submit",
      resourceType: "xhr",
      headers: {},
      query: {},
      body: recordedSubmit
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 17, msg: "" } }
  }, {
    id: "ui-stats", kind: "ui", sessionId: "doc", at: "2026-09-05T11:24:00.000Z",
    pageUrl: "https://example.test/web/#/oa/doc/statistics", eventType: "snapshot",
    form: [
      { name: "deptId", label: "部门", type: "select", value: "市场部" },
      { name: "reportType", label: "汇报类型", type: "select", value: "日报", options: [{ value: 1, label: "日报" }] },
      { name: "startDate", label: "开始日期", type: "date", value: "2026-09-01" },
      { name: "endDate", label: "结束日期", type: "date", value: "2026-09-05" }
    ]
  }, {
    id: "net-stats", kind: "network", sessionId: "doc", at: "2026-09-05T11:24:01.000Z",
    pageUrl: "https://example.test/web/#/oa/doc/statistics",
    request: {
      method: "GET",
      url: "https://example.test/admin-api/oa/doc/statistics?deptId=106&reportType=1&startDate=2026-09-01&endDate=2026-09-05",
      resourceType: "xhr",
      headers: {},
      query: {
        deptId: "106",
        reportType: "1",
        startDate: "2026-09-01",
        endDate: "2026-09-05"
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { submitted: 3, missing: 1 } } }
  }];
}

test("a numeric row label plus an empty-prompt value becomes the business field name", () => {
  const items = collectUiObservations([{
    id: "ui-cell", kind: "ui", sessionId: "doc", at: "2026-09-05T11:23:20.000Z",
    pageUrl: "https://example.test/web/#/oa/doc/info", eventType: "input",
    form: [{ name: "content", label: "1", type: "text", value: "样例-请输入工作内容" }]
  }]);
  assert.equal(items.some(item => item.label === "工作内容"), true);
});

test("object-array flatten keeps the union of row keys instead of only the first row", () => {
  const leaves = flattenRequestValues(recordedSubmit);
  assert.equal(leaves.some(item => item.path === "$.items[*].progress"), true);
  assert.equal(leaves.some(item => item.path === "$.items[*].itemType"), true);
  assert.equal(collectionLeafUniform(recordedSubmit, "$.items[*].itemType"), false);
  assert.equal(collectionLeafUniform(recordedSubmit, "$.items[*].progress"), false);
  assert.equal(collectionLeafUniform(recordedSubmit, "$.items[*].sort"), true);
  assert.equal(collectionLeafUniform(recordedSubmit, "$.items[*]._X_ROW_KEY"), false);
});

test("a leftover department select is not a people picker and does not steal creator", () => {
  assert.equal(pickerEntity({ name: "creator", label: "创建人" }), "user");
  assert.equal(pickerEntity({ name: "deptId", label: "申请部门" }), "dept");
  assert.equal(pickerEntity({ label: "组织机构" }), "dept");
  assert.equal(pickerEntity({ label: "人力审批" }), "user");
  assert.equal(pickerEntity({ label: "领导审批" }), "user");
  assert.equal(pickerEntity({ name: "processStatus", label: "审批结果" }), undefined);
  const events = fidelityEvents();
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const query = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/doc/page"))!;
  const creator = field(query, "$.creator")!;
  assert.equal(creator.source, "system");
  assert.equal(creator.defaultRule, 'literal:"1"');
  assert.notEqual(creator.label, "申请部门");
  assert.equal(creator.candidates?.type === "capability" ? creator.candidates.capabilityId : undefined, undefined);
  const sourced = attachCandidateSources(catalog, events);
  const sourcedCreator = sourced
    .find(item => item.transport.pathTemplate.endsWith("/oa/doc/page"))!
    .inputForm.find(item => item.path === "$.creator")!;
  assert.notEqual(sourcedCreator.candidates?.type, "capability");
  const dept = field(query, "$.deptId");
  assert.equal(dept?.source, "caller");
  assert.equal(dept?.required, false);
  assert.equal(dept?.label, "申请部门");
  const omitted = new URL(materializeHttpRequest(query, { billCode: "1", processStatus: "1" }).url);
  assert.equal(omitted.searchParams.get("deptId"), null);
  const passed = new URL(materializeHttpRequest(query, { billCode: "1", processStatus: "1", deptId: "106" }).url);
  assert.equal(passed.searchParams.get("deptId"), "106");
  assert.equal(field(query, "$.reportType")?.defaultRule, 'literal:"1"');
  assert.equal(field(query, "$.reportType")?.source, "system");
  const sourcedDept = sourced
    .find(item => item.transport.pathTemplate.endsWith("/oa/doc/page"))!
    .inputForm.find(item => item.path === "$.deptId")!;
  assert.equal(sourcedDept.candidates?.type, "capability");
  assert.match(
    sourced.find(item => item.id === (sourcedDept.candidates?.type === "capability" ? sourcedDept.candidates.capabilityId : ""))
      ?.transport.pathTemplate || "",
    /\/dept\/simple-list$/
  );
});

test("heterogeneous recorded rows stay a whole-table system template and replay with the same shape", () => {
  const events = fidelityEvents();
  const create = finalizeCapabilities(buildCapabilityCandidates(events), events)
    .find(item => item.transport.pathTemplate.endsWith("/oa/doc/submit"))!;
  const items = field(create, "$.items")!;
  assert.equal(items.source, "system");
  assert.match(items.defaultRule || "", /^literal:/);
  assert.deepEqual(JSON.parse(items.defaultRule!.slice("literal:".length)), recordedSubmit.items);
  assert.equal(field(create, "$.items[*].itemType")?.defaultRule, undefined);
  assert.equal(field(create, "$.items[*]._X_ROW_KEY")?.defaultRule, undefined);
  assert.equal(field(create, "$.items[*].sort")?.defaultRule, "literal:0");
  assert.equal(field(create, "$.items[*].content"), undefined);
  const firstContent = field(create, "$.items[0].content");
  const secondContent = field(create, "$.items[1].content");
  assert.equal(firstContent?.source, "caller");
  assert.equal(secondContent?.source, "caller");
  assert.match(firstContent?.label || "", /工作内容/);
  assert.equal(field(create, "$.items[0].progress")?.source, "caller");
  assert.equal(field(create, "$.items[0].progress")?.label, "完成进度");
  assert.equal(field(create, "$.items[1].progress"), undefined);

  const request = materializeHttpRequest(create, {
    title: "1",
    todayContent: "1",
    planContent: "1",
    issueContent: "1",
    remark: "1",
    content: "1",
    progress: 30
  });
  assert.deepEqual(request.body, recordedSubmit);
  assert.equal((request.body as { items: Array<{ itemType: number }> }).items[1]?.itemType, 2);
  assert.equal(Object.prototype.hasOwnProperty.call((request.body as { items: object[] }).items[1]!, "progress"), false);

  const split = materializeHttpRequest(create, {
    title: "1",
    todayContent: "1",
    planContent: "1",
    issueContent: "1",
    remark: "1",
    "items[0].content": "今日工作",
    "items[1].content": "明日计划",
    "items[0].progress": 40
  });
  const splitItems = (split.body as { items: Array<Record<string, unknown>> }).items;
  assert.equal(splitItems[0]?.content, "今日工作");
  assert.equal(splitItems[1]?.content, "明日计划");
  assert.equal(splitItems[0]?.progress, 40);
  assert.equal(splitItems[1]?.itemType, 2);
  assert.equal(Object.prototype.hasOwnProperty.call(splitItems[1]!, "progress"), false);
});

test("an unresolved from-rule falls back to the recorded successful request value", () => {
  const request = materializeHttpRequest({
    id: "create-doc",
    kind: "atomic",
    title: "新建",
    description: "新建",
    operation: "create",
    confidence: 1,
    transport: { method: "POST", urlTemplate: "https://example.test/oa/doc/submit", origin: "https://example.test", pathTemplate: "/oa/doc/submit" },
    inputSchema: { type: "object", properties: {} },
    outputSchema: { type: "object", properties: {} },
    inputForm: [{
      path: "$.deptId", name: "deptId", label: "deptId", valueType: "integer",
      source: "binding", required: false, requiredBasis: "not-observed", systemHandled: true,
      sourceDetail: "带出失败时按原值", widget: "number",
      defaultRule: "from:query-approval:$.data.activityNodes[*].candidateUsers[*].deptId|fallback:103"
    }],
    evidence: [],
    sideEffect: true,
    confirmation: { required: true },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "verified", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-05T00:00:00.000Z" }
  }, {});
  assert.equal(request.body?.deptId, 103);
});

test("a business summary query is a primary page result, not a leftover lookup", () => {
  const events = fidelityEvents();
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const stats = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/doc/statistics"))!;
  const page = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/doc/page"))!;
  const create = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/doc/submit"))!;
  assert.equal(isPageResultQuery(stats), true);
  assert.equal(isPrimaryCapability(stats, catalog), true);
  assert.equal(isPrimaryCapability(page, catalog), true);
  assert.equal(isPrimaryCapability(create, catalog), true);
  const exported = exportableCapabilities(catalog.map(item => ({
    ...item,
    validation: { ...item.validation, status: "verified" }
  })));
  assert.equal(exported.some(item => item.transport.pathTemplate.endsWith("/statistics")), true);
  const replay = materializeHttpRequest(stats, {
    deptId: 106,
    reportType: 1,
    startDate: "2026-09-01",
    endDate: "2026-09-05"
  });
  const url = new URL(replay.url);
  assert.equal(url.searchParams.get("deptId"), "106");
  assert.equal(url.searchParams.get("reportType"), "1");
  assert.equal(url.searchParams.get("startDate"), "2026-09-01");
  assert.equal(url.searchParams.get("endDate"), "2026-09-05");
  const statsDept = field(stats, "$.deptId");
  assert.equal(statsDept?.candidates?.type, "capability");
  const sourceId = statsDept?.candidates?.type === "capability" ? statsDept.candidates.capabilityId : "";
  const source = catalog.find(item => item.id === sourceId);
  assert.ok(source, sourceId);
  assert.match(source.transport.pathTemplate, /\/dept\/simple-list$/);
  assert.notEqual(sourceId, page.id);
});

test("later session export keeps a same-resource verified primary from another page", () => {
  const events = fidelityEvents();
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events).map(item => ({
    ...item,
    validation: { ...item.validation, status: "verified" as const }
  }));
  const later = events.filter(item => {
    if (item.kind === "network") return !item.request.url.includes("/statistics");
    return !/statistics/i.test(item.pageUrl || "");
  });
  const slice = sessionCatalogSlice(catalog, events, later);
  assert.equal(slice.some(item => item.transport.pathTemplate.endsWith("/oa/doc/page")), true);
  assert.equal(slice.some(item => item.transport.pathTemplate.endsWith("/oa/doc/submit")), true);
  assert.equal(slice.some(item => item.transport.pathTemplate.endsWith("/oa/doc/statistics")), true);
});

test("export titles distinguish list and statistics pages of the same resource", () => {
  const events = fidelityEvents();
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events).map(item => ({
    ...item,
    validation: { ...item.validation, status: "verified" as const }
  }));
  const page = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/doc/page"))!;
  const stats = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/doc/statistics"))!;
  const write = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/doc/submit"))!;
  const selected = [page, write, stats];
  assert.equal(pageRoleLabel(page.transport.pathTemplate), "列表");
  assert.equal(pageRoleLabel(stats.transport.pathTemplate), "统计");
  assert.equal(exportedCapabilityTitle(page, "工作日报管理", selected), "查询工作日报列表");
  assert.equal(exportedCapabilityTitle(stats, "工作日报管理", selected), "查询工作日报统计");
  assert.notEqual(exportedCapabilityTitle(write, "工作日报管理", selected), "查询工作日报列表");
  assert.notEqual(exportedCapabilityTitle(write, "工作日报管理", selected), "查询工作日报统计");
});

test("indexed collection cells keep their row path as the question key", () => {
  assert.equal(questionKey({
    path: "$.items[0].progress",
    name: "progress",
    label: "progress",
    valueType: "integer",
    source: "caller",
    required: false,
    requiredBasis: "not-observed",
    systemHandled: false,
    sourceDetail: "",
    widget: "number"
  }), "items[0].progress");
});
