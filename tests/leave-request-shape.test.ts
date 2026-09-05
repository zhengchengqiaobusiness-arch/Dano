import test from "node:test";
import assert from "node:assert/strict";
import type { CapabilityContract, EvidenceEvent, InputFormField } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { reviewCatalog, reviewSession } from "../src/review/catalog-review.js";
import { materializeHttpRequest } from "../src/execution/http-executor.js";

const PAGE = "http://admin.dianshixinxi.com:90/oa/duty/leave";
const QUERY_URL = "http://admin.dianshixinxi.com:90/admin-api/oa/duty-leave/page?pageNo=1&pageSize=10&type=3&processStatus=0&reason=1&createTime%5B0%5D=2026-09-15%2000%3A00%3A00&createTime%5B1%5D=2026-09-17%2023%3A59%3A59";
const CREATE_URL = "http://admin.dianshixinxi.com:90/admin-api/oa/duty-leave/submit-process";

const RECORDED_QUERY = {
  pageNo: "1",
  pageSize: "10",
  type: "3",
  processStatus: "0",
  reason: "1",
  "createTime[0]": "2026-09-15 00:00:00",
  "createTime[1]": "2026-09-17 23:59:59"
};

const RECORDED_CREATE = {
  type: 2,
  reason: "123123",
  startTime: 1789401600000,
  endTime: 1789920000000,
  day: 6,
  leaveBalance: 0,
  projectCode: "12312",
  projectName: "123",
  actualStartTime: 1789315200000,
  actualEndTime: 1790611200000,
  actualDay: 123,
  billType: "oa_duty_leave",
  processDefKey: "oa_duty_leave",
  attachments: [] as unknown[],
  startUserSelectAssignees: { Activity_0ag2wyz: [173] }
};

function fieldByName(capability: CapabilityContract, name: string) {
  return capability.inputForm.find(field => field.name === name);
}

function callerNames(capability: CapabilityContract) {
  return capability.inputForm.filter(field => field.source === "caller").map(field => field.name);
}

function requestKeys(sample: Record<string, unknown>) {
  return Object.keys(sample).sort();
}

function leaveEvents(): EvidenceEvent[] {
  const queryForm = [
    { name: "type", label: "请假类型", type: "select", value: "年假", required: false, options: [
      { value: "1", label: "病假" }, { value: "2", label: "事假" }, { value: "3", label: "年假" }
    ] },
    { name: "createTime[0]", label: "开始日期", type: "date", value: "2026-09-15", rangeIndex: 0 },
    { name: "createTime[1]", label: "结束日期", type: "date", value: "2026-09-17", rangeIndex: 1 },
    { label: "开始日期-1", type: "date", value: "2026-09-15", rangeIndex: 0 },
    { label: "结束日期-2", type: "date", value: "2026-09-17", rangeIndex: 1 },
    { name: "processStatus", label: "审批结果", type: "select", value: "未提交", options: [
      { value: "0", label: "未提交" }, { value: "1", label: "审批中" }
    ] },
    { name: "reason", label: "原因", type: "text", value: "1" }
  ];
  const createForm = [
    { name: "type", label: "请假类型", type: "select", value: "事假", required: true, options: [
      { value: "1", label: "病假" }, { value: "2", label: "事假" }, { value: "3", label: "年假" }
    ] },
    { name: "projectCode", label: "请输入项目编码", type: "text", value: "12312" },
    { name: "projectName", label: "请输入项目名称", type: "text", value: "123" },
    { name: "startTime", label: "开始时间", type: "date", value: "2026-09-15", required: true },
    { name: "endTime", label: "结束时间", type: "date", value: "2026-09-21", required: true },
    { name: "day", label: "请假天数", type: "number", value: 6, required: true },
    { name: "actualStartTime", label: "实际开始", type: "date", value: "2026-09-14" },
    { name: "actualEndTime", label: "实际结束", type: "date", value: "2026-09-29" },
    { name: "actualDay", label: "实际天数", type: "number", value: 123 },
    { name: "reason", label: "原因", type: "textarea", value: "123123", required: true },
    { name: "Activity_0ag2wyz", label: "人力审批", type: "picker", value: "LS部门", required: true }
  ];
  return [{
    id: "ui-query", kind: "ui", sessionId: "leave", at: "2026-09-03T12:00:00.000Z",
    pageUrl: PAGE, eventType: "snapshot", form: queryForm
  }, {
    id: "ui-search", kind: "ui", sessionId: "leave", at: "2026-09-03T12:00:01.000Z",
    pageUrl: PAGE, eventType: "click", text: "搜索", label: "搜索", form: queryForm
  }, {
    id: "ui-create", kind: "ui", sessionId: "leave", at: "2026-09-03T12:01:00.000Z",
    pageUrl: `${PAGE}apply/create`, eventType: "snapshot", form: createForm
  }, {
    id: "ui-submit", kind: "ui", sessionId: "leave", at: "2026-09-03T12:01:02.000Z",
    pageUrl: `${PAGE}apply/create`, eventType: "click", text: "提交", label: "提交", form: createForm
  }, {
    id: "net-dict", kind: "network", sessionId: "leave", at: "2026-09-03T12:00:00.100Z",
    pageUrl: PAGE,
    request: { method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/system/dict-data/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: [
      { dictType: "oa_duty_leave_type", value: "1", label: "病假" },
      { dictType: "oa_duty_leave_type", value: "2", label: "事假" },
      { dictType: "oa_duty_leave_type", value: "3", label: "年假" }
    ] } }
  }, {
    id: "net-users", kind: "network", sessionId: "leave", at: "2026-09-03T12:00:00.200Z",
    pageUrl: PAGE,
    request: { method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [
      { id: 173, username: "LSBM", nickname: "LS部门" },
      { id: 1, username: "admin", nickname: "管理员" }
    ] } } }
  }, {
    id: "net-balance", kind: "network", sessionId: "leave", at: "2026-09-03T12:01:00.400Z",
    pageUrl: `${PAGE}apply/create`,
    request: {
      method: "GET",
      url: "http://admin.dianshixinxi.com:90/admin-api/oa/duty-leave/get-balance?type=2",
      resourceType: "xhr",
      headers: {},
      query: { type: "2" }
    },
    response: { status: 200, headers: {}, body: { success: true, data: { leaveBalance: 0 } } }
  }, {
    id: "net-query", kind: "network", sessionId: "leave", at: "2026-09-03T12:00:02.000Z",
    pageUrl: PAGE, correlatedUiEvidenceId: "ui-search",
    request: {
      method: "GET",
      url: QUERY_URL,
      resourceType: "xhr",
      headers: {},
      query: RECORDED_QUERY
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [{ no: "QJD202609030016" }], total: 1 } } }
  }, {
    id: "net-create", kind: "network", sessionId: "leave", at: "2026-09-03T12:01:03.000Z",
    pageUrl: `${PAGE}apply/create`, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST",
      url: CREATE_URL,
      resourceType: "xhr",
      headers: {},
      query: {},
      body: RECORDED_CREATE
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 17 } }
  }];
}

