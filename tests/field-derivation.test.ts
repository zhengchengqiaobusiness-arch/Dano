import test from "node:test";
import assert from "node:assert/strict";
import type { EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { sameValue } from "../src/inference/field-resolver.js";

function events(extra: EvidenceEvent[] = []): EvidenceEvent[] {
  return [{
    id: "ui-form", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:00.000Z",
    pageUrl: "https://example.test/order", eventType: "input",
    form: [
      { name: "productId", label: "产品", type: "select", required: true, value: "苹果" },
      { name: "count", label: "数量", type: "number", required: true, value: 2 },
      { name: "productPrice", label: "单价", type: "number", required: true, value: 10 },
      { name: "unitName", label: "单位", type: "readonly", value: "盒" },
      { name: "requestId", label: "请求号", type: "readonly", value: "550e8400-e29b-41d4-a716-446655440000" }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:01.000Z",
    pageUrl: "https://example.test/order", eventType: "click", text: "确定", label: "确定"
  }, {
    id: "net-product", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.500Z",
    request: { method: "GET", url: "https://example.test/admin-api/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果", unitName: "盒" }] } }
  }, {
    id: "net-create", kind: "network", sessionId: "s", at: "2026-09-02T00:00:02.000Z",
    correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/order/create", resourceType: "xhr", headers: {}, query: {},
      body: {
        productId: 9, count: 2, productPrice: 10, unitName: "盒",
        amount: 20, requestId: "550e8400-e29b-41d4-a716-446655440000", token: "xyz"
      }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }, ...extra];
}

test("sameValue does not treat arbitrary text as boolean true", () => {
  assert.equal(sameValue(true, "盒"), false);
  assert.equal(sameValue(true, "true"), true);
  assert.equal(sameValue(true, 1), true);
  assert.equal(sameValue(3, "3"), true);
});

test("write fields get a unique origin rule instead of a frozen sample", () => {
  const create = buildCapabilityCandidates(events()).find(item => item.transport.pathTemplate.includes("/order/create"))!;
  const unit = create.inputForm.find(field => field.name === "unitName")!;
  const amount = create.inputForm.find(field => field.name === "amount")!;
  const requestId = create.inputForm.find(field => field.name === "requestId")!;
  const token = create.inputForm.find(field => field.name === "token")!;
  const count = create.inputForm.find(field => field.name === "count")!;
  assert.equal(count.source, "caller");
  assert.equal(count.defaultRule, undefined);
  assert.match(unit.defaultRule || "", /^from:.+\.unitName\|via:productId$/);
  assert.equal(unit.source, "binding");
  assert.equal(amount.defaultRule, "computed:count * productPrice");
  assert.equal(requestId.defaultRule, "uuid");
  assert.equal(token.defaultRule, undefined);
  assert.doesNotMatch(unit.sourceDetail || "", /录制成功请求写入/);
  assert.doesNotMatch(amount.sourceDetail || "", /literal:20|写入 20/);
});

test("ids and timestamps are not used as formula operands", () => {
  const create = buildCapabilityCandidates([{
    id: "ui-form", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:00.000Z",
    pageUrl: "https://example.test/leave", eventType: "input",
    form: [
      { name: "type", label: "请假类型", type: "select", required: true, value: "事假" },
      { name: "day", label: "请假天数", type: "number", required: true, value: 1 },
      { name: "startTime", label: "开始时间", type: "date", required: true, value: "2026-09-01" },
      { name: "endTime", label: "结束时间", type: "date", required: true, value: "2026-09-02" },
      { name: "leaveBalance", label: "假期余额", type: "readonly", value: 8 }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "s", at: "2026-09-02T00:00:01.000Z",
    pageUrl: "https://example.test/leave", eventType: "click", text: "提交"
  }, {
    id: "net-balance", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.400Z",
    request: { method: "GET", url: "https://example.test/admin-api/oa/duty-leave/get-balance?type=2", resourceType: "xhr", headers: {}, query: { type: 2 } },
    response: { status: 200, headers: {}, body: { success: true, data: { leaveBalance: 8 } } }
  }, {
    id: "net-users", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.500Z",
    request: { method: "GET", url: "https://example.test/admin-api/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [{ id: 174, username: "LSBM", nickname: "LS部门" }] } } }
  }, {
    id: "net-create", kind: "network", sessionId: "s", at: "2026-09-02T00:00:02.000Z",
    correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/oa/duty-leave/submit-process", resourceType: "xhr", headers: {}, query: {},
      body: {
        type: 2, day: 1, leaveBalance: 8,
        startTime: 1788192000000, endTime: 1788278400000,
        startUserSelectAssignees: { Activity_0ag2wyz: [174] }
      }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }]).find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const day = create.inputForm.find(field => field.name === "day")!;
  const balance = create.inputForm.find(field => field.name === "leaveBalance")!;
  const assignees = create.inputForm.find(field => field.name === "startUserSelectAssignees")!;
  assert.equal(day.source, "caller");
  assert.equal(day.defaultRule, "computed:(endTime - startTime) / 86400000");
  assert.doesNotMatch(balance.defaultRule || "", /^computed:.*\btype\b/);
  assert.match(balance.defaultRule || "", /^from:.+leaveBalance/);
  assert.equal(assignees.source, "computed");
  assert.match(assignees.sourceDetail || "", /拼接/);
});

test("the same value in two queries is not a unique from rule", () => {
  const create = buildCapabilityCandidates(events([{
    id: "net-product-2", kind: "network", sessionId: "s", at: "2026-09-02T00:00:00.600Z",
    request: { method: "GET", url: "https://example.test/admin-api/sku/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果", unitName: "盒" }] } }
  }])).find(item => item.transport.pathTemplate.includes("/order/create"))!;
  const unit = create.inputForm.find(field => field.name === "unitName")!;
  assert.equal(unit.defaultRule?.startsWith("from:") ?? false, false);
  assert.match(unit.sourceDetail || "", /不能把录制样本当成固定值/);
});
