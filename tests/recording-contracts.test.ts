import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent, UiEvidence } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { materializeHttpRequest } from "../src/execution/http-executor.js";

const origin = "https://oa.example.test";

function ui(id: string, second: number, text: string, form: UiEvidence["form"]): UiEvidence {
  return {
    id,
    kind: "ui",
    sessionId: "live-contract",
    at: `2026-09-06T08:40:${String(second).padStart(2, "0")}.000Z`,
    pageUrl: `${origin}/oa/duty/apply`,
    eventType: "click",
    text,
    label: text,
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
  correlatedUiEvidenceId: string
): EvidenceEvent {
  return {
    id,
    kind: "network",
    sessionId: "live-contract",
    at: `2026-09-06T08:40:${String(second).padStart(2, "0")}.100Z`,
    pageUrl: `${origin}/oa/duty/apply`,
    correlatedUiEvidenceId,
    request: {
      method,
      url: `${origin}${path}`,
      resourceType: "xhr",
      headers: {},
      query,
      body
    },
    response: { status: 200, headers: {}, body: { code: 200, rows: [] } }
  };
}

test("live contract review keeps query and create controls on their owning forms", () => {
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
    network("query-1", 1, "GET", "/prod-api/oa/dutyApply/list", query, undefined, "search-1"),
    ui("save-duty", 2, "保存", createForm),
    network("create-duty", 2, "POST", "/prod-api/oa/dutyApply", {}, {
      days: 2,
      billType: "duty_leave",
      leaveType: "marry",
      reason: "婚假",
      startTime: "2026-10-07 00:00:00",
      endTime: "2026-10-08 23:59:59"
    }, "save-duty"),
    ui("search-2", 3, "搜索", queryForm),
    network("query-2", 3, "GET", "/prod-api/oa/dutyApply/list", query, undefined, "search-2")
  ];

  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const queryCapability = catalog.find(item => item.transport.pathTemplate.endsWith("/dutyApply/list"))!;
  const createCapability = catalog.find(item => item.transport.pathTemplate.endsWith("/dutyApply"))!;

  assert.equal(queryCapability.validation.status, "verified", JSON.stringify({ fields: queryCapability.inputForm, checks: queryCapability.validation.checks }));
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
    ],
    JSON.stringify(queryCapability.inputForm)
  );
  assert.deepEqual(
    ["$.startTime", "$.endTime"].map(path => [path, createCapability.inputForm.find(item => item.path === path)?.source]),
    [["$.startTime", "caller"], ["$.endTime", "caller"]]
  );
});

test("live contract records rich-text encoding, real controls, and month precision", () => {
  const events: EvidenceEvent[] = [
    ui("save-formatted", 10, "保存", [
      { label: "使用描述", type: "textarea", value: "合同说明", required: false },
      { label: "统计月份", type: "date", value: "2026-09", required: false }
    ]),
    network(
      "create-formatted",
      10,
      "POST",
      "/prod-api/oa/sealApply",
      {},
      { useInfo: "<p>合同说明</p>", month: "2026-09" },
      "save-formatted"
    )
  ];
  const create = finalizeCapabilities(buildCapabilityCandidates(events), events)
    .find(item => item.transport.pathTemplate.endsWith("/oa/sealApply"))!;
  const useInfo = create.inputForm.find(item => item.name === "useInfo")!;
  const month = create.inputForm.find(item => item.name === "month")!;

  assert.equal(useInfo.source, "caller");
  assert.equal(useInfo.widget, "textarea");
  assert.equal(useInfo.requestFormat, "html");
  assert.equal(month.source, "caller");
  assert.equal(month.widget, "date");
  assert.equal(month.dateFormat, "YYYY-MM");
  const replay = materializeHttpRequest(create, { useInfo: "新的说明", month: "2026-10" });
  assert.deepEqual(replay.body, { useInfo: "<p>新的说明</p>", month: "2026-10" });
});
