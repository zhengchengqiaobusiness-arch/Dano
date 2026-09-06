import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, UiEvidence } from "../src/domain.js";
import { materializeHttpRequest } from "../src/execution/http-executor.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { validateCapability } from "../src/validation/validator.js";

const origin = "https://oa.example.test";

function ui(id: string, second: number, text: string, form: UiEvidence["form"]): UiEvidence {
  return {
    id,
    kind: "ui",
    sessionId: "recorded-contract",
    at: `2026-09-06T08:40:${String(second).padStart(2, "0")}.000Z`,
    pageUrl: `${origin}/oa/apply`,
    eventType: "click",
    text,
    label: text,
    tag: "button",
    role: "button",
    form
  };
}

function network(
  id: string,
  second: number,
  method: string,
  path: string,
  query: Record<string, unknown>,
  body: Record<string, unknown> | undefined,
  responseBody: unknown,
  correlatedUiEvidenceId?: string
): EvidenceEvent {
  return {
    id,
    kind: "network",
    sessionId: "recorded-contract",
    at: `2026-09-06T08:40:${String(second).padStart(2, "0")}.100Z`,
    pageUrl: `${origin}/oa/apply`,
    correlatedUiEvidenceId,
    request: {
      method,
      url: `${origin}${path}`,
      resourceType: "xhr",
      headers: {},
      query,
      body
    },
    response: { status: 200, headers: {}, body: responseBody }
  };
}

test("unnamed successful form fields map to request leaves without equal-value lookup theft", () => {
  const form: UiEvidence["form"] = [
    { label: "报销缘由", type: "textarea", value: "差旅", required: false },
    { label: "开始时间", type: "date", value: "2026-09-07", required: true },
    { label: "结束时间", type: "date", value: "2026-09-09", required: true },
    { label: "月度", type: "date", value: "2026-09", required: true },
    { label: "票据总数", type: "number", value: "1", required: true },
    { label: "备注", type: "textarea", value: "备注", required: false },
    { label: "票据类型", type: "select", value: "车船票", required: false },
    { label: "票据张数", type: "number", value: "1", required: false },
    { label: "起始地", type: "text", value: "北京", required: false },
    { label: "目的地", type: "text", value: "上海", required: false },
    { label: "票据日期", type: "date", value: "2026-09-08", required: false },
    { label: "款项金额", type: "number", value: "1", required: false },
    { label: "住宿地点", type: "text", value: "上海", required: false }
  ];
  const body = {
    totalAmt: "1.00",
    billAmt: 1,
    subsidyAmt: 0,
    billType: "reimburse",
    startTime: "2026-09-07",
    des: "差旅",
    billCount: "1",
    remark: "备注",
    endTime: "2026-09-09",
    month: "2026-09",
    oaReimburseFeeitemList: [{
      remark: "",
      itemAmt: "1",
      year: "",
      month: "",
      billType: "0",
      billCount: "1",
      startCity: "北京",
      endCity: "上海",
      billDate: "2026-09-08",
      dayCount: "",
      standardSubsidy: "",
      itemType: "",
      stayCity: "上海",
      index: 1
    }]
  };
  const events: EvidenceEvent[] = [
    network("company-list", 1, "GET", "/prod-api/queryCompanyTenant", {}, undefined, {
      code: 200, data: [{ id: 1, name: "示例公司" }]
    }),
    network("bill-types", 2, "GET", "/prod-api/system/dict/data/type/reimburse_bill_type", {}, undefined, {
      code: 200,
      data: [
        { dictType: "reimburse_bill_type", dictValue: "0", dictLabel: "车船票" },
        { dictType: "reimburse_bill_type", dictValue: "1", dictLabel: "出租车票" }
      ]
    }),
    ui("save", 10, "保存", form),
    network("create", 10, "POST", "/prod-api/oa/reimburseApply", {}, body, { code: 200, data: { id: "created" } }, "save")
  ];
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const create = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/reimburseApply"))!;
  const byPath = new Map(create.inputForm.map(field => [field.path, field]));

  assert.equal(create.validation.status, "verified", JSON.stringify(create.validation.checks));
  assert.equal(byPath.get("$.totalAmt")?.source, "system");
  assert.equal(byPath.get("$.totalAmt")?.candidates, undefined);
  assert.notEqual(byPath.get("$.totalAmt")?.label, "所属公司");
  assert.deepEqual(
    ["$.startTime", "$.endTime", "$.billCount", "$.oaReimburseFeeitemList[*].billCount", "$.oaReimburseFeeitemList[*].itemAmt"]
      .map(path => [path, byPath.get(path)?.source]),
    [
      ["$.startTime", "caller"],
      ["$.endTime", "caller"],
      ["$.billCount", "caller"],
      ["$.oaReimburseFeeitemList[*].billCount", "caller"],
      ["$.oaReimburseFeeitemList[*].itemAmt", "caller"]
    ]
  );
  assert.equal(byPath.get("$.oaReimburseFeeitemList[*].billType")?.candidates?.type, "static");
  assert.equal(byPath.get("$.oaReimburseFeeitemList[*].startCity")?.widget, "text");
  assert.equal(byPath.get("$.oaReimburseFeeitemList[*].endCity")?.widget, "text");
  assert.equal(byPath.get("$.month")?.dateFormat, "YYYY-MM");

  const replay = materializeHttpRequest(create, {
    startTime: "2026-09-07",
    endTime: "2026-09-09",
    des: "差旅",
    billCount: "1",
    remark: "备注",
    month: "2026-09",
    oaReimburseFeeitemList: [{
      itemAmt: "1",
      billType: "车船票",
      billCount: "1",
      startCity: "北京",
      endCity: "上海",
      billDate: "2026-09-08",
      stayCity: "上海"
    }]
  });
  assert.deepEqual(replay.body, body);

  const frozen = {
    ...create,
    inputForm: create.inputForm.map(field => field.path === "$.startTime"
      ? { ...field, source: "system" as const, systemHandled: true, required: false, defaultRule: 'literal:"2026-09-07"' }
      : field)
  };
  const checked = validateCapability(frozen, events, catalog);
  assert.equal(checked.validation.checks.find(item => item.name === "editable-ui-fields-covered")?.ok, false);
});

