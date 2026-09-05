import test from "node:test";
import assert from "node:assert/strict";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { capabilitiesForSession, isPrimaryCapability, relatedLookupCapabilities, sessionCatalogSlice, summarizeCatalog } from "../src/inference/export-scope.js";
import { reviewCatalog } from "../src/review/catalog-review.js";
import { applyReviewActionPolicy, isMajorEvidenceGap } from "../src/review/review-action.js";
import { mergeCatalogByTransport } from "../src/catalog/normalize.js";
import { finalizeSessionSlice } from "../src/inference/finalize-capabilities.js";
import { validateCapability } from "../src/validation/validator.js";

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

test("a same-resource detail reload after save is a supporting lookup, not a third primary ability", () => {
  const catalog = [
    cap({ id: "query-work-report-page", operation: "query", transport: { method: "GET", urlTemplate: "https://x/admin-api/oa/work-report/page", origin: "https://x", pathTemplate: "/admin-api/oa/work-report/page" }, inputForm: [{ path: "$.billCode", name: "billCode", label: "单据编号", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text" }] }),
    cap({ id: "create-work-report", operation: "create", transport: { method: "POST", urlTemplate: "https://x/admin-api/oa/work-report/save", origin: "https://x", pathTemplate: "/admin-api/oa/work-report/save" } }),
    cap({ id: "query-work-report-get", operation: "query", transport: { method: "GET", urlTemplate: "https://x/admin-api/oa/work-report/get", origin: "https://x", pathTemplate: "/admin-api/oa/work-report/get" }, inputForm: [{ path: "$.id", name: "id", label: "id", valueType: "integer", source: "system", required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "保存后刷新详情", widget: "number", defaultRule: "literal:20" }] })
  ];

  assert.deepEqual(summarizeCatalog(catalog).primary.map(item => item.id), ["query-work-report-page", "create-work-report"]);
  assert.equal(isPrimaryCapability(catalog[2]!, catalog), false);
});

test("a detail get with a record-shaped response is still not a primary when the write exists", () => {
  const catalog = [
    cap({
      id: "query-page",
      operation: "query",
      transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" },
      inputForm: [{ path: "$.billCode", name: "billCode", label: "单据编号", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text" }]
    }),
    cap({ id: "create-doc", operation: "create", transport: { method: "POST", urlTemplate: "https://x/oa/doc/submit", origin: "https://x", pathTemplate: "/oa/doc/submit" } }),
    cap({
      id: "query-get",
      operation: "query",
      transport: { method: "GET", urlTemplate: "https://x/oa/doc/get", origin: "https://x", pathTemplate: "/oa/doc/get" },
      outputSchema: { type: "object", properties: { data: { type: "object", properties: { items: { type: "array", items: { type: "object" } } } } } },
      inputForm: [{ path: "$.id", name: "id", label: "id", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "详情", widget: "text" }]
    })
  ];
  assert.equal(isPrimaryCapability(catalog[2]!, catalog), false);
  assert.equal(summarizeCatalog(catalog).primary.some(item => item.id === "query-get"), false);
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
  assert.match(review.summary, /不要进入补录循环|不要对同一审核结果再分析或再录/);
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

test("complete field coverage blocks a blank visible filter and an unexercised business detail collection", () => {
  const query = cap({
    id: "query-seal",
    operation: "query",
    title: "查询公章申请",
    transport: { method: "GET", urlTemplate: "https://x/oa/sealApply/list", origin: "https://x", pathTemplate: "/oa/sealApply/list" },
    evidence: [{ eventId: "net-query", sessionId: "coverage", kind: "network", at: "2026-09-04T10:00:01.000Z", status: 200 }]
  });
  const create = cap({
    id: "create-reimbursement",
    operation: "create",
    title: "新建报销申请",
    transport: { method: "POST", urlTemplate: "https://x/oa/reimburseApply", origin: "https://x", pathTemplate: "/oa/reimburseApply" },
    inputForm: [{
      path: "$.oaReimburseFeeitemList", name: "oaReimburseFeeitemList", label: "报销费用明细",
      valueType: "array", source: "fixed", required: false, requiredBasis: "not-observed", systemHandled: true,
      sourceDetail: "录制请求中的空数组", widget: "text", defaultRule: "literal:[]"
    }],
    evidence: [{ eventId: "net-create", sessionId: "coverage", kind: "network", at: "2026-09-04T10:01:01.000Z", status: 200 }]
  });
  const events: EvidenceEvent[] = [{
    id: "ui-query", kind: "ui", sessionId: "coverage", at: "2026-09-04T10:00:00.000Z",
    pageUrl: "https://x/oa/sealApply", eventType: "click", text: "搜索", label: "搜索",
    form: [
      { label: "流程状态", type: "select", value: "未提交" },
      { label: "公章", type: "select", value: "" }
    ]
  }, {
    id: "net-query", kind: "network", sessionId: "coverage", at: "2026-09-04T10:00:01.000Z",
    pageUrl: "https://x/oa/sealApply", correlatedUiEvidenceId: "ui-query",
    request: { method: "GET", url: "https://x/oa/sealApply/list?status=0", resourceType: "xhr", headers: {}, query: { status: "0" } },
    response: { status: 200, headers: {}, body: { rows: [], total: 0 } }
  }, {
    id: "ui-create", kind: "ui", sessionId: "coverage", at: "2026-09-04T10:01:00.000Z",
    pageUrl: "https://x/oa/reimburseApply/form/add", eventType: "click", text: "保存", label: "保存",
    form: [{ label: "报销缘由", type: "textarea", value: "办公费用" }]
  }, {
    id: "net-create", kind: "network", sessionId: "coverage", at: "2026-09-04T10:01:01.000Z",
    pageUrl: "https://x/oa/reimburseApply/form/add", correlatedUiEvidenceId: "ui-create",
    request: {
      method: "POST", url: "https://x/oa/reimburseApply", resourceType: "xhr", headers: {}, query: {},
      body: { oaReimburseFeeitemList: [] }
    },
    response: { status: 200, headers: {}, body: { code: 200 } }
  }];

  assert.equal(reviewCatalog([query, create], events).status, "passed", "ordinary recordings remain backward compatible");
  const review = reviewCatalog([query, create], events, ["query", "create"], true);
  assert.equal(review.status, "blocked");
  assert.equal(review.next, "re-record");
  assert.equal(review.findings.some(item => item.fieldPath === undefined && item.message.includes("公章")), true);
  assert.equal(review.findings.some(item => item.fieldPath === "$.oaReimburseFeeitemList"), true);
});

test("lookup-to-lookup bindings do not block export of already verified primaries", () => {
  const catalog = [
    cap({
      id: "query-page",
      operation: "query",
      title: "查询单据",
      transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" },
      inputForm: [{ path: "$.billCode", name: "billCode", label: "单据编号", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text" }]
    }),
    cap({
      id: "create-doc",
      operation: "create",
      title: "新建单据",
      transport: { method: "POST", urlTemplate: "https://x/oa/doc/submit", origin: "https://x", pathTemplate: "/oa/doc/submit" }
    }),
    cap({
      id: "query-approval",
      operation: "query",
      title: "查询审批详情",
      transport: { method: "GET", urlTemplate: "https://x/bpm/approval-detail", origin: "https://x", pathTemplate: "/bpm/approval-detail" },
      inputForm: [{
        path: "$.processDefinitionId", name: "processDefinitionId", label: "流程定义", valueType: "string",
        source: "binding", required: false, requiredBasis: "not-observed", systemHandled: true,
        sourceDetail: "从流程图带出", widget: "text",
        defaultRule: "from:query-bpmn:$.data.processInstance.processDefinitionId"
      }],
      bindings: [{
        id: "bind-1", fromCapabilityId: "query-bpmn", fromPath: "$.data.processInstance.processDefinitionId",
        toPath: "$.processDefinitionId", confidence: 1, evidenceIds: [], approved: true,
        approvalSource: "evidence", approvedAt: "2026-09-05T00:00:00.000Z"
      }]
    }),
    cap({
      id: "query-bpmn",
      operation: "query",
      title: "查询流程图",
      transport: { method: "GET", urlTemplate: "https://x/bpm/bpmn-model-view", origin: "https://x", pathTemplate: "/bpm/bpmn-model-view" },
      validation: { version: 2, status: "candidate", checks: [{ name: "binding-structure-valid", ok: false, detail: "绑定引用了未知能力" }] }
    })
  ];
  const review = reviewCatalog(catalog);
  assert.equal(review.status, "passed");
  assert.equal(review.findings.some(item => item.capabilityId === "query-bpmn"), false);
});

test("complete coverage does not demand a named empty filter that the successful request never sent", () => {
  const query = cap({
    id: "query-page",
    operation: "query",
    title: "查询单据",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" },
    evidence: [{ eventId: "net-query", sessionId: "coverage", kind: "network", at: "2026-09-05T11:20:01.000Z", status: 200 }]
  });
  const events: EvidenceEvent[] = [{
    id: "ui-query", kind: "ui", sessionId: "coverage", at: "2026-09-05T11:20:00.000Z",
    pageUrl: "https://x/oa/doc/list", eventType: "click", text: "搜索", label: "搜索",
    form: [
      { name: "billCode", label: "单据编号", type: "text", value: "1" },
      { name: "deptId", label: "申请部门", type: "select", value: "" }
    ]
  }, {
    id: "net-query", kind: "network", sessionId: "coverage", at: "2026-09-05T11:20:01.000Z",
    pageUrl: "https://x/oa/doc/list", correlatedUiEvidenceId: "ui-query",
    request: {
      method: "GET",
      url: "https://x/oa/doc/page?billCode=1",
      resourceType: "xhr",
      headers: {},
      query: { billCode: "1" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];
  const review = reviewCatalog([query], events, ["query"], true);
  assert.equal(review.status, "passed");
  assert.equal(review.findings.some(item => String(item.message).includes("申请部门")), false);
});

test("analyze merge keeps create when a later session only saw the page query", () => {
  const existing = [
    cap({ id: "query-order", operation: "query", transport: { method: "GET", urlTemplate: "https://x/erp/purchase-order/page", origin: "https://x", pathTemplate: "/erp/purchase-order/page" } }),
    cap({ id: "create-order", operation: "create", transport: { method: "POST", urlTemplate: "https://x/erp/purchase-order/create", origin: "https://x", pathTemplate: "/erp/purchase-order/create" } }),
    cap({ id: "query-product", operation: "query", transport: { method: "GET", urlTemplate: "https://x/erp/product/simple-list", origin: "https://x", pathTemplate: "/erp/product/simple-list" } })
  ];
  const incoming = [
    cap({ id: "query-order-again", operation: "query", transport: { method: "GET", urlTemplate: "https://x/erp/purchase-order/page", origin: "https://x", pathTemplate: "/erp/purchase-order/page" } }),
    cap({ id: "query-product-again", operation: "query", transport: { method: "GET", urlTemplate: "https://x/erp/product/simple-list", origin: "https://x", pathTemplate: "/erp/product/simple-list" } })
  ];
  const merged = mergeCatalogByTransport(incoming, existing);
  assert.equal(merged.some(item => item.operation === "create" && item.transport.pathTemplate.includes("/purchase-order/create")), true);
  assert.equal(merged.filter(item => item.transport.pathTemplate.includes("/purchase-order/page")).length, 1);
  assert.equal(merged.find(item => item.transport.pathTemplate.includes("/purchase-order/page"))?.id, "query-order-again");
});

test("session review keeps same-page writes and ignores other-page unverified creates", () => {
  const chat = cap({
    id: "ask-chat",
    operation: "query",
    title: "查询 sjws_chat",
    transport: { method: "POST", urlTemplate: "http://x/dataiq/sjws_chat", origin: "http://x", pathTemplate: "/dataiq/sjws_chat" },
    inputForm: [{ path: "$.sys_query", name: "sys_query", label: "和数据智能体聊天", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text" }],
    evidence: [{ eventId: "chat-net", sessionId: "chat", kind: "network", at: "2026-09-03T10:00:00.000Z", status: 200 }]
  });
  const create = cap({
    id: "create-order",
    operation: "create",
    title: "新建 purchase-order",
    transport: { method: "POST", urlTemplate: "https://x/erp/purchase-order/create", origin: "https://x", pathTemplate: "/erp/purchase-order/create" },
    evidence: [{ eventId: "purchase-net", sessionId: "purchase", kind: "network", at: "2026-09-02T10:00:00.000Z", status: 200 }],
    validation: { version: 2, status: "candidate", checks: [{ name: "caller-fields-backed-by-ui", ok: false, detail: "存在没有页面输入证据的调用方字段" }] }
  });
  const chatEvents = [{
    id: "chat-net",
    kind: "network" as const,
    sessionId: "chat",
    at: "2026-09-03T10:00:00.000Z",
    pageUrl: "http://10.255.158.85/dopenportal/#/Home",
    request: { method: "POST", url: "http://x/dataiq/sjws_chat", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: {} }
  }];
  const purchaseEvents = [{
    id: "purchase-net",
    kind: "network" as const,
    sessionId: "purchase",
    at: "2026-09-02T10:00:00.000Z",
    pageUrl: "http://admin.dianshixinxi.com:90/erp/purchase/order",
    request: { method: "POST", url: "https://x/erp/purchase-order/create", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: {} }
  }];
  const scoped = capabilitiesForSession([chat, create], [...chatEvents, ...purchaseEvents], chatEvents);
  assert.equal(scoped.some(item => item.id === "ask-chat"), true);
  assert.equal(scoped.some(item => item.id === "create-order"), false);
  const review = reviewCatalog(scoped, chatEvents);
  assert.equal(review.status, "passed", review.summary);
  assert.equal(review.primaryTitles.join(","), "查询 sjws_chat");
});

test("complete coverage does not demand a chooser that opened with no options", () => {
  const query = cap({
    id: "query-doc",
    operation: "query",
    title: "查询单据",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" },
    evidence: [{ eventId: "net-search", sessionId: "s", kind: "network", at: "2026-09-05T08:00:01.000Z", status: 200 }]
  });
  const events: EvidenceEvent[] = [{
    id: "ui-search", kind: "ui", sessionId: "s", at: "2026-09-05T08:00:00.000Z",
    pageUrl: "https://x/web/#/oa/doc", eventType: "click", text: "搜索", label: "搜索",
    form: [
      { name: "billCode", label: "单据编号", type: "text", value: "A1" },
      { name: "deptId", label: "申请部门", type: "select", value: "", visibleOptions: ["暂无数据"] }
    ]
  }, {
    id: "net-search", kind: "network", sessionId: "s", at: "2026-09-05T08:00:01.000Z",
    pageUrl: "https://x/web/#/oa/doc", correlatedUiEvidenceId: "ui-search",
    request: { method: "GET", url: "https://x/oa/doc/page?billCode=A1", resourceType: "xhr", headers: {}, query: { billCode: "A1" } },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];
  const review = reviewCatalog([query], events, ["query"], true);
  assert.equal(review.findings.some(item => item.code === "complete-field-coverage"), false, review.summary);
});

test("unverified lookup without this session's evidence stays out of the session slice", () => {
  const query = cap({
    id: "query-doc",
    operation: "query",
    title: "查询单据",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" },
    inputForm: [{
      path: "$.billCode", name: "billCode", label: "单据编号", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
    }, {
      path: "$.deptId", name: "deptId", label: "申请部门", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "旧候选", widget: "select",
      candidates: { type: "capability", capabilityId: "query-dept", valuePath: "$.data[*].id", labelPath: "$.data[*].name" }
    }],
    evidence: [{ eventId: "net-page", sessionId: "now", kind: "network", at: "2026-09-05T08:00:01.000Z", status: 200 }]
  });
  const create = cap({
    id: "create-doc",
    operation: "create",
    title: "新建单据",
    transport: { method: "POST", urlTemplate: "https://x/oa/doc/submit", origin: "https://x", pathTemplate: "/oa/doc/submit" },
    inputForm: [{
      path: "$.title", name: "title", label: "标题", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
    }],
    evidence: [{ eventId: "net-create", sessionId: "now", kind: "network", at: "2026-09-05T08:00:02.000Z", status: 200 }]
  });
  const dept = cap({
    id: "query-dept",
    operation: "query",
    title: "查询部门",
    transport: { method: "GET", urlTemplate: "https://x/system/dept/list", origin: "https://x", pathTemplate: "/system/dept/list" },
    evidence: [{ eventId: "old-dept", sessionId: "old", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }],
    validation: {
      version: 2,
      status: "candidate",
      checks: [{ name: "recorded-network-evidence", ok: false, detail: "No recorded network evidence" }]
    }
  });
  const events: EvidenceEvent[] = [{
    id: "net-page", kind: "network", sessionId: "now", at: "2026-09-05T08:00:01.000Z",
    pageUrl: "https://x/web/#/oa/doc",
    request: { method: "GET", url: "https://x/oa/doc/page?billCode=A1", resourceType: "xhr", headers: {}, query: { billCode: "A1" } },
    response: { status: 200, headers: {}, body: { data: { list: [] } } }
  }, {
    id: "net-create", kind: "network", sessionId: "now", at: "2026-09-05T08:00:02.000Z",
    pageUrl: "https://x/web/#/oa/doc-info",
    request: { method: "POST", url: "https://x/oa/doc/submit", resourceType: "xhr", headers: {}, query: {}, body: { title: "A" } },
    response: { status: 200, headers: {}, body: { data: 1 } }
  }];
  const slice = sessionCatalogSlice([query, create, dept], events, events);
  assert.equal(slice.some(item => item.id === "query-dept"), false, slice.map(item => item.id).join(","));
  const review = reviewCatalog(slice, events, ["query", "create"]);
  assert.equal(review.findings.some(item => item.capabilityId === "query-dept"), false, review.summary);
});

test("caller field not sent in this session does not fail caller-fields-backed-by-ui", () => {
  const query = cap({
    id: "query-doc",
    operation: "query",
    title: "查询单据",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/page?billCode={billCode}", origin: "https://x", pathTemplate: "/oa/doc/page" },
    inputForm: [
      { path: "$.billCode", name: "billCode", label: "单据编号", valueType: "string", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text" },
      { path: "$.createTime", name: "createTime", label: "开始时间 / 结束时间", valueType: "array", source: "caller", required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "旧会话日期", widget: "date" }
    ],
    evidence: [
      { eventId: "ui-search", sessionId: "now", kind: "ui", at: "2026-09-05T08:00:00.000Z" },
      { eventId: "net-search", sessionId: "now", kind: "network", at: "2026-09-05T08:00:01.000Z", status: 200 }
    ],
    validation: { version: 2, status: "candidate", checks: [] }
  });
  const events: EvidenceEvent[] = [{
    id: "ui-search", kind: "ui", sessionId: "now", at: "2026-09-05T08:00:00.000Z",
    pageUrl: "https://x/web/#/oa/doc", eventType: "click", text: "搜索", label: "搜索",
    form: [{ name: "billCode", label: "单据编号", type: "text", value: "A1" }]
  }, {
    id: "net-search", kind: "network", sessionId: "now", at: "2026-09-05T08:00:01.000Z",
    pageUrl: "https://x/web/#/oa/doc", correlatedUiEvidenceId: "ui-search",
    request: { method: "GET", url: "https://x/oa/doc/page?billCode=A1", resourceType: "xhr", headers: {}, query: { billCode: "A1" } },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];
  const validated = validateCapability(query, events, [query]);
  const check = validated.validation.checks.find(item => item.name === "caller-fields-backed-by-ui");
  assert.equal(check?.ok, true, JSON.stringify(validated.validation.checks.filter(item => !item.ok)));
});

test("a department picker brings in a recorded directory lookup from another page", () => {
  const page = cap({
    id: "query-doc-page",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" },
    inputForm: [{
      path: "$.deptId", name: "deptId", label: "申请部门", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "select"
    }]
  });
  const create = cap({
    id: "create-doc",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/oa/doc/submit", origin: "https://x", pathTemplate: "/oa/doc/submit" }
  });
  const dept = cap({
    id: "query-dept",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/system/dept/simple-list", origin: "https://x", pathTemplate: "/system/dept/simple-list" },
    outputSchema: {
      type: "object",
      properties: {
        data: { type: "array", items: { type: "object", properties: { id: { type: "integer" }, name: { type: "string" } } } }
      }
    }
  });
  const related = relatedLookupCapabilities([page, create, dept], [page, create]);
  assert.equal(related.some(item => item.id === "query-dept"), true);
  const foreign = cap({
    id: "query-foreign-dept",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://other.test/system/dept/simple-list", origin: "https://other.test", pathTemplate: "/system/dept/simple-list" }
  });
  const sameHostOnly = relatedLookupCapabilities([page, create, dept, foreign], [page, create]);
  assert.equal(sameHostOnly.some(item => item.id === "query-foreign-dept"), false);
});

test("finalize drops a sibling business page bound as a department candidate", () => {
  const stats = cap({
    id: "query-doc-statistics",
    operation: "query",
    title: "查询单据",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/statistics", origin: "https://x", pathTemplate: "/oa/doc/statistics" },
    inputForm: [{
      path: "$.deptId", name: "deptId", label: "组织机构", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false,
      sourceDetail: "调用方从已录制查询接口选择，不要写死录制样本。接口 GET /oa/doc/page，值 $.data.list[*].id，显示 $.data.list[*].title",
      widget: "select",
      candidates: {
        type: "capability",
        capabilityId: "query-doc-page",
        valuePath: "$.data.list[*].id",
        labelPath: "$.data.list[*].title"
      }
    }]
  });
  const page = cap({
    id: "query-doc-page",
    operation: "query",
    title: "查询单据",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" },
    outputSchema: {
      type: "object",
      properties: {
        data: {
          type: "object",
          properties: {
            list: { type: "array", items: { type: "object", properties: { id: { type: "integer" }, title: { type: "string" } } } },
            total: { type: "integer" }
          }
        }
      }
    },
    inputForm: [{
      path: "$.billCode", name: "billCode", label: "单据编号", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
    }]
  });
  const [next] = finalizeSessionSlice([stats, page], [], [stats, page]);
  const dept = next?.inputForm.find(item => item.name === "deptId");
  assert.equal(dept?.candidates, undefined);
});

test("finalize drops candidate sources that are not in this session slice", () => {
  const query = cap({
    id: "query-doc",
    operation: "query",
    title: "查询单据",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" },
    inputForm: [{
      path: "$.deptId", name: "deptId", label: "申请部门", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "旧候选", widget: "select",
      candidates: { type: "capability", capabilityId: "query-dept", valuePath: "$.data[*].id", labelPath: "$.data[*].name" }
    }],
    evidence: [{ eventId: "net-search", sessionId: "now", kind: "network", at: "2026-09-05T08:00:01.000Z", status: 200 }]
  });
  const events: EvidenceEvent[] = [{
    id: "net-search", kind: "network", sessionId: "now", at: "2026-09-05T08:00:01.000Z",
    pageUrl: "https://x/web/#/oa/doc",
    request: { method: "GET", url: "https://x/oa/doc/page?billCode=A1", resourceType: "xhr", headers: {}, query: { billCode: "A1" } },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }];
  const [next] = finalizeSessionSlice([query], events, [query]);
  assert.equal(next?.inputForm.find(item => item.name === "deptId")?.candidates, undefined);
});

test("lookup evidence gaps remapped to re-analyze when primary writes already succeeded", () => {
  const query = cap({
    id: "query-doc",
    operation: "query",
    title: "查询单据",
    transport: { method: "GET", urlTemplate: "https://x/oa/doc/page", origin: "https://x", pathTemplate: "/oa/doc/page" }
  });
  const create = cap({
    id: "create-doc",
    operation: "create",
    title: "新建单据",
    transport: { method: "POST", urlTemplate: "https://x/oa/doc/submit", origin: "https://x", pathTemplate: "/oa/doc/submit" },
    inputForm: [{
      path: "$.deptId", name: "deptId", label: "申请部门", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "select",
      candidates: { type: "capability", capabilityId: "query-dept", valuePath: "$.data[*].id", labelPath: "$.data[*].name" }
    }]
  });
  const dept = cap({
    id: "query-dept",
    operation: "query",
    title: "查询部门",
    transport: { method: "GET", urlTemplate: "https://x/system/dept/list", origin: "https://x", pathTemplate: "/system/dept/list" },
    validation: {
      version: 2,
      status: "candidate",
      checks: [{ name: "recorded-network-evidence", ok: false, detail: "No recorded network evidence" }]
    }
  });
  const review = reviewCatalog([query, create, dept]);
  const leftover = review.findings.find(item => item.capabilityId === "query-dept" && item.code === "recorded-network-evidence");
  assert.ok(leftover, review.summary);
  assert.equal(leftover.next, "re-analyze");
  assert.equal(review.next, "re-analyze");
  assert.match(review.summary, /不要进入补录循环|不要重新录制/);
  assert.equal(isMajorEvidenceGap(leftover, [query, create]), false);
});

test("applyReviewActionPolicy keeps major primary gaps on re-record", () => {
  const create = cap({
    id: "create-doc",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/oa/doc/submit", origin: "https://x", pathTemplate: "/oa/doc/submit" }
  });
  const [kept] = applyReviewActionPolicy([{
    code: "successful-response",
    severity: "block",
    stage: "record",
    next: "re-record",
    capabilityId: "create-doc",
    capabilityTitle: "新建单据",
    message: "No successful recorded response"
  }], [create]);
  assert.equal(kept?.next, "re-record");
  const [remapped] = applyReviewActionPolicy([{
    code: "recorded-network-evidence",
    severity: "block",
    stage: "record",
    next: "re-record",
    capabilityId: "query-dept",
    capabilityTitle: "查询部门",
    message: "No recorded network evidence"
  }], [create]);
  assert.equal(remapped?.next, "re-analyze");
  assert.match(remapped?.message || "", /不要重新录制/);
});
