import test from "node:test";
import assert from "node:assert/strict";
import type { CapabilityContract, EvidenceEvent, InputFormField } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import {
  applyDeterministicCatalogJudgment,
  applyExactEvidenceJoin,
  applySameResourceCandidates,
  fallbackRole
} from "../src/inference/pi-skill-runtime.js";
import { relatedLookupCapabilities } from "../src/inference/export-scope.js";

function field(partial: Partial<InputFormField> & Pick<InputFormField, "name">): InputFormField {
  return {
    path: partial.path || `$.${partial.name}`,
    label: partial.label || partial.name,
    valueType: partial.valueType || "string",
    source: partial.source || "system",
    required: partial.required ?? false,
    requiredBasis: partial.requiredBasis || "not-observed",
    systemHandled: partial.systemHandled ?? true,
    sourceDetail: partial.sourceDetail || "",
    widget: partial.widget || "text",
    ...partial
  };
}

function cap(partial: Partial<CapabilityContract> & Pick<CapabilityContract, "id" | "operation" | "transport">): CapabilityContract {
  return {
    kind: "atomic",
    title: partial.title || partial.id,
    description: partial.id,
    confidence: 1,
    inputSchema: { type: "object", properties: {} },
    outputSchema: { type: "object", properties: {} },
    inputForm: [],
    evidence: [],
    sideEffect: false,
    confirmation: { required: false },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "candidate", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-06T00:00:00.000Z" },
    ...partial
  };
}

test("exact name join marks the same-name page field as caller and keeps every request key", () => {
  const events: EvidenceEvent[] = [{
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/leave", eventType: "input",
    form: [
      { name: "reason", label: "请输入原因", type: "text", value: "事假说明" },
      { name: "remark", label: "备注", type: "textarea", value: "无关" }
    ]
  }, {
    id: "net", kind: "network", sessionId: "s", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/leave",
    request: {
      method: "POST", url: "https://x/oa/leave/create", resourceType: "xhr", headers: {}, query: {},
      body: { reason: "事假说明", token: "hidden-token", pageNo: 1 }
    },
    response: { status: 200, headers: {}, body: { success: true } }
  }];
  const clustered = buildCapabilityCandidates(events);
  const judged = applyDeterministicCatalogJudgment(clustered, events);
  const create = judged.find(item => item.transport.pathTemplate.endsWith("/leave/create"))!;
  const names = create.inputForm.map(item => item.name);
  assert.equal(names.includes("reason"), true);
  assert.equal(names.includes("token"), true);
  assert.equal(names.includes("pageNo"), true);
  assert.equal(create.inputForm.find(item => item.name === "reason")?.source, "caller");
  assert.equal(create.inputForm.find(item => item.name === "reason")?.label, "原因");
  assert.equal(create.inputForm.find(item => item.name === "token")?.source, "system");
  assert.equal(create.inputForm.find(item => item.name === "token")?.defaultRule, 'literal:"hidden-token"');
  assert.equal(create.inputForm.find(item => item.name === "pageNo")?.source, "system");
  assert.equal(create.inputForm.some(item => item.path === "$.invented"), false);
});

test("an unnamed seat count control stays caller-owned when its value equals pageNum", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-car-search", kind: "ui", sessionId: "car", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/car/car", eventType: "click", text: "搜索", label: "搜索",
    form: [
      { label: "座位数", type: "text", value: "1", required: false },
      { label: "状态", type: "select", value: "启用", required: false }
    ]
  }, {
    id: "net-car-list", kind: "network", sessionId: "car", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/car/car", correlatedUiEvidenceId: "ui-car-search",
    request: {
      method: "GET", url: "https://x/oa/car/list?pageNum=1&seatCount=1&status=0",
      resourceType: "xhr", headers: {}, query: { pageNum: "1", seatCount: "1", status: "0" }
    },
    response: { status: 200, headers: {}, body: { code: 200, rows: [] } }
  }];

  const judged = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const query = judged.find(item => item.transport.pathTemplate.endsWith("/oa/car/list"))!;
  const seatCount = query.inputForm.find(item => item.name === "seatCount");
  assert.equal(seatCount?.label, "座位数");
  assert.equal(seatCount?.source, "caller");
  assert.equal(seatCount?.systemHandled, false);
  assert.equal(seatCount?.defaultRule, undefined);
});