test("query and create forms sharing one URL do not leak required flags or fields", () => {
  const queryForm: UiEvidence["form"] = [
    { label: "请假类型", type: "select", value: "婚假", required: false, options: [{ value: "marry", label: "婚假" }] },
    { label: "单据编号", type: "text", value: "Q-1", required: false },
    { label: "天数", type: "text", value: "1", required: false },
    { label: "流程状态", type: "select", value: "未提交", required: false, options: [{ value: "0", label: "未提交" }] }
  ];
  const createForm: UiEvidence["form"] = [
    { label: "请假类型", type: "select", value: "婚假", required: true, options: [{ value: "marry", label: "婚假" }] },
    { label: "开始时间", type: "date", value: "2026-10-07 00:00:00", required: false, rangeIndex: 0 },
    { label: "结束时间", type: "date", value: "2026-10-08 23:59:59", required: false, rangeIndex: 1 },
    { label: "天数", type: "text", value: "2", required: false },
    { label: "事由", type: "textarea", value: "婚假", required: false }
  ];
  const query = { pageNum: "1", pageSize: "20", billType: "duty_leave", leaveType: "marry", billCode: "Q-1", status: "0", days: "1" };
  const events: EvidenceEvent[] = [
    ui("search-1", 1, "搜索", queryForm),
    network("query-1", 1, "GET", "/prod-api/oa/dutyApply/list", query, undefined, { code: 200, rows: [] }, "search-1"),
    ui("save-duty", 2, "保存", createForm),
    network("create-duty", 2, "POST", "/prod-api/oa/dutyApply", {}, {
      days: 2,
      billType: "duty_leave",
      leaveType: "marry",
      reason: "婚假",
      startTime: "2026-10-07 00:00:00",
      endTime: "2026-10-08 23:59:59"
    }, { code: 200, data: { id: "created" } }, "save-duty"),
    ui("search-2", 3, "搜索", queryForm),
    network("query-2", 3, "GET", "/prod-api/oa/dutyApply/list", query, undefined, { code: 200, rows: [] }, "search-2")
  ];
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const queryCapability = catalog.find(item => item.transport.pathTemplate.endsWith("/dutyApply/list"))!;
  const createCapability = catalog.find(item => item.transport.pathTemplate.endsWith("/dutyApply"))!;

  assert.equal(queryCapability.validation.status, "verified", JSON.stringify(queryCapability.validation.checks));
  assert.deepEqual(
    ["leaveType", "billCode", "status", "days"].map(name => {
      const field = queryCapability.inputForm.find(item => item.name === name)!;
      return [name, field.source, field.required];
    }),
    [
      ["leaveType", "caller", false],
      ["billCode", "caller", false],
      ["status", "caller", false],
      ["days", "caller", false]
    ]
  );
  assert.deepEqual(
    ["$.startTime", "$.endTime"].map(path => [path, createCapability.inputForm.find(item => item.path === path)?.source]),
    [["$.startTime", "caller"], ["$.endTime", "caller"]]
  );
});

test("a rich-text page value keeps the recorded HTML request format", () => {
  const events: EvidenceEvent[] = [
    ui("save-rich", 1, "保存", [{ label: "使用描述", type: "textarea", value: "合同说明", required: false }]),
    network("create-rich", 1, "POST", "/prod-api/oa/sealApply", {}, { useInfo: "<p>合同说明</p>" }, { code: 200, data: { id: "created" } }, "save-rich")
  ];
  const create = finalizeCapabilities(buildCapabilityCandidates(events), events)
    .find(item => item.transport.pathTemplate.endsWith("/oa/sealApply"))!;
  const field = create.inputForm.find(item => item.name === "useInfo")!;
  assert.equal(field.source, "caller");
  assert.equal(field.richText, true);
  assert.equal(materializeHttpRequest(create, { useInfo: "新的说明" }).body?.useInfo, "<p>新的说明</p>");
  assert.equal(materializeHttpRequest(create, { useInfo: "<p>已有格式</p>" }).body?.useInfo, "<p>已有格式</p>");
});
