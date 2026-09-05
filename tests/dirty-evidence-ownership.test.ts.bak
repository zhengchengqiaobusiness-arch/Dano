import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, InputFormField } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { materializeHttpRequest } from "../src/execution/http-executor.js";
import { collectUiObservations } from "../src/inference/field-resolver.js";

const PAGE = "https://example.test/oa/ticket";
const QUERY_URL = "https://example.test/admin-api/oa/ticket/page";
const CREATE_URL = "https://example.test/admin-api/oa/ticket/submit-process";

const STATUS_OPTIONS = [
  { value: "0", label: "未提交" },
  { value: "1", label: "审批中" },
  { value: "2", label: "已通过" },
  { value: "3", label: "已驳回" },
  { value: "4", label: "已取消" }
];

const START_MS = 1_789_401_600_000;
const END_MIDNIGHT_MS = 1_789_920_000_000;
const END_EOD_MS = 1_790_006_399_000;

function fieldByName(capability: { inputForm: InputFormField[] }, name: string) {
  return capability.inputForm.find(field => field.name === name);
}

function queryForm() {
  return [
    { name: "type", label: "单据类型", type: "select", value: "年假", options: [
      { value: "1", label: "病假" }, { value: "2", label: "事假" }, { value: "3", label: "年假" }
    ] },
    { name: "createTime[0]", label: "开始日期", type: "date", value: "2026-09-15", rangeIndex: 0 },
    { name: "createTime[1]", label: "结束日期", type: "date", value: "2026-09-17", rangeIndex: 1 },
    { name: "processStatus", label: "审批结果", type: "select", value: "未提交", options: STATUS_OPTIONS },
    { name: "reason", label: "原因", type: "text", value: "1" }
  ];
}

test("a text filter does not inherit a leftover dropdown list from another control", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-page", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:00:00.000Z",
    pageUrl: PAGE, eventType: "snapshot", form: queryForm()
  }, {
    id: "ui-reason", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:00:01.000Z",
    pageUrl: PAGE, eventType: "change", label: "原因", name: "reason", inputType: "text", value: "1",
    role: undefined, tag: "input",
    options: STATUS_OPTIONS,
    visibleOptions: STATUS_OPTIONS.map(item => item.label),
    form: queryForm()
  }, {
    id: "ui-search", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:00:02.000Z",
    pageUrl: PAGE, eventType: "click", text: "搜索", label: "搜索", form: queryForm()
  }, {
    id: "net-query", kind: "network", sessionId: "dirty", at: "2026-09-04T05:00:03.000Z",
    pageUrl: PAGE, correlatedUiEvidenceId: "ui-search",
    request: {
      method: "GET",
      url: `${QUERY_URL}?pageNo=1&pageSize=10&type=3&processStatus=0&reason=1&createTime%5B0%5D=2026-09-15%2000%3A00%3A00&createTime%5B1%5D=2026-09-17%2023%3A59%3A59`,
      resourceType: "xhr",
      headers: {},
      query: {
        pageNo: "1",
        pageSize: "10",
        type: "3",
        processStatus: "0",
        reason: "1",
        "createTime[0]": "2026-09-15 00:00:00",
        "createTime[1]": "2026-09-17 23:59:59"
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];

  const leaked = collectUiObservations(events.filter((item): item is Extract<EvidenceEvent, { kind: "ui" }> => item.kind === "ui"));
  assert.equal(
    leaked.some(item => item.label === "原因" && item.type === "select"),
    false,
    "reason click must not become a select just because a leftover dropdown is visible"
  );

  const query = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("/ticket/page"))!;
  const reason = fieldByName(query, "reason")!;
  const status = fieldByName(query, "processStatus")!;
  assert.equal(reason.source, "caller");
  assert.equal(reason.label, "原因");
  assert.equal(reason.widget, "text");
  assert.equal(reason.candidates, undefined);
  assert.equal(status.source, "caller");
  assert.equal(status.label, "审批结果");
  assert.equal(status.widget, "select");
  assert.deepEqual(
    status.candidates?.type === "static" ? status.candidates.values.map(item => item.label) : [],
    STATUS_OPTIONS.map(item => item.label)
  );
  assert.equal(fieldByName(query, "createTime[0]")?.dateClock, "00:00:00");
  assert.equal(fieldByName(query, "createTime[1]")?.dateClock, "23:59:59");
});

