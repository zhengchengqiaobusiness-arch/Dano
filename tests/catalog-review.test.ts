import test from "node:test";
import assert from "node:assert/strict";
import type { CapabilityContract } from "../src/domain.js";
import { isPrimaryCapability, summarizeCatalog } from "../src/inference/export-scope.js";
import { reviewCatalog } from "../src/review/catalog-review.js";

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
    sideEffect: ["create", "update", "review", "delete", "upload"].includes(partial.operation),
    confirmation: { required: ["create", "update", "review", "delete", "upload"].includes(partial.operation) },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "verified", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-01T00:00:00.000Z" },
    ...partial
  };
}

test("page query plus create are the only primaries when user page and simple-list also exist", () => {
  const catalog = [
    cap({ id: "query-order", operation: "query", title: "查询采购订单", transport: { method: "GET", urlTemplate: "https://x/erp/purchase/order/page", origin: "https://x", pathTemplate: "/erp/purchase/order/page" }, inputForm: [{ path: "$.no", name: "no", label: "单号", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text" }] }),
    cap({ id: "create-order", operation: "create", title: "新建采购订单", transport: { method: "POST", urlTemplate: "https://x/erp/purchase/order/create", origin: "https://x", pathTemplate: "/erp/purchase/order/create" } }),
    cap({ id: "query-user", operation: "query", title: "查询用户", transport: { method: "GET", urlTemplate: "https://x/system/user/page", origin: "https://x", pathTemplate: "/system/user/page" }, inputForm: [{ path: "$.username", name: "username", label: "用户", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text" }] }),
    cap({ id: "query-product", operation: "query", title: "查询产品", transport: { method: "GET", urlTemplate: "https://x/erp/product/simple-list", origin: "https://x", pathTemplate: "/erp/product/simple-list" }, inputForm: [{ path: "$.name", name: "name", label: "产品", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text" }] })
  ];
  assert.equal(summarizeCatalog(catalog).primary.map(item => item.id).join(","), "query-order,create-order");
  assert.equal(isPrimaryCapability(catalog[2]!, catalog), false);
  assert.equal(isPrimaryCapability(catalog[3]!, catalog), false);
  const review = reviewCatalog(catalog);
  assert.equal(review.status, "passed");
  assert.equal(review.primaryCount, 2);
});

test("unexplained write field blocks export and asks for re-analyze", () => {
  const catalog = [
    cap({
      id: "create-order",
      operation: "create",
      title: "新建采购订单",
      transport: { method: "POST", urlTemplate: "https://x/erp/purchase/order/create", origin: "https://x", pathTemplate: "/erp/purchase/order/create" },
      inputForm: [{
        path: "$.productUnitName", name: "productUnitName", label: "单位", valueType: "string",
        source: "system", required: false, requiredBasis: "not-observed", systemHandled: true,
        sourceDetail: "页面只读展示，但已录制查询里没有唯一带出路径，不能把录制样本当成固定值", widget: "text"
      }]
    })
  ];
  const review = reviewCatalog(catalog);
  assert.equal(review.status, "blocked");
  assert.equal(review.next, "re-analyze");
  assert.match(review.summary, /审核未通过/);
  assert.match(review.summary, /只验证一次|禁止发现一条/);
  assert.match(review.summary, /单位/);
});

test("frozen user picker blocks export when a recorded user query exists", () => {
  const catalog = [
    cap({
      id: "create-leave",
      operation: "create",
      title: "发起请假",
      transport: { method: "POST", urlTemplate: "https://x/oa/duty-leave/submit-process", origin: "https://x", pathTemplate: "/oa/duty-leave/submit-process" },
      inputForm: [{
        path: "$.startUserSelectAssignees.Activity_0ag2wyz", name: "Activity_0ag2wyz", label: "人力审批",
        valueType: "array", source: "caller", required: true, requiredBasis: "ui-required", systemHandled: false,
        sourceDetail: "页面固定枚举", widget: "select",
        candidates: { type: "static", values: [{ value: 1, label: "管理员" }, { value: 174, label: "LS部门" }] }
      }]
    }),
    cap({
      id: "query-user",
      operation: "query",
      title: "查询用户",
      validation: { version: 2, status: "verified", checks: [] },
      transport: { method: "GET", urlTemplate: "https://x/system/user/page", origin: "https://x", pathTemplate: "/system/user/page" },
      outputSchema: { type: "object", properties: { data: { type: "object", properties: { list: { type: "array", items: { type: "object", properties: { id: { type: "integer" }, nickname: { type: "string" } } } } } } } },
      evidence: [{ eventId: "net-users", sessionId: "s", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }]
    })
  ];
  const events = [{
    id: "net-users", kind: "network" as const, sessionId: "s", at: "2026-09-01T00:00:00.000Z",
    request: { method: "GET", url: "https://x/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [{ id: 1, nickname: "管理员" }, { id: 174, nickname: "LS部门" }] } } }
  }];
  const review = reviewCatalog(catalog, events);
  assert.equal(review.status, "blocked");
  assert.match(review.summary, /选人|弹窗/);
});

test("unsound computed formula blocks export", () => {
  const catalog = [
    cap({
      id: "create-leave",
      operation: "create",
      title: "发起请假",
      transport: { method: "POST", urlTemplate: "https://x/oa/duty-leave/submit-process", origin: "https://x", pathTemplate: "/oa/duty-leave/submit-process" },
      inputForm: [{
        path: "$.leaveBalance", name: "leaveBalance", label: "假期余额", valueType: "number",
        source: "computed", required: false, requiredBasis: "not-observed", systemHandled: true,
        defaultRule: "computed:day - type",
        sourceDetail: "由请求内字段自动计算：day - type，调用方不要手填", widget: "number"
      }, {
        path: "$.day", name: "day", label: "请假天数", valueType: "number",
        source: "caller", required: true, requiredBasis: "ui-required", systemHandled: false,
        sourceDetail: "调用方填写", widget: "number"
      }, {
        path: "$.type", name: "type", label: "请假类型", valueType: "integer",
        source: "caller", required: true, requiredBasis: "ui-required", systemHandled: false,
        sourceDetail: "页面固定枚举", widget: "select",
        candidates: { type: "static", values: [{ value: 1, label: "事假" }] }
      }]
    })
  ];
  const review = reviewCatalog(catalog);
  assert.equal(review.status, "blocked");
  assert.match(review.summary, /编号、枚举或时间戳/);
});

test("missing successful write evidence asks to re-record", () => {
  const catalog = [
    cap({
      id: "create-order",
      operation: "create",
      title: "新建采购订单",
      transport: { method: "POST", urlTemplate: "https://x/erp/purchase/order/create", origin: "https://x", pathTemplate: "/erp/purchase/order/create" },
      validation: {
        version: 2,
        status: "candidate",
        checks: [{ name: "successful-response", ok: false, detail: "No successful recorded response" }]
      }
    })
  ];
  const review = reviewCatalog(catalog);
  assert.equal(review.status, "blocked");
  assert.equal(review.next, "re-record");
  assert.match(review.summary, /回到页面补录/);
});
