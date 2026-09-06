import test from "node:test";
import assert from "node:assert/strict";
import type { CapabilityContract, EvidenceEvent, InputFormField } from "../src/domain.js";
import { inferOperation } from "../src/inference/heuristics.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import {
  bindLeftoverFields,
  findObservation,
  type UiObservation
} from "../src/inference/field-resolver.js";
import {
  capabilitiesForSession,
  exportableCapabilities,
  isPrimaryCapability,
  sessionCatalogSlice,
  summarizeCatalog
} from "../src/inference/export-scope.js";
import { reviewCatalog, reviewSession } from "../src/review/catalog-review.js";
import { applyPiCatalogJudgment } from "../src/inference/pi-skill-runtime.js";

function field(partial: Partial<InputFormField> & Pick<InputFormField, "name">): InputFormField {
  return {
    path: partial.path || `$.${partial.name}`,
    label: partial.label || partial.name,
    valueType: partial.valueType || "string",
    source: partial.source || "system",
    required: partial.required ?? false,
    requiredBasis: partial.requiredBasis || "not-observed",
    systemHandled: partial.systemHandled ?? true,
    sourceDetail: partial.sourceDetail || "未推断",
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
    sideEffect: ["create", "update", "review", "delete", "upload", "action"].includes(partial.operation),
    confirmation: { required: ["create", "update", "review", "delete", "upload", "action"].includes(partial.operation) },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "verified", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-03T00:00:00.000Z" },
    ...partial
  };
}

function network(method: string, url: string, extra: Partial<EvidenceEvent> = {}): EvidenceEvent {
  return {
    id: "n1",
    kind: "network",
    sessionId: "s1",
    at: "2026-09-03T00:00:00.000Z",
    request: { method, url, resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: {} },
    ...extra
  } as EvidenceEvent;
}

test("review keeps referenced lookup from another session on the same page", () => {
  const write = cap({
    id: "create-leave",
    operation: "create",
    title: "发起请假",
    transport: { method: "POST", urlTemplate: "https://x/oa/duty-leave/submit-process", origin: "https://x", pathTemplate: "/oa/duty-leave/submit-process" },
    evidence: [{ eventId: "net-create", sessionId: "now", kind: "network", at: "2026-09-03T00:00:00.000Z", status: 200 }],
    inputForm: [field({
      name: "leaveBalance", path: "$.leaveBalance", label: "假期余额", valueType: "integer",
      source: "binding", defaultRule: "from:query-balance:$.data.leaveBalance|via:type",
      widget: "number"
    })]
  });
  const lookup = cap({
    id: "query-balance",
    operation: "query",
    title: "查询余额",
    validation: { version: 2, status: "candidate", checks: [{ name: "not-verified", ok: false, detail: "尚未验证" }] },
    transport: { method: "GET", urlTemplate: "https://x/oa/duty-leave/leave-balance/my", origin: "https://x", pathTemplate: "/oa/duty-leave/leave-balance/my" },
    evidence: [{ eventId: "net-balance", sessionId: "old", kind: "network", at: "2026-09-02T00:00:00.000Z", status: 200 }]
  });
  const events: EvidenceEvent[] = [
    network("POST", "https://x/oa/duty-leave/submit-process", { id: "net-create", sessionId: "now", pageUrl: "https://x/oa/duty/leave" }),
    network("GET", "https://x/oa/duty-leave/leave-balance/my", { id: "net-balance", sessionId: "old", pageUrl: "https://x/oa/duty/leave" })
  ];
  const now = events.filter(item => item.sessionId === "now");
  const { capabilities, review } = reviewSession([write, lookup], events, now);
  assert.equal(capabilities.some(item => item.id === "query-balance"), true);
  assert.equal(review.status, "blocked", review.summary);
  assert.match(review.summary, /余额|尚未|验证/);
});

