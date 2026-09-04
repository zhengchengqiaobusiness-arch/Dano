import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, InputFormField } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";

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
