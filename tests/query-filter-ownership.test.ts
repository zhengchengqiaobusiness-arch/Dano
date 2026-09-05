import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, InputFormField } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { validateCapability } from "../src/validation/validator.js";
import { materializeHttpRequest } from "../src/execution/http-executor.js";

const PAGE = "http://admin.example.test/workspace/todo";
const API = "http://admin.example.test/admin-api/bpm/task/todo-page";

const CATEGORY_OPTIONS = [
  { value: 118, label: "OA办公" },
  { value: 119, label: "CRM客户资源" },
  { value: 120, label: "HRM人力资源" }
];

const PROCESS_OPTIONS = [
  { value: "ct_contract_reconciliation", label: "合同对账" },
  { value: "oa_duty_leave", label: "请假申请" }
];

function fieldByName(capability: { inputForm: InputFormField[] }, name: string) {
  return capability.inputForm.find(field => field.name === name);
}

function todoFilterEvents(): EvidenceEvent[] {
  return [{
    id: "ui-page", kind: "ui", sessionId: "todo", at: "2026-09-04T05:00:00.000Z",
    pageUrl: PAGE, eventType: "snapshot",
    form: [
      { name: "name", label: "任务名称", type: "text", value: "请假" },
      {
        name: "category", label: "流程分类", type: "select", value: "OA办公",
        options: CATEGORY_OPTIONS
      }
    ]
  }, {
    id: "ui-filter", kind: "ui", sessionId: "todo", at: "2026-09-04T05:00:10.000Z",
    pageUrl: PAGE, eventType: "snapshot",
    form: [
      {
        name: "processDefinitionKey", label: "所属流程", type: "select", value: "合同对账",
        options: PROCESS_OPTIONS
      },
      { name: "createTime", label: "开始日期", type: "date", value: "2026-09-01", rangeIndex: 0 },
      { name: "createTime", label: "结束日期", type: "date", value: "2026-09-04", rangeIndex: 1 }
    ]
  }, {
    id: "ui-confirm", kind: "ui", sessionId: "todo", at: "2026-09-04T05:00:11.000Z",
    pageUrl: PAGE, eventType: "click", text: "确认", label: "确认"
  }, {
    id: "net-todo", kind: "network", sessionId: "todo", at: "2026-09-04T05:00:12.000Z",
    pageUrl: PAGE, correlatedUiEvidenceId: "ui-confirm",
    request: {
      method: "GET",
      url: `${API}?category=118&createTime%5B0%5D=2026-09-01+00:00:00&createTime%5B1%5D=2026-09-04+00:00:00&name=%E8%AF%B7%E5%81%87&pageNo=1&pageSize=10&processDefinitionKey=ct_contract_reconciliation`,
      resourceType: "xhr",
      headers: {},
      query: {
        pageNo: "1",
        pageSize: "10",
        name: "请假",
        category: "118",
        processDefinitionKey: "ct_contract_reconciliation",
        "createTime[0]": "2026-09-01 00:00:00",
        "createTime[1]": "2026-09-04 00:00:00"
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];
}

test("query filters on the page stay caller; hidden constants stay system", () => {
  const capability = buildCapabilityCandidates(todoFilterEvents())[0]!;
  const name = fieldByName(capability, "name")!;
  const category = fieldByName(capability, "category")!;
  const process = fieldByName(capability, "processDefinitionKey")!;
  const start = fieldByName(capability, "createTime[0]")!;
  const end = fieldByName(capability, "createTime[1]")!;
  const pageNo = fieldByName(capability, "pageNo")!;

  assert.equal(name.source, "caller");
  assert.equal(name.label, "任务名称");
  assert.equal(name.widget, "text");
  assert.equal(name.candidates, undefined);

  assert.equal(category.source, "caller");
  assert.equal(category.label, "流程分类");
  assert.equal(category.widget, "select");
  assert.equal(category.systemHandled, false);
  assert.deepEqual(
    category.candidates?.type === "static" ? category.candidates.values.map(item => item.label) : [],
    CATEGORY_OPTIONS.map(item => item.label)
  );

  assert.equal(process.source, "caller");
  assert.equal(process.label, "所属流程");
  assert.notEqual(process.defaultRule, "literal:ct_contract_reconciliation");
  assert.equal(process.systemHandled, false);

  assert.equal(start.source, "caller");
  assert.equal(end.source, "caller");
  assert.equal(pageNo.source, "system");
});

test("a text search box does not inherit a nearby enum list", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-page", kind: "ui", sessionId: "todo", at: "2026-09-04T05:00:00.000Z",
    pageUrl: PAGE, eventType: "snapshot",
    form: [
      { name: "name", label: "任务名称", type: "text", value: "OA办公" },
      { name: "category", label: "流程分类", type: "select", value: "OA办公", options: CATEGORY_OPTIONS }
    ]
  }, {
    id: "net-list", kind: "network", sessionId: "todo", at: "2026-09-04T05:00:01.000Z",
    pageUrl: PAGE,
    request: {
      method: "GET",
      url: "http://admin.example.test/admin-api/bpm/category/simple-list",
      resourceType: "xhr",
      headers: {},
      query: {}
    },
    response: {
      status: 200,
      headers: {},
      body: { data: CATEGORY_OPTIONS.map(item => ({ id: item.value, name: item.label })) }
    }
  }, {
    id: "net-todo", kind: "network", sessionId: "todo", at: "2026-09-04T05:00:02.000Z",
    pageUrl: PAGE, correlatedUiEvidenceId: "ui-page",
    request: {
      method: "GET",
      url: `${API}?name=OA%E5%8A%9E%E5%85%AC&category=118&pageNo=1&pageSize=10`,
      resourceType: "xhr",
      headers: {},
      query: { pageNo: "1", pageSize: "10", name: "OA办公", category: "118" }
    },
    response: { status: 200, headers: {}, body: { data: { list: [], total: 0 } } }
  }];
  const capability = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("todo-page"))!;
  const name = fieldByName(capability, "name")!;
  const category = fieldByName(capability, "category")!;
  assert.equal(name.source, "caller");
  assert.equal(name.widget, "text");
  assert.equal(name.candidates, undefined);
  assert.equal(category.source, "caller");
  assert.equal(category.widget, "select");
});