test("submit-style POST is a primary write even when the path is not CRUD", () => {
  const complete = inferOperation(
    network("POST", "https://x/bpm/task/complete") as any,
    { text: "提交", pageUrl: "https://x/oa/approval" } as any
  );
  assert.equal(["create", "action"].includes(complete), true);
  const saveNew = inferOperation(
    network("POST", "https://x/erp/order/save") as any,
    { text: "新增", pageUrl: "https://x/erp/order" } as any
  );
  assert.equal(saveNew, "create");
  const createOnApprovalPage = inferOperation(
    network("POST", "https://x/oa/duty-leave/create") as any,
    { text: "提交", pageUrl: "https://x/oa/approval/leave", label: "人力审批" } as any
  );
  assert.equal(createOnApprovalPage, "create");
  const action = cap({
    id: "enable-user",
    operation: "action",
    title: "启用用户",
    transport: { method: "POST", urlTemplate: "https://x/system/user/update-status", origin: "https://x", pathTemplate: "/system/user/update-status" }
  });
  assert.equal(isPrimaryCapability(action, [action]), true);
  const review = reviewCatalog([action]);
  assert.equal(review.primaryCount, 1);
});

test("login performed before a business workflow is classified as authentication noise", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-login",
    kind: "ui",
    sessionId: "seal-recording",
    at: "2026-09-06T07:03:00.914Z",
    pageUrl: "http://boot.test/login?redirect=%2Foa%2Fseal%2FsealApply",
    eventType: "click",
    text: "登录",
    label: "登录"
  }, network("POST", "http://boot.test/prod-api/login", {
    id: "net-login",
    sessionId: "seal-recording",
    at: "2026-09-06T07:03:01.095Z",
    pageUrl: "http://boot.test/login?redirect=%2Foa%2Fseal%2FsealApply",
    correlatedUiEvidenceId: "ui-login",
    request: {
      method: "POST",
      url: "http://boot.test/prod-api/login",
      resourceType: "xhr",
      headers: {},
      query: {},
      body: { username: "demo", password: "[REDACTED]", code: "2", uuid: "recorded" }
    },
    response: { status: 200, headers: {}, body: { code: 200, msg: "操作成功" } }
  }), network("GET", "http://boot.test/prod-api/oa/sealApply/list?pageNum=1", {
    id: "net-query",
    sessionId: "seal-recording",
    at: "2026-09-06T07:03:49.140Z",
    pageUrl: "http://boot.test/oa/seal/sealApply?billType=seal_apply",
    response: { status: 200, headers: {}, body: { code: 200, rows: [] } }
  })];

  const catalog = buildCapabilityCandidates(events);
  const login = catalog.find(item => item.transport.pathTemplate === "/prod-api/login");
  assert.equal(login?.operation, "authenticate");
  assert.equal(capabilitiesForSession(catalog, events, events).some(item => item.id === login?.id), false);
});

test("a recorded download is an exportable primary page operation", () => {
  const download = cap({
    id: "download-report",
    operation: "download",
    title: "Download report",
    transport: { method: "GET", urlTemplate: "https://x/reports/export", origin: "https://x", pathTemplate: "/reports/export" }
  });
  assert.equal(isPrimaryCapability(download, [download]), true);
  assert.deepEqual(exportableCapabilities([download]).map(item => item.id), [download.id]);
  assert.equal(reviewCatalog([download], [], ["download"]).status, "passed");
});

test("review blocks a partial package when the recording expected query and create", () => {
  const query = cap({
    id: "query-seal-apply",
    operation: "query",
    title: "查询印章申请",
    transport: { method: "GET", urlTemplate: "https://x/oa/sealApply/list", origin: "https://x", pathTemplate: "/oa/sealApply/list" }
  });
  const review = reviewCatalog([query], [], ["query", "create"]);
  assert.equal(review.status, "blocked", review.summary);
  assert.equal(review.findings.some(item => item.code === "missing-expected-operation" && item.message.includes("新建")), true, review.summary);
});

test("review blocks a capability that tries to obtain an input from itself", () => {
  const create = cap({
    id: "create-seal",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/oa/seal", origin: "https://x", pathTemplate: "/oa/seal" },
    bindings: [{
      id: "self-binding",
      fromCapabilityId: "create-seal",
      fromPath: "$.data.billType",
      toPath: "$.billType",
      confidence: 1,
      evidenceIds: ["net-create"],
      approved: true
    }]
  });
  const review = reviewCatalog([create]);
  assert.equal(review.status, "blocked");
  assert.equal(review.findings.some(item => item.code === "binding-structure-valid" && item.fieldPath === "$.billType"), true, review.summary);
});