test("hotel room fields bind by compound semantics when every recorded value is 1", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-hotel-search", kind: "ui", sessionId: "hotel", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/hotelApply", eventType: "click", text: "搜索", label: "搜索",
    form: [
      { label: "房间类型", type: "select", value: "标准间", required: false },
      { label: "房间数量", type: "text", value: "1", required: false },
      { label: "房间等级", type: "select", value: "标准", required: false },
      { label: "入住人数", type: "text", value: "1", required: false },
      { label: "入住时间", type: "date", value: "2026-09-07", required: false }
    ]
  }, {
    id: "ui-hotel-date-input", kind: "ui", sessionId: "hotel", at: "2026-09-06T00:00:00.250Z",
    pageUrl: "https://x/oa/hotelApply", eventType: "input", label: "入住时间", inputType: "text", value: "2026-09-07",
    form: [{ label: "入住时间", type: "date", value: "2026-09-07", required: false }]
  }, {
    id: "ui-hotel-dropdown-click", kind: "ui", sessionId: "hotel", at: "2026-09-06T00:00:00.500Z",
    pageUrl: "https://x/oa/hotelApply", eventType: "click", label: "房间类型", inputType: "text", value: "标准间大床房",
    form: [
      { label: "房间类型", type: "select", value: "标准间", required: false },
      { label: "房间等级", type: "select", value: "标准", required: false }
    ]
  }, {
    id: "net-hotel-list", kind: "network", sessionId: "hotel", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/hotelApply", correlatedUiEvidenceId: "ui-hotel-search",
    request: {
      method: "GET", url: "https://x/oa/hotelApply/list?pageNum=1&roomType=1&roomCount=1&roomLevel=1&userCount=1&useTime=2026-09-07",
      resourceType: "xhr", headers: {}, query: { pageNum: "1", roomType: "1", roomCount: "1", roomLevel: "1", userCount: "1", useTime: "2026-09-07" }
    },
    response: { status: 200, headers: {}, body: { code: 200, rows: [] } }
  }];

  const judged = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const query = judged.find(item => item.transport.pathTemplate.endsWith("/oa/hotelApply/list"))!;
  const expected = new Map([
    ["roomType", "房间类型"],
    ["roomCount", "房间数量"],
    ["roomLevel", "房间等级"],
    ["userCount", "入住人数"]
  ]);
  for (const [name, label] of expected) {
    const input = query.inputForm.find(item => item.name === name);
    assert.equal(input?.label, label, JSON.stringify(query.inputForm));
    assert.equal(input?.source, "caller", JSON.stringify(query.inputForm));
    assert.equal(input?.systemHandled, false, JSON.stringify(query.inputForm));
    assert.equal(input?.defaultRule, undefined, JSON.stringify(query.inputForm));
    if (name === "roomType" || name === "roomLevel") assert.equal(input?.widget, "select", JSON.stringify(query.inputForm));
  }
  assert.equal(query.inputForm.find(item => item.name === "useTime")?.widget, "date", JSON.stringify(query.inputForm));
});