function leaveCatalog() {
  const events = leaveEvents();
  return { events, catalog: finalizeCapabilities(buildCapabilityCandidates(events), events) };
}

function assertBound(field: InputFormField | undefined, label: string, extra?: (field: InputFormField) => void) {
  assert.ok(field, `missing field for ${label}`);
  assert.equal(field!.label, label, `${field!.name} label`);
  extra?.(field!);
}

test("query skill keys and labels come from the recorded page request, not invented dates", () => {
  const { catalog } = leaveCatalog();
  const query = catalog.find(item => item.operation === "query" && item.transport.pathTemplate.includes("duty-leave/page"))!;
  assert.ok(query, catalog.map(item => item.transport.pathTemplate).join(","));
  const names = query.inputForm.map(field => field.name);
  for (const key of requestKeys(RECORDED_QUERY)) {
    assert.equal(names.includes(key), true, `query missing recorded key ${key}: ${names.join(",")}`);
  }
  assert.equal(names.some(name => /开始日期-1|结束日期-2|startTime|endTime/.test(name)), false, names.join(","));
  assertBound(fieldByName(query, "type"), "请假类型", field => {
    assert.equal(field.source, "caller");
    assert.equal(field.widget, "select");
  });
  assertBound(fieldByName(query, "processStatus"), "审批结果", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(query, "reason"), "原因", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(query, "createTime[0]"), "开始日期", field => {
    assert.equal(field.source, "caller");
    assert.equal(field.widget, "date");
    assert.equal(field.dateClock, "00:00:00");
  });
  assertBound(fieldByName(query, "createTime[1]"), "结束日期", field => {
    assert.equal(field.source, "caller");
    assert.equal(field.widget, "date");
    assert.equal(field.dateClock, "23:59:59");
  });
  assert.equal(fieldByName(query, "pageNo")?.source, "system");
  assert.equal(fieldByName(query, "pageSize")?.source, "system");
});