test("same-resource list stays primary while its picker list stays a dependency", () => {
  const query = cap({
    id: "query-seal-apply",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/prod-api/oa/sealApply/list", origin: "https://x", pathTemplate: "/prod-api/oa/sealApply/list" }
  });
  const picker = cap({
    id: "query-seal-options",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/prod-api/oa/seal/listAll", origin: "https://x", pathTemplate: "/prod-api/oa/seal/listAll" }
  });
  const create = cap({
    id: "create-seal-apply",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/prod-api/oa/sealApply", origin: "https://x", pathTemplate: "/prod-api/oa/sealApply" },
    inputForm: [field({
      name: "sealId",
      path: "$.sealId",
      source: "caller",
      systemHandled: false,
      candidates: { type: "capability", capabilityId: picker.id, valuePath: "$.data[*].id", labelPath: "$.data[*].name" }
    })]
  });
  const catalog = [query, picker, create];
  assert.equal(isPrimaryCapability(query, catalog), true);
  assert.equal(isPrimaryCapability(picker, catalog), false);
  assert.deepEqual(exportableCapabilities(catalog).map(item => item.id), [query.id, picker.id, create.id]);
});

test("lookup-named API is primary only when it is the page's own query", () => {
  const balance = cap({
    id: "query-balance",
    operation: "query",
    title: "查询余额",
    transport: { method: "GET", urlTemplate: "https://x/oa/leave-balance/my", origin: "https://x", pathTemplate: "/oa/leave-balance/my" },
    inputForm: [field({ name: "type", label: "请假类型", source: "caller", systemHandled: false })]
  });
  assert.equal(isPrimaryCapability(balance, [balance]), true);
  const page = cap({
    id: "query-leave",
    operation: "query",
    title: "查询请假",
    transport: { method: "GET", urlTemplate: "https://x/oa/duty-leave/page", origin: "https://x", pathTemplate: "/oa/duty-leave/page" },
    inputForm: [field({ name: "type", label: "请假类型", source: "caller", systemHandled: false })]
  });
  assert.equal(isPrimaryCapability(balance, [page, balance]), false);
  assert.equal(isPrimaryCapability(page, [page, balance]), true);
});

test("export does not dump every verified lookup when no primary exists", () => {
  const lookup = cap({
    id: "query-user",
    operation: "query",
    title: "查询用户",
    transport: { method: "GET", urlTemplate: "https://x/system/user/page", origin: "https://x", pathTemplate: "/system/user/page" }
  });
  assert.deepEqual(exportableCapabilities([lookup]).map(item => item.id), []);
});

test("session scope does not fall back to another page when nothing matches", () => {
  const other = cap({
    id: "create-order",
    operation: "create",
    title: "新建采购",
    transport: { method: "POST", urlTemplate: "https://x/erp/order/create", origin: "https://x", pathTemplate: "/erp/order/create" },
    evidence: [{ eventId: "purchase-net", sessionId: "p", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }]
  });
  const session: EvidenceEvent[] = [
    network("GET", "https://x/oa/duty-leave/page", { id: "leave-net", sessionId: "leave", pageUrl: "https://x/oa/duty/leave" })
  ];
  const all: EvidenceEvent[] = [
    ...session,
    network("POST", "https://x/erp/order/create", { id: "purchase-net", sessionId: "p", pageUrl: "https://x/erp/order" })
  ];
  assert.deepEqual(capabilitiesForSession([other], all, session).map(item => item.id), []);
  assert.deepEqual(sessionCatalogSlice([other], all, session).map(item => item.id), []);
});

test("leftover one-to-one does not bind a hidden token to an unrelated remark", () => {
  const fields = [
    field({ name: "token", path: "$.token", source: "system", sourceDetail: "请求中出现但未能唯一对应" })
  ];
  const observations: UiObservation[] = [{ name: "remark", label: "备注", type: "textarea", value: "说明文字" }];
  const bound = bindLeftoverFields(fields, observations, { token: "hidden-token" });
  assert.notEqual(bound[0]?.source, "caller");
  assert.notEqual(bound[0]?.label, "备注");
});

test("leftover one-to-one does not guess a remaining visible field", () => {
  const fields = [
    field({ name: "billType", path: "$.billType", source: "system", sourceDetail: "未解析" }),
    field({ name: "useInfo", path: "$.useInfo", source: "system", sourceDetail: "未解析" }),
    field({ name: "remark", path: "$.remark", label: "备注", source: "caller", systemHandled: false })
  ];
  const observations: UiObservation[] = [
    { name: "w-e-textarea-1", label: "使用描述", type: "textarea", value: "同一段测试内容" },
    { label: "备注", type: "textarea", value: "同一段测试内容" }
  ];
  const bound = bindLeftoverFields(fields, observations, {
    billType: "seal_apply",
    useInfo: "<p>同一段测试内容</p>",
    remark: "同一段测试内容"
  });
  assert.equal(bound.find(item => item.name === "billType")?.source, "system");
  assert.equal(bound.find(item => item.name === "useInfo")?.source, "system");
  assert.notEqual(bound.find(item => item.name === "useInfo")?.label, "使用描述");
});