test("repeated query keys bind one array field to the ordered date controls", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-search", kind: "ui", sessionId: "range", at: "2026-09-04T05:00:00.000Z",
    pageUrl: PAGE, eventType: "click", text: "搜索", label: "搜索",
    form: [
      { name: "generated-range[0]", label: "开始时间", type: "date", value: "2026-09-05 00:00:00" },
      { name: "generated-range[1]", label: "结束时间", type: "date", value: "2026-09-06 23:59:59" }
    ]
  }, {
    id: "net-range", kind: "network", sessionId: "range", at: "2026-09-04T05:00:01.000Z",
    pageUrl: PAGE, correlatedUiEvidenceId: "ui-search",
    request: {
      method: "GET",
      url: `${API}?pageNo=1&pageSize=20&createTime=2026-09-05%2000%3A00%3A00&createTime=2026-09-06%2023%3A59%3A59`,
      resourceType: "xhr", headers: {},
      query: { pageNo: "1", pageSize: "20", createTime: ["2026-09-05 00:00:00", "2026-09-06 23:59:59"] }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];

  const capability = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("todo-page"))!;
  const range = fieldByName(capability, "createTime")!;
  assert.equal(range.valueType, "array");
  assert.equal(range.source, "caller");
  assert.equal(range.systemHandled, false);
  assert.equal(range.widget, "date");
  assert.equal(range.label, "开始时间 / 结束时间");
  assert.match(range.sourceDetail, /按页面顺序/);
  assert.match(range.sourceDetail, /00:00:00、23:59:59/);
  assert.deepEqual(range.dateClocks, ["00:00:00", "23:59:59"]);
  assert.equal(validateCapability(capability, events, [capability]).validation.status, "verified");
  const request = materializeHttpRequest(capability, { createTime: ["2026-10-01", "2026-10-03"] });
  assert.deepEqual(new URL(request.url).searchParams.getAll("createTime"), [
    "2026-10-01 00:00:00",
    "2026-10-03 23:59:59"
  ]);
});