test("unnamed same-value selects use their displayed labels to choose the exact dictionary", () => {
  const events: EvidenceEvent[] = [{
    id: "room-types", kind: "network", sessionId: "hotel", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/hotelApply",
    request: { method: "GET", url: "https://x/system/dict/data/type/office_hotel_room_type", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { code: 200, data: [
      { dictType: "office_hotel_room_type", dictValue: "1", dictLabel: "标准间" },
      { dictType: "office_hotel_room_type", dictValue: "2", dictLabel: "大床房" }
    ] } }
  }, {
    id: "room-levels", kind: "network", sessionId: "hotel", at: "2026-09-06T00:00:00.100Z",
    pageUrl: "https://x/oa/hotelApply",
    request: { method: "GET", url: "https://x/system/dict/data/type/office_hotel_room_level", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { code: 200, data: [
      { dictType: "office_hotel_room_level", dictValue: "1", dictLabel: "标准" },
      { dictType: "office_hotel_room_level", dictValue: "2", dictLabel: "豪华" }
    ] } }
  }, {
    id: "hotel-before-selection", kind: "ui", sessionId: "hotel", at: "2026-09-06T00:00:00.500Z",
    pageUrl: "https://x/oa/hotelApply", eventType: "snapshot",
    form: [
      { label: "房间类型", type: "select", value: "标准间大床房" },
      { label: "房间等级", type: "select", value: "标准豪华" }
    ]
  }, {
    id: "hotel-search", kind: "ui", sessionId: "hotel", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/hotelApply", eventType: "click", text: "搜索", label: "搜索",
    form: [
      { label: "房间类型", type: "select", value: "标准间" },
      { label: "房间等级", type: "select", value: "标准" }
    ]
  }, {
    id: "hotel-list", kind: "network", sessionId: "hotel", at: "2026-09-06T00:00:01.100Z",
    pageUrl: "https://x/oa/hotelApply", correlatedUiEvidenceId: "hotel-search",
    request: {
      method: "GET", url: "https://x/oa/hotelApply/list?roomType=1&roomLevel=1",
      resourceType: "xhr", headers: {}, query: { roomType: "1", roomLevel: "1" }
    },
    response: { status: 200, headers: {}, body: { code: 200, rows: [] } }
  }];

  const catalog = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const query = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/hotelApply/list"))!;
  const roomType = query.inputForm.find(item => item.name === "roomType")?.candidates;
  const roomLevel = query.inputForm.find(item => item.name === "roomLevel")?.candidates;
  assert.equal(roomType?.type, "capability");
  assert.match(roomType?.type === "capability" ? roomType.capabilityId : "", /room-type/);
  assert.equal(roomLevel?.type, "capability");
  assert.match(roomLevel?.type === "capability" ? roomLevel.capabilityId : "", /room-level/);
});

test("leftover one-to-one does not bind an unrelated remark to a hidden token", () => {
  const capability = cap({
    id: "write",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/a", origin: "https://x", pathTemplate: "/a" },
    inputForm: [field({ name: "token", source: "system" })],
    evidence: [{ eventId: "ui", sessionId: "s", kind: "ui", at: "2026-09-06T00:00:00.000Z" }]
  });
  const events: EvidenceEvent[] = [{
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/a", eventType: "input",
    form: [{ name: "remark", label: "备注", type: "textarea", value: "说明文字" }]
  }];
  const joined = applyExactEvidenceJoin(capability, events);
  assert.notEqual(joined.inputForm[0]?.source, "caller");
  assert.notEqual(joined.inputForm[0]?.label, "备注");
});

test("a distinctive unique value binds exactly one field and leaves 0/1 alone", () => {
  const events: EvidenceEvent[] = [{
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/home", eventType: "change",
    form: [{ label: "请输入关键字", type: "search", value: "社会信用" }]
  }, {
    id: "net", kind: "network", sessionId: "s", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/home",
    request: {
      method: "POST", url: "https://x/search/getAllZy", resourceType: "xhr", headers: {}, query: {},
      body: { gjz: "社会信用", pageNo: 1, pageSize: 10, zylx: "0" }
    },
    response: { status: 200, headers: {}, body: { success: true, data: { list: [], total: 0 } } }
  }];
  const judged = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const search = judged.find(item => item.transport.pathTemplate.endsWith("/getAllZy"))!;
  assert.equal(search.inputForm.find(item => item.name === "gjz")?.source, "caller");
  assert.match(search.inputForm.find(item => item.name === "gjz")?.label || "", /关键字/);
  assert.equal(search.inputForm.find(item => item.name === "zylx")?.source, "system");
  assert.equal(search.inputForm.find(item => item.name === "zylx")?.defaultRule, 'literal:"0"');
  assert.equal(search.role, "primary");
});