test("create skill keeps every recorded body key and does not invent duration for actualDay", () => {
  const { catalog } = leaveCatalog();
  const create = catalog.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.ok(create, catalog.map(item => item.transport.pathTemplate).join(","));
  const names = create.inputForm.map(field => field.name);
  for (const key of requestKeys(RECORDED_CREATE)) {
    assert.equal(names.includes(key), true, `create missing recorded key ${key}: ${names.join(",")}`);
  }
  assertBound(fieldByName(create, "type"), "请假类型", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(create, "projectCode"), "项目编码", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(create, "projectName"), "项目名称", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(create, "startTime"), "开始时间", field => {
    assert.equal(field.source, "caller");
    assert.equal(field.widget, "date");
    assert.equal(field.valueType, "integer");
  });
  assertBound(fieldByName(create, "endTime"), "结束时间", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(create, "day"), "请假天数", field => {
    assert.equal(field.source, "caller");
    assert.ok(!field.defaultRule || field.defaultRule.startsWith("computed:") || field.defaultRule.startsWith("literal:"));
  });
  assertBound(fieldByName(create, "actualStartTime"), "实际开始", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(create, "actualEndTime"), "实际结束", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(create, "actualDay"), "实际天数", field => {
    assert.equal(field.source, "caller");
    assert.equal(field.defaultRule, undefined);
  });
  assertBound(fieldByName(create, "reason"), "原因", field => assert.equal(field.source, "caller"));
  assertBound(fieldByName(create, "Activity_0ag2wyz"), "人力审批", field => assert.equal(field.source, "caller"));
  assert.match(fieldByName(create, "leaveBalance")?.defaultRule || "", /^(from:.+leaveBalance|literal:0)/);
  assert.match(fieldByName(create, "billType")?.defaultRule || "", /^literal:"?oa_duty_leave"?$/);
  assert.match(fieldByName(create, "processDefKey")?.defaultRule || "", /^literal:"?oa_duty_leave"?$/);
  assert.equal(fieldByName(create, "attachments")?.defaultRule, "literal:[]");
  assert.ok(["system", "computed"].includes(fieldByName(create, "startUserSelectAssignees")?.source || ""));
  assert.equal(callerNames(create).some(name => name === "attachments" || name === "billType"), false);
});

test("caller page values rematerialize the recorded query string and create body", () => {
  const { catalog } = leaveCatalog();
  const query = catalog.find(item => item.transport.pathTemplate.includes("duty-leave/page"))!;
  const create = catalog.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const queryRequest = materializeHttpRequest(query, {
    type: "年假",
    processStatus: "未提交",
    reason: "1",
    "createTime[0]": "2026-09-15",
    "createTime[1]": "2026-09-17"
  });
  assert.deepEqual(queryRequest.query, RECORDED_QUERY);

  const from = /^from:([^:]+):/.exec(fieldByName(create, "leaveBalance")?.defaultRule || "");
  const lookupId = from?.[1];
  const createRequest = materializeHttpRequest(create, {
    type: "事假",
    reason: "123123",
    startTime: "2026-09-15",
    endTime: "2026-09-21",
    actualStartTime: "2026-09-14",
    actualEndTime: "2026-09-29",
    actualDay: 123,
    day: 6,
    projectCode: "12312",
    projectName: "123",
    Activity_0ag2wyz: 173
  }, lookupId ? {
    lookupBodies: { [lookupId]: { data: { leaveBalance: 0 } } }
  } : undefined);
  assert.deepEqual(createRequest.body, RECORDED_CREATE);
});

test("session review only returns this page and ignores other-page catalog entries", () => {
  const { events, catalog } = leaveCatalog();
  const other: CapabilityContract = {
    ...catalog[0]!,
    id: "create-other-page",
    title: "新建采购订单",
    operation: "create",
    role: "primary",
    transport: { method: "POST", urlTemplate: "https://example.test/erp/purchase-order/create", origin: "https://example.test", pathTemplate: "/erp/purchase-order/create" },
    evidence: [{ eventId: "purchase-net", sessionId: "purchase", kind: "network", at: "2026-09-02T10:00:00.000Z", status: 200 }],
    validation: { version: 2, status: "candidate", checks: [{ name: "caller-fields-backed-by-ui", ok: false, detail: "存在没有页面输入证据的调用方字段" }] }
  };
  const otherEvents: EvidenceEvent[] = [{
    id: "purchase-net", kind: "network", sessionId: "purchase", at: "2026-09-02T10:00:00.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order",
    request: { method: "POST", url: "https://example.test/erp/purchase-order/create", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: {} }
  }];
  const { capabilities, review } = reviewSession([...catalog, other], [...events, ...otherEvents], events);
  assert.equal(capabilities.some(item => item.id === "create-other-page"), false);
  assert.equal(capabilities.some(item => item.transport.pathTemplate.includes("duty-leave/page")), true);
  assert.equal(capabilities.some(item => item.transport.pathTemplate.includes("submit-process")), true);
  assert.equal(review.primaryTitles.some(title => /采购/.test(title)), false);
  assert.equal(reviewCatalog([...catalog, other], events).primaryCount > review.primaryCount, true);
});
