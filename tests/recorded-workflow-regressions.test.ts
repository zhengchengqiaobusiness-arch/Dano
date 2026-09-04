import test from "node:test";
import assert from "node:assert/strict";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { reanalyzeIncoming } from "../src/inference/reanalyze.js";

function network(partial: Extract<EvidenceEvent, { kind: "network" }>): EvidenceEvent {
  return partial;
}

test("RuoYi dictionary values map unnamed select labels without mixing another page's form", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-query", kind: "ui", sessionId: "s", at: "2026-09-04T00:00:00.000Z",
    pageUrl: "https://example.test/apply/list", eventType: "click", text: "搜索", label: "搜索", tag: "button",
    form: [
      { label: "流程状态", type: "select", value: "未提交" },
      { label: "房间类型", type: "select", value: "标准间" },
      { label: "房间等级", type: "select", value: "标准" }
    ]
  }, {
    id: "ui-custom-select-shell", kind: "ui", sessionId: "s", at: "2026-09-04T00:00:00.500Z",
    pageUrl: "https://example.test/apply/list", eventType: "click", label: "房间类型", inputType: "text", value: "",
    form: [
      { label: "流程状态", type: "select", value: "未提交" },
      { label: "房间类型", type: "select", value: "标准间" },
      { label: "房间等级", type: "select", value: "标准" }
    ]
  }, {
    id: "ui-create", kind: "ui", sessionId: "s", at: "2026-09-04T00:00:02.000Z",
    pageUrl: "https://example.test/apply/form/add", eventType: "click", text: "保存", label: "保存", tag: "button",
    form: [
      { label: "选择公章", type: "select", value: "合同章", required: true },
      { label: "使用时间", type: "date", value: "2026-09-04", required: true }
    ]
  }, network({
    id: "net-status-dict", kind: "network", sessionId: "s", at: "2026-09-03T23:59:59.100Z",
    pageUrl: "https://example.test/apply/list",
    request: { method: "GET", url: "https://example.test/api/dict/status", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: [
      { dictType: "flow_status", dictValue: "0", dictLabel: "未提交" },
      { dictType: "flow_status", dictValue: "1", dictLabel: "审批中" }
    ] } }
  }), network({
    id: "net-room-type-dict", kind: "network", sessionId: "s", at: "2026-09-03T23:59:59.200Z",
    pageUrl: "https://example.test/apply/list",
    request: { method: "GET", url: "https://example.test/api/dict/room-type", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: [
      { dictType: "room_type", dictValue: "1", dictLabel: "标准间" },
      { dictType: "room_type", dictValue: "2", dictLabel: "大床房" }
    ] } }
  }), network({
    id: "net-room-level-dict", kind: "network", sessionId: "s", at: "2026-09-03T23:59:59.300Z",
    pageUrl: "https://example.test/apply/list",
    request: { method: "GET", url: "https://example.test/api/dict/room-level", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: [
      { dictType: "room_level", dictValue: "1", dictLabel: "标准" },
      { dictType: "room_level", dictValue: "2", dictLabel: "豪华" }
    ] } }
  }), network({
    id: "net-query", kind: "network", sessionId: "s", at: "2026-09-04T00:00:01.000Z",
    pageUrl: "https://example.test/apply/list", correlatedUiEvidenceId: "ui-query",
    request: {
      method: "GET", url: "https://example.test/api/hotel/list?status=0&roomType=1&roomLevel=1&pageNum=1",
      resourceType: "xhr", headers: {}, query: { status: "0", roomType: "1", roomLevel: "1", pageNum: "1" }
    },
    response: { status: 200, headers: {}, body: { rows: [], total: 0 } }
  }), network({
    id: "net-seals", kind: "network", sessionId: "s", at: "2026-09-04T00:00:02.200Z",
    pageUrl: "https://example.test/apply/form/add",
    request: { method: "GET", url: "https://example.test/api/seal/listAll", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: [{ id: "seal-1", name: "合同章" }, { id: "seal-2", name: "财务章" }] } }
  }), network({
    id: "net-create", kind: "network", sessionId: "s", at: "2026-09-04T00:00:03.000Z",
    pageUrl: "https://example.test/apply/form/add", correlatedUiEvidenceId: "ui-create",
    request: {
      method: "POST", url: "https://example.test/api/seal/apply", resourceType: "xhr", headers: {}, query: {},
      body: { sealId: "seal-1", useTime: "2026-09-04" }
    },
    response: { status: 200, headers: {}, body: { data: { id: "created-1" } } }
  })];

  const capabilities = buildCapabilityCandidates(events);
  const query = capabilities.find(item => item.transport.pathTemplate === "/api/hotel/list")!;
  for (const [name, label] of [["status", "流程状态"], ["roomType", "房间类型"], ["roomLevel", "房间等级"]]) {
    const field = query.inputForm.find(item => item.name === name)!;
    assert.equal(field.source, "caller", `${name} must remain caller input`);
    assert.equal(field.label, label);
    assert.equal(field.widget, "select");
    assert.equal(field.defaultRule, undefined);
  }
  const create = capabilities.find(item => item.transport.pathTemplate === "/api/seal/apply")!;
  const seal = create.inputForm.find(item => item.name === "sealId")!;
  assert.equal(seal.label, "选择公章");
  assert.equal(seal.required, true);
  assert.equal(create.inputForm.some(item => item.label === "流程状态"), false);
});