test("exact list id joins a directory lookup and ignores a sibling business page", () => {
  const events: EvidenceEvent[] = [{
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/leave", eventType: "submit",
    form: [{ name: "assigneeId", label: "审批人", type: "picker", value: "张三" }]
  }, {
    id: "users", kind: "network", sessionId: "s", at: "2026-09-06T00:00:00.100Z",
    request: { method: "GET", url: "https://x/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [{ id: 174, nickname: "张三" }, { id: 1, nickname: "管理员" }] } } }
  }, {
    id: "docs", kind: "network", sessionId: "s", at: "2026-09-06T00:00:00.200Z",
    request: { method: "POST", url: "https://x/oa/doc/page", resourceType: "xhr", headers: {}, query: {}, body: { pageNo: 1 } },
    response: { status: 200, headers: {}, body: { data: { list: [{ id: 174, name: "制度A" }, { id: 9, name: "制度B" }] } } }
  }, {
    id: "net", kind: "network", sessionId: "s", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/leave", correlatedUiEvidenceId: "ui",
    request: {
      method: "POST", url: "https://x/oa/leave/create", resourceType: "xhr", headers: {}, query: {},
      body: { assigneeId: 174, reason: "事假" }
    },
    response: { status: 200, headers: {}, body: { success: true } }
  }];
  const judged = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const create = judged.find(item => item.transport.pathTemplate.endsWith("/leave/create"))!;
  const assignee = create.inputForm.find(item => item.name === "assigneeId");
  assert.equal(assignee?.source, "caller");
  assert.equal(assignee?.candidates?.type, "capability");
  const candidateId = assignee?.candidates?.type === "capability" ? assignee.candidates.capabilityId : "";
  assert.match(candidateId, /user/);
  assert.doesNotMatch(candidateId, /doc/);
});

test("a shared amount value never becomes an unrelated chooser id", () => {
  const events: EvidenceEvent[] = [{
    id: "companies", kind: "network", sessionId: "expense", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/expense",
    request: { method: "GET", url: "https://x/queryCompanyTenant", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { code: 200, data: [
      { id: 1, name: "示例公司" }, { id: 2, name: "另一公司" }
    ] } }
  }, {
    id: "expense-save", kind: "ui", sessionId: "expense", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/reimburseApply/form/add", eventType: "click", text: "保存", label: "保存",
    form: [{ label: "所属公司", type: "select", value: "示例公司" }]
  }, {
    id: "expense-create", kind: "network", sessionId: "expense", at: "2026-09-06T00:00:01.100Z",
    pageUrl: "https://x/oa/reimburseApply/form/add", correlatedUiEvidenceId: "expense-save",
    request: {
      method: "POST", url: "https://x/oa/reimburseApply", resourceType: "xhr", headers: {}, query: {},
      body: { totalAmt: "1.00", billAmt: 1 }
    },
    response: { status: 200, headers: {}, body: { code: 200 } }
  }];

  const catalog = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const create = catalog.find(item => item.operation === "create")!;
  for (const name of ["totalAmt", "billAmt"]) {
    const input = create.inputForm.find(item => item.name === name);
    assert.notEqual(input?.label, "所属公司", JSON.stringify(create.inputForm));
    assert.notEqual(input?.candidates?.type, "capability", JSON.stringify(create.inputForm));
  }
});

test("same-named header and detail fields bind to their exact visible controls", () => {
  const events: EvidenceEvent[] = [{
    id: "bill-types", kind: "network", sessionId: "expense", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/reimburseApply/form/add",
    request: {
      method: "GET", url: "https://x/system/dict/data/type/reimburse_bill_type",
      resourceType: "xhr", headers: {}, query: {}
    },
    response: { status: 200, headers: {}, body: { code: 200, data: [
      { dictType: "reimburse_bill_type", dictValue: "0", dictLabel: "车船票" },
      { dictType: "reimburse_bill_type", dictValue: "1", dictLabel: "出租车票" }
    ] } }
  }, {
    id: "expense-save", kind: "ui", sessionId: "expense", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/reimburseApply/form/add", eventType: "click", text: "保存", label: "保存",
    form: [
      { label: "票据总数", type: "number", value: "1", required: true },
      { label: "票据类型", type: "select", value: "车船票" },
      { label: "票据张数", type: "number", value: "1" },
      { label: "款项金额", type: "number", value: "1" }
    ]
  }, {
    id: "expense-create", kind: "network", sessionId: "expense", at: "2026-09-06T00:00:01.100Z",
    pageUrl: "https://x/oa/reimburseApply/form/add", correlatedUiEvidenceId: "expense-save",
    request: {
      method: "POST", url: "https://x/oa/reimburseApply", resourceType: "xhr", headers: {}, query: {},
      body: {
        totalAmt: "1.00", billAmt: 1, billType: "reimburse", billCount: "1",
        oaReimburseFeeitemList: [{ itemAmt: "1", billType: "0", billCount: "1" }]
      }
    },
    response: { status: 200, headers: {}, body: { code: 200, data: { id: "expense-1" } } }
  }];

  const catalog = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const create = catalog.find(item => item.operation === "create")!;
  const byPath = (path: string) => create.inputForm.find(item => item.path === path);
  assert.equal(byPath("$.totalAmt")?.source, "system");
  assert.equal(byPath("$.billAmt")?.source, "system");
  assert.equal(byPath("$.billType")?.source, "system");
  assert.deepEqual(
    [byPath("$.billCount")?.source, byPath("$.billCount")?.label, byPath("$.billCount")?.widget],
    ["caller", "票据总数", "number"]
  );
  assert.deepEqual(
    [byPath("$.oaReimburseFeeitemList[*].itemAmt")?.source, byPath("$.oaReimburseFeeitemList[*].itemAmt")?.label, byPath("$.oaReimburseFeeitemList[*].itemAmt")?.widget],
    ["caller", "款项金额", "number"]
  );
  assert.deepEqual(
    [byPath("$.oaReimburseFeeitemList[*].billCount")?.source, byPath("$.oaReimburseFeeitemList[*].billCount")?.label, byPath("$.oaReimburseFeeitemList[*].billCount")?.widget],
    ["caller", "票据张数", "number"]
  );
  const detailType = byPath("$.oaReimburseFeeitemList[*].billType");
  assert.deepEqual([detailType?.source, detailType?.label, detailType?.widget], ["caller", "票据类型", "select"]);
  assert.equal(detailType?.candidates?.type, "capability");
  assert.match(detailType?.candidates?.type === "capability" ? detailType.candidates.capabilityId : "", /reimburse-bill-type/);
});

test("same-resource candidates never leak from a detail caller field to a system field", () => {
  const transport = {
    method: "GET",
    urlTemplate: "https://x/oa/reimburseApply/list",
    origin: "https://x",
    pathTemplate: "/oa/reimburseApply/list"
  };
  const dynamic = {
    type: "capability" as const,
    capabilityId: "query-bill-types",
    valuePath: "$.data[*].value",
    labelPath: "$.data[*].label"
  };
  const query = cap({
    id: "query-reimburse", operation: "query", transport,
    inputForm: [
      field({ name: "billType", source: "system", defaultRule: 'literal:"reimburse"' }),
      field({ name: "status", source: "caller", systemHandled: false })
    ]
  });
  const create = cap({
    id: "create-reimburse", operation: "create",
    transport: { ...transport, method: "POST", pathTemplate: "/oa/reimburseApply", urlTemplate: "https://x/oa/reimburseApply" },
    inputForm: [
      field({
        name: "billType", path: "$.items[*].billType", source: "caller", systemHandled: false,
        label: "票据类型", widget: "select", candidates: dynamic
      }),
      field({ name: "status", source: "caller", systemHandled: false, widget: "select", candidates: dynamic })
    ]
  });

  const [resolved] = applySameResourceCandidates([query, create]);
  assert.equal(resolved?.inputForm.find(item => item.path === "$.billType")?.candidates, undefined);
  assert.deepEqual(resolved?.inputForm.find(item => item.path === "$.status")?.candidates, dynamic);
});

test("recorded dictionary and directory APIs supply work-report select candidates", () => {
  const events: EvidenceEvent[] = [{
    id: "dicts-unauthorized", kind: "network", sessionId: "s", at: "2026-09-05T23:59:59.000Z",
    pageUrl: "https://x/oa/work-report",
    request: { method: "GET", url: "https://x/system/dict-data/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { code: 401, msg: "账号未登录", data: null } }
  }, {
    id: "dicts", kind: "network", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/work-report",
    request: { method: "GET", url: "https://x/system/dict-data/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { code: 0, data: [
      { dictType: "unrelated", value: "1", label: "聊天" },
      { dictType: "unrelated", value: "2", label: "图像" },
      { dictType: "oa_work_report_type", value: "1", label: "日报" },
      { dictType: "oa_work_report_type", value: "2", label: "周报" },
      { dictType: "oa_work_report_type", value: "3", label: "月报" },
      { dictType: "bpm_process_instance_status", value: "-1", label: "未提交" },
      { dictType: "bpm_process_instance_status", value: "1", label: "审批中" }
    ] } }
  }, {
    id: "depts", kind: "network", sessionId: "s", at: "2026-09-06T00:00:00.100Z",
    pageUrl: "https://x/oa/work-report",
    request: { method: "GET", url: "https://x/system/dept/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { code: 0, data: [
      { id: 103, name: "研发部门" }, { id: 106, name: "财务部门" }
    ] } }
  }, {
    id: "dept-tree", kind: "network", sessionId: "s", at: "2026-09-06T00:00:00.200Z",
    pageUrl: "https://x/oa/work-report",
    request: { method: "GET", url: "https://x/system/dept/list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { code: 0, data: [
      { id: 103, name: "研发部门", parentId: 101 }, { id: 106, name: "财务部门", parentId: 101 }
    ] } }
  }, {
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/work-report", eventType: "submit",
    form: [
      { name: "reportType", label: "汇报类型", type: "select", value: "日报" },
      { name: "processStatus", label: "单据状态", type: "select", value: "未提交" },
      { name: "deptId", label: "申请部门", type: "select", value: "" }
    ]
  }, {
    id: "create", kind: "network", sessionId: "s", at: "2026-09-06T00:00:02.000Z",
    pageUrl: "https://x/oa/work-report", correlatedUiEvidenceId: "ui",
    request: {
      method: "POST", url: "https://x/oa/work-report/submit", resourceType: "xhr", headers: {}, query: {},
      body: { reportType: 1, processStatus: -1, deptId: 103 }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 1 } }
  }, {
    id: "statistics", kind: "network", sessionId: "s", at: "2026-09-06T00:00:03.000Z",
    pageUrl: "https://x/oa/work-report",
    request: {
      method: "GET", url: "https://x/oa/work-report/statistics?reportType=1&deptId=103",
      resourceType: "xhr", headers: {}, query: { reportType: "1", deptId: "103" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: [] } }
  }];
  const catalog = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const create = catalog.find(item => item.transport.pathTemplate.endsWith("/work-report/submit"))!;
  const statistics = catalog.find(item => item.transport.pathTemplate.endsWith("/work-report/statistics"))!;
  const storedStatistics = {
    ...statistics,
    inputForm: statistics.inputForm.map(item => item.name === "reportType"
      ? { ...item, source: "system" as const, systemHandled: true, widget: "text" as const, candidates: undefined, defaultRule: 'literal:"1"' }
      : { ...item, widget: "text" as const, candidates: undefined })
  };
  const related = relatedLookupCapabilities(catalog, [create]);
  const judged = applyDeterministicCatalogJudgment([create, storedStatistics, ...related], events);
  const result = judged.find(item => item.id === create.id)!;

  assert.equal(related.some(item => item.transport.pathTemplate.endsWith("/dict-data/simple-list")), true);
  assert.equal(related.some(item => item.transport.pathTemplate.endsWith("/dept/simple-list")), true);
  assert.equal(result.inputForm.find(item => item.name === "reportType")?.candidates?.type, "capability");
  assert.equal(result.inputForm.find(item => item.name === "processStatus")?.candidates?.type, "capability");
  const dept = result.inputForm.find(item => item.name === "deptId")?.candidates;
  assert.equal(dept?.type, "capability");
  assert.match(dept?.type === "capability" ? dept.capabilityId : "", /dept-simple-list/);
  const judgedStatistics = judged.find(item => item.id === statistics.id)!;
  assert.equal(judgedStatistics.inputForm.find(item => item.name === "reportType")?.candidates?.type, "capability");
  assert.equal(judgedStatistics.inputForm.find(item => item.name === "deptId")?.candidates?.type, "capability");
});