test("shared group headings keep distinct caller labels for each sibling field", () => {
  const form = [
    { name: "projectCode", label: "所属项目", type: "text", value: "P-1" },
    { name: "projectName", label: "所属项目", type: "text", value: "项目甲" },
    { label: "请输入项目编码", type: "text", value: "P-1" },
    { label: "请输入项目名称", type: "text", value: "项目甲" }
  ];
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:01:00.000Z",
    pageUrl: `${PAGE}/create`, eventType: "snapshot", form
  }, {
    id: "ui-submit", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:01:01.000Z",
    pageUrl: `${PAGE}/create`, eventType: "click", text: "提交", form
  }, {
    id: "net-create", kind: "network", sessionId: "dirty", at: "2026-09-04T05:01:02.000Z",
    pageUrl: `${PAGE}/create`, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST",
      url: CREATE_URL,
      resourceType: "xhr",
      headers: {},
      query: {},
      body: { projectCode: "P-1", projectName: "项目甲", billType: "oa_ticket" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 1 } }
  }];
  const create = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.equal(fieldByName(create, "projectCode")?.label, "项目编码");
  assert.equal(fieldByName(create, "projectName")?.label, "项目名称");
});

test("two fields that only share a group heading still split by their request names", () => {
  const form = [
    { name: "projectCode", label: "所属项目", type: "text", value: "P-1" },
    { name: "projectName", label: "所属项目", type: "text", value: "项目甲" }
  ];
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:02:00.000Z",
    pageUrl: `${PAGE}/create`, eventType: "snapshot", form
  }, {
    id: "ui-submit", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:02:01.000Z",
    pageUrl: `${PAGE}/create`, eventType: "click", text: "提交", form
  }, {
    id: "net-create", kind: "network", sessionId: "dirty", at: "2026-09-04T05:02:02.000Z",
    pageUrl: `${PAGE}/create`, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST",
      url: CREATE_URL,
      resourceType: "xhr",
      headers: {},
      query: {},
      body: { projectCode: "P-1", projectName: "项目甲" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 1 } }
  }];
  const create = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.equal(fieldByName(create, "projectCode")?.label, "项目编码");
  assert.equal(fieldByName(create, "projectName")?.label, "项目名称");
});

test("epoch dates keep the recorded clock instead of collapsing to midnight", () => {
  const form = [
    { name: "startTime", label: "开始时间", type: "date", value: "2026-09-15", required: true },
    { name: "endTime", label: "结束时间", type: "date", value: "2026-09-21", required: true },
    { name: "day", label: "天数", type: "number", value: 6, required: true }
  ];
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:03:00.000Z",
    pageUrl: `${PAGE}/create`, eventType: "snapshot", form
  }, {
    id: "ui-submit", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:03:01.000Z",
    pageUrl: `${PAGE}/create`, eventType: "click", text: "提交", form
  }, {
    id: "net-create", kind: "network", sessionId: "dirty", at: "2026-09-04T05:03:02.000Z",
    pageUrl: `${PAGE}/create`, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST",
      url: CREATE_URL,
      resourceType: "xhr",
      headers: {},
      query: {},
      body: {
        startTime: START_MS,
        endTime: END_EOD_MS,
        day: 7
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 1 } }
  }];
  const create = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.equal(fieldByName(create, "startTime")?.dateClock, "00:00:00");
  assert.equal(fieldByName(create, "endTime")?.dateClock, "23:59:59");
  const request = materializeHttpRequest(create, {
    startTime: "2026-09-15",
    endTime: "2026-09-21",
    day: 7
  });
  assert.equal(request.body?.startTime, START_MS);
  assert.equal(request.body?.endTime, END_EOD_MS);
});