test("page-load helpers and future list responses cannot become write field sources", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-add", kind: "ui", sessionId: "s", at: "2026-09-04T01:00:00.000Z",
    pageUrl: "https://example.test/hotel/form/add", eventType: "click", text: "新增", label: "新增", tag: "button"
  }, {
    id: "ui-save", kind: "ui", sessionId: "s", at: "2026-09-04T01:00:01.000Z",
    pageUrl: "https://example.test/hotel/form/add", eventType: "click", text: "保存", label: "保存", tag: "button",
    form: [
      { label: "房间类型", type: "select", value: "标准间", required: true },
      { label: "入住人数", type: "number", value: "1", required: true }
    ]
  }, network({
    id: "net-attachment-count", kind: "network", sessionId: "s", at: "2026-09-04T01:00:00.200Z",
    pageUrl: "https://example.test/hotel/form/add", correlatedUiEvidenceId: "ui-add",
    request: { method: "GET", url: "https://example.test/api/attachment/count?billType=hotel_apply", resourceType: "xhr", headers: {}, query: { billType: "hotel_apply" } },
    response: { status: 200, headers: {}, body: { data: 0 } }
  }), network({
    id: "net-flow-list", kind: "network", sessionId: "s", at: "2026-09-04T01:00:00.300Z",
    pageUrl: "https://example.test/hotel/form/add", correlatedUiEvidenceId: "ui-add",
    request: { method: "GET", url: "https://example.test/api/flow/list?billType=hotel_apply", resourceType: "xhr", headers: {}, query: { billType: "hotel_apply" } },
    response: { status: 200, headers: {}, body: { total: 0, rows: [] } }
  }), network({
    id: "net-create", kind: "network", sessionId: "s", at: "2026-09-04T01:00:02.000Z",
    pageUrl: "https://example.test/hotel/form/add", correlatedUiEvidenceId: "ui-save",
    request: {
      method: "POST", url: "https://example.test/api/hotel/apply", resourceType: "xhr", headers: {}, query: {},
      body: { billType: "hotel_apply", roomType: "1", userCount: "1", totalAmt: 0, feeItems: [] }
    },
    response: { status: 200, headers: {}, body: { data: { id: "created-1" } } }
  }), network({
    id: "net-future-list", kind: "network", sessionId: "s", at: "2026-09-04T01:00:03.000Z",
    pageUrl: "https://example.test/hotel/list",
    request: { method: "GET", url: "https://example.test/api/hotel/list?billType=hotel_apply", resourceType: "xhr", headers: {}, query: { billType: "hotel_apply" } },
    response: { status: 200, headers: {}, body: { total: 1, rows: [{ id: "created-1", billType: "hotel_apply" }] } }
  })];

  const create = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate === "/api/hotel/apply")!;
  const byName = (name: string) => create.inputForm.find(item => item.name === name)!;
  assert.equal(byName("billType").defaultRule, "literal:hotel_apply");
  assert.equal(byName("userCount").source, "caller");
  assert.equal(byName("userCount").defaultRule, undefined);
  assert.equal(byName("roomType").defaultRule, undefined);
  assert.equal(byName("totalAmt").defaultRule, "literal:0");
  assert.equal(byName("feeItems").defaultRule, "literal:[]");
  assert.deepEqual(create.bindings, []);
});