test("standard RuoYi dictValue and dictLabel rows resolve an unnamed select", () => {
  const events: EvidenceEvent[] = [{
    id: "statuses", kind: "network", sessionId: "seal", at: "2026-09-06T07:03:01.000Z",
    pageUrl: "http://boot.test/oa/seal/sealApply?billType=seal_apply",
    request: {
      method: "GET", url: "http://boot.test/prod-api/system/dict/data/type/oa_flow_billstatus",
      resourceType: "xhr", headers: {}, query: {}
    },
    response: { status: 200, headers: {}, body: { code: 200, data: [
      { dictType: "oa_flow_billstatus", dictValue: "0", dictLabel: "未提交" },
      { dictType: "oa_flow_billstatus", dictValue: "1", dictLabel: "审批中" },
      { dictType: "oa_flow_billstatus", dictValue: "2", dictLabel: "已完成" },
      { dictType: "oa_flow_billstatus", dictValue: "3", dictLabel: "被驳回" }
    ] } }
  }, {
    id: "search", kind: "ui", sessionId: "seal", at: "2026-09-06T07:03:49.081Z",
    pageUrl: "http://boot.test/oa/seal/sealApply?billType=seal_apply", eventType: "click",
    text: "搜索", label: "搜索",
    form: [{ label: "流程状态", type: "select", value: "未提交" }]
  }, {
    id: "list", kind: "network", sessionId: "seal", at: "2026-09-06T07:03:49.140Z",
    pageUrl: "http://boot.test/oa/seal/sealApply?billType=seal_apply", correlatedUiEvidenceId: "search",
    request: {
      method: "GET", url: "http://boot.test/prod-api/oa/sealApply/list?status=0",
      resourceType: "xhr", headers: {}, query: { status: "0" }
    },
    response: { status: 200, headers: {}, body: { code: 200, rows: [] } }
  }, {
    id: "tenant-bootstrap", kind: "network", sessionId: "seal", at: "2026-09-06T07:04:00.000Z",
    pageUrl: "http://boot.test/login",
    request: {
      method: "GET", url: "http://boot.test/prod-api/queryCompanyTenant?status=0",
      resourceType: "xhr", headers: {}, query: { status: "0" }
    },
    response: { status: 200, headers: {}, body: { code: 200, data: [{ id: 1, companyName: "示例企业" }] } }
  }];

  const catalog = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const query = catalog.find(item => item.transport.pathTemplate.endsWith("/oa/sealApply/list"))!;
  const status = query.inputForm.find(item => item.name === "status");
  assert.equal(status?.source, "caller");
  assert.equal(status?.label, "流程状态");
  assert.equal(status?.candidates?.type, "capability");
  assert.match(status?.candidates?.type === "capability" ? status.candidates.capabilityId : "", /type-oa-flow-billstatus/);
  const bootstrapStatus = catalog
    .find(item => item.transport.pathTemplate.endsWith("/queryCompanyTenant"))
    ?.inputForm.find(item => item.name === "status");
  assert.equal(bootstrapStatus?.source, "system");
  assert.equal(bootstrapStatus?.candidates, undefined);
});

test("fallback role keeps a write primary and marks companion queries as lookup", () => {
  const write = cap({
    id: "create",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/leave/create", origin: "https://x", pathTemplate: "/leave/create" },
    inputForm: [field({ name: "reason", source: "caller", systemHandled: false })]
  });
  const lookup = cap({
    id: "users",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/user/page", origin: "https://x", pathTemplate: "/user/page" },
    inputForm: [field({ name: "pageNo", source: "system" })]
  });
  assert.equal(fallbackRole(write, [write, lookup]), "primary");
  assert.equal(fallbackRole(lookup, [write, lookup]), "lookup");
});