test("duration formulas stay inside the same start/end family", () => {
  const form = [
    { name: "startTime", label: "开始时间", type: "date", value: "2026-09-15" },
    { name: "endTime", label: "结束时间", type: "date", value: "2026-09-21" },
    { name: "day", label: "天数", type: "number", value: 1 },
    { name: "actualStartTime", label: "实际开始", type: "date", value: "2026-09-22" },
    { name: "actualEndTime", label: "实际结束", type: "date", value: "2026-10-01" },
    { name: "actualDay", label: "实际天数", type: "number", value: 9 }
  ];
  const actualStart = END_MIDNIGHT_MS + 86_400_000;
  const actualEnd = actualStart + 9 * 86_400_000;
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:04:00.000Z",
    pageUrl: `${PAGE}/create`, eventType: "snapshot", form
  }, {
    id: "ui-submit", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:04:01.000Z",
    pageUrl: `${PAGE}/create`, eventType: "click", text: "提交", form
  }, {
    id: "net-create", kind: "network", sessionId: "dirty", at: "2026-09-04T05:04:02.000Z",
    pageUrl: `${PAGE}/create`, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST",
      url: CREATE_URL,
      resourceType: "xhr",
      headers: {},
      query: {},
      body: {
        startTime: START_MS,
        endTime: END_MIDNIGHT_MS,
        day: 1,
        actualStartTime: actualStart,
        actualEndTime: actualEnd,
        actualDay: 9
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 1 } }
  }];
  const create = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const day = fieldByName(create, "day")!;
  const actualDay = fieldByName(create, "actualDay")!;
  assert.doesNotMatch(day.defaultRule || "", /actualStartTime|actualEndTime|endTime/);
  assert.notEqual(day.defaultRule, "computed:(actualStartTime - endTime) / 86400000");
  assert.equal(actualDay.defaultRule, "computed:(actualEndTime - actualStartTime) / 86400000");
});

test("when two durations match, day uses the non-actual pair", () => {
  const form = [
    { name: "startTime", label: "开始时间", type: "date", value: "2026-09-15" },
    { name: "endTime", label: "结束时间", type: "date", value: "2026-09-21" },
    { name: "day", label: "天数", type: "number", value: 6 },
    { name: "actualStartTime", label: "实际开始", type: "date", value: "2026-09-15" },
    { name: "actualEndTime", label: "实际结束", type: "date", value: "2026-09-21" },
    { name: "actualDay", label: "实际天数", type: "number", value: 6 }
  ];
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:05:00.000Z",
    pageUrl: `${PAGE}/create`, eventType: "snapshot", form
  }, {
    id: "ui-submit", kind: "ui", sessionId: "dirty", at: "2026-09-04T05:05:01.000Z",
    pageUrl: `${PAGE}/create`, eventType: "click", text: "提交", form
  }, {
    id: "net-create", kind: "network", sessionId: "dirty", at: "2026-09-04T05:05:02.000Z",
    pageUrl: `${PAGE}/create`, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST",
      url: CREATE_URL,
      resourceType: "xhr",
      headers: {},
      query: {},
      body: {
        startTime: START_MS,
        endTime: END_MIDNIGHT_MS,
        day: 6,
        actualStartTime: START_MS,
        actualEndTime: END_MIDNIGHT_MS,
        actualDay: 6
      }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 1 } }
  }];
  const create = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.equal(fieldByName(create, "day")?.defaultRule, "computed:(endTime - startTime) / 86400000");
  assert.equal(fieldByName(create, "actualDay")?.defaultRule, "computed:(actualEndTime - actualStartTime) / 86400000");
});