test("a field-triggered lookup disambiguates equal zero values by response semantics", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-leave-type", kind: "ui", sessionId: "s", at: "2026-09-04T02:00:00.000Z",
    pageUrl: "https://example.test/leave/create", eventType: "click", tag: "li", role: "option", text: "病假",
    form: [
      { name: "type", label: "请假类型", type: "select", value: "", required: true },
      { name: "day", label: "请假天数", type: "number", value: "1", required: true }
    ]
  }, network({
    id: "net-balance", kind: "network", sessionId: "s", at: "2026-09-04T02:00:00.100Z",
    pageUrl: "https://example.test/leave/create", correlatedUiEvidenceId: "ui-leave-type",
    request: { method: "GET", url: "https://example.test/api/leave-balance/my?leaveType=1", resourceType: "xhr", headers: {}, query: { leaveType: "1" } },
    response: { status: 200, headers: {}, body: { data: { leaveType: "1", totalDays: 0, remainingDays: 0 } } }
  }), network({
    id: "net-init", kind: "network", sessionId: "s", at: "2026-09-04T02:00:00.200Z",
    pageUrl: "https://example.test/leave/create",
    request: { method: "GET", url: "https://example.test/api/flow/list?billType=leave", resourceType: "xhr", headers: {}, query: { billType: "leave" } },
    response: { status: 200, headers: {}, body: { total: 0, rows: [] } }
  }), network({
    id: "net-create", kind: "network", sessionId: "s", at: "2026-09-04T02:00:01.000Z",
    pageUrl: "https://example.test/leave/create",
    request: {
      method: "POST", url: "https://example.test/api/duty-leave/submit-process", resourceType: "xhr", headers: {}, query: {},
      body: { type: 1, day: 1, leaveBalance: 0, billType: "leave" }
    },
    response: { status: 200, headers: {}, body: { data: 1 } }
  })];

  const create = buildCapabilityCandidates(events).find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const balance = create.inputForm.find(item => item.name === "leaveBalance")!;
  assert.equal(
    balance.defaultRule,
    "from:query-get-leave-balance-my:$.data.remainingDays|via:type",
    JSON.stringify({ balance, capabilities: create.evidence })
  );
  assert.equal(create.bindings.length, 1);
  assert.equal(create.bindings[0]!.fromPath, "$.data.remainingDays");
});

function capability(partial: Partial<CapabilityContract> & Pick<CapabilityContract, "id" | "operation" | "transport">): CapabilityContract {
  return {
    kind: "atomic", title: partial.id, description: "generated", confidence: 1,
    inputSchema: { type: "object", properties: { billType: { type: "string" } } },
    outputSchema: { type: "object", properties: {} }, inputForm: [], evidence: [], sideEffect: false,
    confirmation: { required: false }, completion: { acceptedHttpStatuses: [200] }, bindings: [],
    validation: { version: 2, status: "candidate", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-04T00:00:00.000Z" },
    editing: { title: "generated", description: "generated", operation: "generated", fields: "generated" },
    ...partial
  };
}

test("reanalyzing current evidence replaces stale generated lookup context and stale operation id", () => {
  const staleLookup = capability({
    id: "query-get-flow-list", operation: "query",
    transport: { method: "GET", origin: "https://example.test", pathTemplate: "/api/flow/list", urlTemplate: "https://example.test/api/flow/list?billType={billType}" },
    inputForm: [{
      path: "$.billType", name: "billType", label: "billType", valueType: "string", source: "fixed", required: false,
      requiredBasis: "not-observed", systemHandled: true, widget: "text", defaultRule: "literal:seal_apply", sourceDetail: "old generated context"
    }],
    validation: { version: 2, status: "verified", checks: [] }
  });
  const freshLookup = capability({
    id: "query-get-flow-list", operation: "query",
    transport: staleLookup.transport,
    inputForm: [{
      path: "$.billType", name: "billType", label: "billType", valueType: "string", source: "fixed", required: false,
      requiredBasis: "not-observed", systemHandled: true, widget: "text", defaultRule: "literal:reimburse", sourceDetail: "current evidence context"
    }]
  });
  const staleWrite = capability({
    id: "query-post-seal-apply", operation: "create", sideEffect: true,
    transport: { method: "POST", origin: "https://example.test", pathTemplate: "/api/seal/apply", urlTemplate: "https://example.test/api/seal/apply" },
    validation: { version: 2, status: "verified", checks: [] }
  });
  const freshWrite = capability({
    id: "create-post-seal-apply", operation: "create", sideEffect: true,
    transport: staleWrite.transport
  });

  const [lookup, write] = reanalyzeIncoming([freshLookup, freshWrite], [staleLookup, staleWrite]);
  assert.equal(lookup!.inputForm[0]!.defaultRule, "literal:reimburse");
  assert.equal(lookup!.validation.status, "candidate");
  assert.equal(write!.id, "create-post-seal-apply");
});