test("a list query does not inherit create-dialog fields from the same page", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-list", kind: "ui", sessionId: "supply", at: "2026-09-05T16:47:50.000Z",
    pageUrl: "https://example.test/web/#/oa/supply/info", eventType: "snapshot", scope: "page",
    form: [
      { name: "name", label: "物品名称", type: "text", value: "笔" },
      { name: "code", label: "物品编码", type: "text", value: "" },
      { name: "managementType", label: "管理类型", type: "select", value: "消耗品" }
    ]
  }, {
    id: "net-query", kind: "network", sessionId: "supply", at: "2026-09-05T16:47:51.000Z",
    pageUrl: "https://example.test/web/#/oa/supply/info", correlatedUiEvidenceId: "ui-list",
    request: {
      method: "GET",
      url: "https://example.test/admin-api/oa/supply/page?pageNo=1&pageSize=20&name=%E7%AC%94&managementType=1&companyId=",
      resourceType: "xhr",
      headers: {},
      query: { pageNo: "1", pageSize: "20", name: "笔", managementType: "1", companyId: "" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }, {
    id: "ui-dialog", kind: "ui", sessionId: "supply", at: "2026-09-05T16:54:18.000Z",
    pageUrl: "https://example.test/web/#/oa/supply/info", eventType: "snapshot", scope: "dialog",
    form: [
      { name: "companyId", label: "*所属公司", type: "select", value: "宇擎源码" },
      { name: "name", label: "*物品名称", type: "text", value: "笔" },
      { name: "code", label: "物品编码", type: "text", value: "B01" },
      { name: "unitPrice", label: "参考单价", type: "text", value: "10" },
      { name: "stockQuantity", label: "库存数量", type: "text", value: "100" },
      { name: "remark", label: "备注", type: "textarea", value: "备注" }
    ]
  }, {
    id: "net-create", kind: "network", sessionId: "supply", at: "2026-09-05T16:54:23.000Z",
    pageUrl: "https://example.test/web/#/oa/supply/info", correlatedUiEvidenceId: "ui-dialog",
    request: {
      method: "POST",
      url: "https://example.test/admin-api/oa/supply/create",
      resourceType: "xhr",
      headers: {},
      body: { companyId: 100, name: "笔", code: "B01", unitPrice: 10, stockQuantity: 100, remark: "备注" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 36 } }
  }, {
    id: "net-refresh", kind: "network", sessionId: "supply", at: "2026-09-05T16:54:23.400Z",
    pageUrl: "https://example.test/web/#/oa/supply/info",
    request: {
      method: "GET",
      url: "https://example.test/admin-api/oa/supply/page?pageNo=1&pageSize=20&name=%E7%AC%94&managementType=1&companyId=",
      resourceType: "xhr",
      headers: {},
      query: { pageNo: "1", pageSize: "20", name: "笔", managementType: "1", companyId: "" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];
  const query = buildCapabilityCandidates(events).find(item => item.operation === "query")!;
  const create = buildCapabilityCandidates(events).find(item => item.operation === "create")!;
  assert.equal(fieldByName(query, "name")?.source, "caller");
  assert.equal(fieldByName(query, "unitPrice"), undefined, JSON.stringify(query.inputForm.map(field => field.name)));
  assert.equal(fieldByName(query, "stockQuantity"), undefined);
  assert.equal(fieldByName(query, "remark"), undefined);
  assert.equal(fieldByName(create, "unitPrice")?.source, "caller");
  assert.equal(fieldByName(create, "stockQuantity")?.source, "caller");
});

test("write-only process keys still freeze when they never appear on the page", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-create", kind: "ui", sessionId: "leave", at: "2026-09-04T05:00:00.000Z",
    pageUrl: "http://admin.example.test/oa/duty/leave",
    eventType: "submit", text: "提交",
    form: [
      { name: "reason", label: "原因", type: "text", value: "年假", required: true }
    ]
  }, {
    id: "net-create", kind: "network", sessionId: "leave", at: "2026-09-04T05:00:01.000Z",
    pageUrl: "http://admin.example.test/oa/duty/leave",
    correlatedUiEvidenceId: "ui-create",
    request: {
      method: "POST",
      url: "http://admin.example.test/admin-api/oa/duty-leave/submit-process",
      resourceType: "xhr",
      headers: {},
      query: {},
      body: { reason: "年假", billType: "oa_duty_leave", processDefKey: "oa_duty_leave" }
    },
    response: { status: 200, headers: {}, body: { success: true } }
  }];
  const capability = buildCapabilityCandidates(events)[0]!;
  assert.equal(fieldByName(capability, "reason")?.source, "caller");
  assert.equal(fieldByName(capability, "billType")?.defaultRule, "literal:oa_duty_leave");
  assert.equal(fieldByName(capability, "processDefKey")?.defaultRule, "literal:oa_duty_leave");
});