test("a single start-date observation does not claim the end-date request field", () => {
  const end = field({
    name: "endTime", path: "$.endTime", label: "endTime", valueType: "integer",
    source: "system", widget: "date"
  });
  const observations: UiObservation[] = [
    { name: "startTime", label: "开始时间", type: "date", value: "2026-09-01" }
  ];
  assert.equal(findObservation(end, 1789920000000, observations), undefined);
  const leftover = bindLeftoverFields(
    [end, field({ name: "startTime", path: "$.startTime", label: "开始时间", source: "caller", widget: "date" })],
    observations,
    { startTime: 1789401600000, endTime: 1789920000000 }
  );
  assert.notEqual(leftover.find(item => item.name === "endTime")?.label, "开始时间");
});

test("zero quantity does not bind to another field named stock via productId", () => {
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "s", at: "2026-09-03T00:00:00.000Z",
    pageUrl: "https://x/erp/stock", eventType: "input",
    form: [
      { name: "productId", label: "产品", type: "select", required: true, value: "苹果" },
      { name: "qty", label: "数量", type: "number", required: true, value: 0 }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "s", at: "2026-09-03T00:00:01.000Z",
    pageUrl: "https://x/erp/stock", eventType: "click", text: "提交"
  }, {
    id: "net-product", kind: "network", sessionId: "s", at: "2026-09-03T00:00:00.500Z",
    request: { method: "GET", url: "https://x/erp/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 3, name: "苹果", stock: 0 }] } }
  }, {
    id: "net-create", kind: "network", sessionId: "s", at: "2026-09-03T00:00:02.000Z",
    correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://x/erp/stock/create", resourceType: "xhr", headers: {}, query: {},
      body: { productId: 3, qty: 0 }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }];
  const create = finalizeCapabilities(buildCapabilityCandidates(events), events)
    .find(item => item.transport.pathTemplate.includes("/stock/create"))!;
  const qty = create.inputForm.find(item => item.name === "qty")!;
  assert.doesNotMatch(qty.defaultRule || "", /stock/);
  assert.notEqual(qty.source, "binding");
});

test("frozen picker still blocks when the user query is not yet verified", () => {
  const catalog = [
    cap({
      id: "create-leave",
      operation: "create",
      title: "发起请假",
      transport: { method: "POST", urlTemplate: "https://x/oa/duty-leave/submit-process", origin: "https://x", pathTemplate: "/oa/duty-leave/submit-process" },
      inputForm: [field({
        name: "Activity_0ag2wyz", path: "$.startUserSelectAssignees.Activity_0ag2wyz",
        label: "人力审批", valueType: "array", source: "caller", required: true,
        requiredBasis: "ui-required", systemHandled: false, widget: "select",
        candidates: { type: "static", values: [{ value: 1, label: "管理员" }, { value: 174, label: "LS部门" }] }
      })]
    }),
    cap({
      id: "query-user",
      operation: "query",
      title: "查询用户",
      validation: { version: 2, status: "candidate", checks: [] },
      transport: { method: "GET", urlTemplate: "https://x/system/user/page", origin: "https://x", pathTemplate: "/system/user/page" },
      outputSchema: { type: "object", properties: { data: { type: "object", properties: { list: { type: "array", items: { type: "object", properties: { id: { type: "integer" }, username: { type: "string" } } } } } } } },
      evidence: [{ eventId: "net-users", sessionId: "s", kind: "network", at: "2026-09-03T00:00:00.000Z", status: 200 }]
    })
  ];
  const events: EvidenceEvent[] = [{
    id: "net-users", kind: "network", sessionId: "s", at: "2026-09-03T00:00:00.000Z",
    request: { method: "GET", url: "https://x/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [{ id: 1, username: "admin" }, { id: 174, username: "ls" }] } } }
  }];
  const review = reviewCatalog(catalog, events);
  assert.equal(review.status, "blocked");
  assert.match(review.summary, /选人|弹窗|验证/);
});

test("username-only user lists are not guessed without a unique recorded value", () => {
  const catalog = [
    cap({
      id: "create-leave",
      operation: "create",
      title: "发起请假",
      transport: { method: "POST", urlTemplate: "https://x/oa/duty-leave/submit-process", origin: "https://x", pathTemplate: "/oa/duty-leave/submit-process" },
      inputForm: [field({
        name: "Activity_0ag2wyz", path: "$.startUserSelectAssignees.Activity_0ag2wyz",
        label: "人力审批", valueType: "array", source: "caller", required: true,
        requiredBasis: "ui-required", systemHandled: false, widget: "select"
      })]
    }),
    cap({
      id: "query-user",
      operation: "query",
      title: "查询用户",
      transport: { method: "GET", urlTemplate: "https://x/system/user/page", origin: "https://x", pathTemplate: "/system/user/page" },
      outputSchema: { type: "object", properties: { data: { type: "object", properties: { list: { type: "array", items: { type: "object", properties: { id: { type: "integer" }, username: { type: "string" } } } } } } } }
    })
  ];
  const finalized = finalizeCapabilities(catalog, []);
  const picker = finalized[0]!.inputForm.find(item => item.name === "Activity_0ag2wyz")!;
  assert.notEqual(picker.candidates?.type, "capability");
});

test("recalculate stays action and is still exportable as a primary", () => {
  assert.equal(inferOperation(network("POST", "https://x/orders/recalculate") as any), "unknown");
  const catalog = [
    cap({
      id: "recalculate",
      operation: "action",
      title: "重算订单",
      transport: { method: "POST", urlTemplate: "https://x/orders/recalculate", origin: "https://x", pathTemplate: "/orders/recalculate" }
    })
  ];
  assert.deepEqual(summarizeCatalog(catalog).primary.map(item => item.id), ["recalculate"]);
  assert.deepEqual(exportableCapabilities(catalog).map(item => item.id), ["recalculate"]);
});

test("model judgment cannot bind a field to the capability being executed", async () => {
  const query = cap({
    id: "query-duty",
    operation: "query",
    role: "primary",
    transport: {
      method: "GET",
      urlTemplate: "https://x/oa/dutyApply/list?days={days}",
      origin: "https://x",
      pathTemplate: "/oa/dutyApply/list"
    },
    inputForm: [field({ name: "days", source: "caller", systemHandled: false })]
  });
  const reasoner = {
    model: "test",
    available: () => true,
    parseStructured: async () => ({
      capabilities: [{
        id: query.id,
        operation: "query",
        role: "primary",
        title: query.title,
        description: query.description,
        fields: [{
          path: "$.days",
          required: true,
          source: "binding",
          defaultRule: `from:${query.id}:$.rows[*].days`,
          candidateCapabilityId: query.id,
          candidateValuePath: "$.rows[*].days",
          candidateLabelPath: "$.rows[*].days"
        }]
      }]
    })
  };

  const [judged] = await applyPiCatalogJudgment([query], [], reasoner as any, process.cwd(), true);
  const days = judged!.inputForm[0]!;
  assert.equal(days.source, "caller");
  assert.equal(days.required, false);
  assert.equal(days.defaultRule, undefined);
  assert.equal(days.candidates, undefined);
  assert.equal(judged!.bindings.some(binding => binding.fromCapabilityId === judged!.id), false);
});

test("model judgment cannot invent that an optional recorded field is required", async () => {
  const query = cap({
    id: "query-duty-optional",
    operation: "query",
    role: "primary",
    transport: {
      method: "GET",
      urlTemplate: "https://x/oa/dutyApply/list?leaveType={leaveType}",
      origin: "https://x",
      pathTemplate: "/oa/dutyApply/list"
    },
    inputForm: [field({
      name: "leaveType",
      label: "请假类型",
      source: "caller",
      systemHandled: false,
      required: false,
      requiredBasis: "not-observed",
      sourceDetail: "页面同名字段有输入，由调用方提供"
    })]
  });
  const reasoner = {
    model: "test",
    available: () => true,
    parseStructured: async () => ({
      capabilities: [{
        id: query.id,
        operation: "query",
        role: "primary",
        title: query.title,
        description: query.description,
        fields: [{ path: "$.leaveType", required: true }]
      }]
    })
  };

  const [judged] = await applyPiCatalogJudgment([query], [], reasoner as any, process.cwd(), true);

  assert.equal(judged!.inputForm[0]?.required, false);
});
