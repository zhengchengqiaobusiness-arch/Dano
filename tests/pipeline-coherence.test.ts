import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { exportSkill } from "../src/export/skill-exporter.js";
import { buildInputForms } from "../src/export/skill-handbook.js";
import { reanalyzeIncoming } from "../src/inference/reanalyze.js";
import { exportableCapabilities } from "../src/inference/export-scope.js";
import { StudioService } from "../src/studio-service.js";
import { writeJson, appendJsonl } from "../src/utils.js";

function cap(partial: Partial<CapabilityContract> & Pick<CapabilityContract, "id" | "operation" | "transport">): CapabilityContract {
  const sideEffect = ["create", "update", "review", "delete", "upload"].includes(partial.operation);
  return {
    kind: "atomic",
    title: partial.title || partial.id,
    description: partial.description || partial.id,
    confidence: 1,
    inputSchema: { type: "object", properties: {} },
    outputSchema: { type: "object", properties: {} },
    inputForm: [],
    evidence: [],
    sideEffect,
    confirmation: { required: sideEffect },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "verified", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-01T00:00:00.000Z" },
    ...partial
  };
}

const leaveQuery = cap({
  id: "query-leave",
  title: "查询请假",
  operation: "query",
  transport: { method: "GET", urlTemplate: "https://x/oa/duty-leave/page", origin: "https://x", pathTemplate: "/oa/duty-leave/page" },
  inputForm: [{
    path: "$.reason", name: "reason", label: "原因", valueType: "string", source: "caller",
    required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
  }],
  evidence: [{ eventId: "leave-query", sessionId: "leave", kind: "network", at: "2026-09-03T00:00:00.000Z", status: 200 }]
});

const leaveCreate = cap({
  id: "create-leave",
  title: "新建请假",
  operation: "create",
  transport: { method: "POST", urlTemplate: "https://x/oa/duty-leave/submit-process", origin: "https://x", pathTemplate: "/oa/duty-leave/submit-process" },
  inputForm: [{
    path: "$.reason", name: "reason", label: "原因", valueType: "string", source: "caller",
    required: true, requiredBasis: "ui-required", systemHandled: false, sourceDetail: "页面", widget: "text"
  }, {
    path: "$.startTime", name: "startTime", label: "开始时间", valueType: "integer", source: "caller",
    required: true, requiredBasis: "ui-required", systemHandled: false,
    sourceDetail: "页面按 YYYY-MM-DD 填写，执行器转成当天 00:00 的毫秒时间戳", widget: "date"
  }, {
    path: "$.leaveBalance", name: "leaveBalance", label: "假期余额", valueType: "number", source: "binding",
    required: false, requiredBasis: "not-observed", systemHandled: true,
    defaultRule: "from:query-balance:$.data.leaveBalance|via:type",
    sourceDetail: "选择「请假类型」后，从已录制查询 GET /oa/duty-leave/get-balance 的 $.data.leaveBalance 带出，调用方不要手填",
    widget: "number"
  }, {
    path: "$.billType", name: "billType", label: "单据类型", valueType: "string", source: "fixed",
    required: false, requiredBasis: "not-observed", systemHandled: true,
    defaultRule: "literal:oa_duty_leave",
    sourceDetail: "系统默认值 oa_duty_leave，调用方未提供时使用，不是某次录制的业务样本",
    widget: "text"
  }],
  evidence: [{ eventId: "leave-create", sessionId: "leave", kind: "network", at: "2026-09-03T00:00:01.000Z", status: 200 }]
});

const balanceLookup = cap({
  id: "query-balance",
  title: "查询假期余额",
  operation: "query",
  transport: { method: "GET", urlTemplate: "https://x/oa/duty-leave/get-balance", origin: "https://x", pathTemplate: "/oa/duty-leave/get-balance" },
  evidence: [{ eventId: "leave-balance", sessionId: "leave", kind: "network", at: "2026-09-03T00:00:00.500Z", status: 200 }]
});

const otherCreate = cap({
  id: "create-purchase",
  title: "新建采购订单",
  operation: "create",
  transport: { method: "POST", urlTemplate: "https://x/erp/purchase-order/create", origin: "https://x", pathTemplate: "/erp/purchase-order/create" },
  inputForm: [{
    path: "$.productUnitName", name: "productUnitName", label: "单位", valueType: "string",
    source: "system", required: false, requiredBasis: "not-observed", systemHandled: true,
    sourceDetail: "页面只读展示，但已录制查询里没有唯一带出路径，不能把录制样本当成固定值", widget: "text"
  }],
  evidence: [{ eventId: "purchase-net", sessionId: "purchase", kind: "network", at: "2026-09-02T00:00:00.000Z", status: 200 }],
  validation: {
    version: 2,
    status: "candidate",
    checks: [{ name: "write-field-origins-resolved", ok: false, detail: "存在无法唯一对应来源的写字段" }]
  }
});

test("export is not blocked by another page's unverified write", async () => {
  const mixed = [leaveQuery, leaveCreate, balanceLookup, otherCreate];
  assert.equal(exportableCapabilities(mixed).some(item => item.id === "create-purchase"), false);
  const temporary = await mkdtemp(path.join(os.tmpdir(), "pipeline-export-"));
  try {
    const result = await exportSkill(temporary, "请假申请", mixed);
    const skill = await readFile(path.join(result.dir, "SKILL.md"), "utf8");
    const contract = await readFile(path.join(result.dir, "references", "CONTRACT.json"), "utf8");
    assert.match(skill, /查询请假|新建请假/);
    assert.doesNotMatch(skill, /采购订单/);
    assert.match(contract, /leaveBalance/);
    assert.match(contract, /sourceDetail/);
    assert.match(contract, /get-balance/);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("handbook keeps recorded date millis and from-rule evidence wording", () => {
  const forms = buildInputForms([leaveQuery, leaveCreate, balanceLookup]);
  assert.match(forms, /毫秒时间戳/);
  assert.doesNotMatch(forms, /按当天开始时间提交/);
  assert.match(forms, /GET \/oa\/duty-leave\/get-balance/);
  assert.match(forms, /\$\.data\.leaveBalance/);
  assert.match(forms, /from:query-balance:\$\.data\.leaveBalance\\?\|via:type/);
  assert.doesNotMatch(forms, /从已录制查询带出(?!（)/);
});

test("reanalyze keeps verified shared lookups and resets this page's primaries", () => {
  const userLookup = cap({
    id: "query-user",
    title: "查询用户",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/system/user/page", origin: "https://x", pathTemplate: "/system/user/page" },
    inputForm: [{
      path: "$.username", name: "username", label: "用户", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
    }],
    validation: { version: 2, status: "verified", checks: [{ name: "recorded-network-evidence", ok: true, detail: "ok" }] }
  });
  const existing = [
    userLookup,
    cap({
      ...leaveCreate,
      validation: { version: 2, status: "verified", checks: [{ name: "recorded-network-evidence", ok: true, detail: "ok" }] }
    })
  ];
  const incoming = [
    { ...leaveQuery, validation: { version: 2, status: "candidate" as const, checks: [] } },
    { ...leaveCreate, id: "create-leave-again", validation: { version: 2, status: "candidate" as const, checks: [] } },
    { ...userLookup, id: "query-user-again", validation: { version: 2, status: "candidate" as const, checks: [] } }
  ];
  const next = reanalyzeIncoming(incoming, existing);
  assert.equal(next.find(item => item.transport.pathTemplate.includes("submit-process"))?.validation.status, "candidate");
  assert.equal(next.find(item => item.transport.pathTemplate.includes("/system/user/page"))?.validation.status, "verified");
});

test("reanalyze drops impossible self-bindings when a transport is reclassified", () => {
  const old = cap({
    id: "query-post-seal",
    operation: "query",
    transport: { method: "POST", urlTemplate: "https://x/oa/seal", origin: "https://x", pathTemplate: "/oa/seal" },
    bindings: [{
      id: "self",
      fromCapabilityId: "query-post-seal",
      fromPath: "$.data.billType",
      toPath: "$.billType",
      confidence: 1,
      evidenceIds: ["net-create"],
      approved: true
    }],
    inputForm: [{
      path: "$.billType", name: "billType", label: "billType", valueType: "string", source: "binding",
      required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "旧的错误自绑定",
      widget: "text", defaultRule: "from:query-post-seal:$.data.billType"
    }]
  });
  const incoming = cap({
    id: "create-post-seal",
    operation: "create",
    transport: old.transport,
    inputForm: [{
      path: "$.billType", name: "billType", label: "billType", valueType: "string", source: "system",
      required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "录制常量",
      widget: "text", defaultRule: "literal:seal_apply"
    }]
  });
  const [next] = reanalyzeIncoming([incoming], [old]);
  assert.equal(next?.operation, "create");
  assert.deepEqual(next?.bindings, []);
  assert.equal(next?.inputForm[0]?.defaultRule, "literal:seal_apply");
});

test("reanalyze replaces stale evidence bindings on a primary while preserving explicit human bindings", () => {
  const transport = {
    method: "POST",
    urlTemplate: "https://x/erp/purchase-order/create",
    origin: "https://x",
    pathTemplate: "/erp/purchase-order/create"
  };
  const old = cap({
    id: "create-purchase-old",
    operation: "create",
    transport,
    inputForm: [{
      path: "$.discountPercent", name: "discountPercent", label: "优惠率", valueType: "number", source: "binding",
      required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "旧的自动误判",
      widget: "number", defaultRule: "from:query-tenant:$.data[*].accountCount"
    }, {
      path: "$.reviewerId", name: "reviewerId", label: "审核人", valueType: "integer", source: "binding",
      required: false, requiredBasis: "manual", systemHandled: true, sourceDetail: "人工确认",
      widget: "select", defaultRule: "from:query-user:$.data[*].id"
    }, {
      path: "$.items[*].unitName", name: "unitName", label: "单位", valueType: "string", source: "binding",
      required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "旧带出",
      widget: "text", defaultRule: "from:query-product:$.data[*].unitName"
    }],
    bindings: [{
      id: "bad-auto", fromCapabilityId: "query-tenant", fromPath: "$.data[*].accountCount", toPath: "$.discountPercent",
      confidence: 1, evidenceIds: ["old-tenant"], approved: true, approvalSource: "evidence"
    }, {
      id: "human-reviewer", fromCapabilityId: "query-user", fromPath: "$.data[*].id", toPath: "$.reviewerId",
      confidence: 1, evidenceIds: [], approved: true, approvalSource: "human"
    }],
    validation: { version: 2, status: "verified", checks: [] },
    editing: { title: "generated", description: "generated", operation: "generated", fields: "generated" }
  });
  const incoming = cap({
    id: "create-purchase-new",
    operation: "create",
    transport,
    inputForm: [{
      path: "$.discountPercent", name: "discountPercent", label: "优惠率（%）", valueType: "number", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "真实页面输入", widget: "number"
    }, {
      path: "$.reviewerId", name: "reviewerId", label: "审核人", valueType: "integer", source: "system",
      required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "待人工绑定", widget: "select"
    }, {
      path: "$.items[*].unitName", name: "unitName", label: "单位", valueType: "string", source: "binding",
      required: false, requiredBasis: "not-observed", systemHandled: true, sourceDetail: "按产品关联带出",
      widget: "text", defaultRule: "from:query-product:$.data[*].unitName|via:productId"
    }],
    bindings: [{
      id: "fresh-unit", fromCapabilityId: "query-product", fromPath: "$.data[*].unitName", toPath: "$.items[*].unitName",
      confidence: 1, evidenceIds: ["new-product"], approved: true, approvalSource: "evidence"
    }],
    validation: { version: 2, status: "candidate", checks: [] }
  });

  const [next] = reanalyzeIncoming([incoming], [old]);
  const discount = next?.inputForm.find(field => field.path === "$.discountPercent");
  const reviewer = next?.inputForm.find(field => field.path === "$.reviewerId");
  const unit = next?.inputForm.find(field => field.path === "$.items[*].unitName");
  assert.equal(discount?.source, "caller");
  assert.equal(discount?.defaultRule, undefined);
  assert.equal(next?.bindings.some(binding => binding.fromPath.endsWith("accountCount")), false);
  assert.equal(reviewer?.defaultRule, "from:query-user:$.data[*].id");
  assert.equal(next?.bindings.some(binding => binding.id === "human-reviewer"), true);
  assert.equal(unit?.defaultRule, "from:query-product:$.data[*].unitName|via:productId");
  assert.equal(next?.bindings.some(binding => binding.id === "fresh-unit"), true);
});

test("review follows the analyzed session instead of a newer unrelated recording", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "pipeline-review-session-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const leaveEvents: EvidenceEvent[] = [{
    id: "leave-query", kind: "network", sessionId: "leave", at: "2026-09-03T00:00:00.000Z",
    pageUrl: "https://x/oa/duty/leave",
    request: { method: "GET", url: "https://x/oa/duty-leave/page", resourceType: "xhr", headers: {}, query: { reason: "1" } },
    response: { status: 200, headers: {}, body: { data: { list: [] } } }
  }, {
    id: "leave-create", kind: "network", sessionId: "leave", at: "2026-09-03T00:00:01.000Z",
    pageUrl: "https://x/oa/duty/leave",
    request: { method: "POST", url: "https://x/oa/duty-leave/submit-process", resourceType: "xhr", headers: {}, query: {}, body: { reason: "1" } },
    response: { status: 200, headers: {}, body: { success: true } }
  }];
  const otherEvents: EvidenceEvent[] = [{
    id: "other-net", kind: "network", sessionId: "other-new", at: "2026-09-04T00:00:00.000Z",
    pageUrl: "https://x/erp/purchase/order",
    request: { method: "GET", url: "https://x/erp/purchase-order/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [] } } }
  }];
  const purchaseQuery = cap({
    id: "query-purchase",
    title: "查询采购订单",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/erp/purchase-order/page", origin: "https://x", pathTemplate: "/erp/purchase-order/page" },
    inputForm: [{
      path: "$.no", name: "no", label: "单号", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
    }],
    evidence: [{ eventId: "other-net", sessionId: "other-new", kind: "network", at: "2026-09-04T00:00:00.000Z", status: 200 }]
  });
  await writeJson(path.join(catalogDir, "capabilities.json"), [leaveQuery, leaveCreate, balanceLookup, purchaseQuery]);
  const writeSession = async (session: { id: string; startUrl: string; startedAt: string }, events: EvidenceEvent[]) => {
    await writeJson(path.join(recordingsDir, session.id, "session.json"), { ...session, name: session.id, eventsFile: path.join(recordingsDir, session.id, "events.jsonl") });
    for (const event of events) await appendJsonl(path.join(recordingsDir, session.id, "events.jsonl"), event);
  };
  await writeSession({ id: "leave", startUrl: "https://x/oa/duty/leave", startedAt: "2026-09-03T00:00:00.000Z" }, leaveEvents);
  await writeSession({ id: "other-new", startUrl: "https://x/erp/purchase/order", startedAt: "2026-09-04T00:00:00.000Z" }, otherEvents);
  const studio = new StudioService({
    rootDir: temporary,
    dataDir: temporary,
    recordingsDir,
    catalogDir,
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  await studio.analyze("leave", false);
  const { capabilities } = await studio.review();
  assert.equal(capabilities.some(item => item.transport.pathTemplate.includes("duty-leave")), true);
  assert.equal(capabilities.some(item => item.transport.pathTemplate.includes("purchase-order")), false);
  await rm(temporary, { recursive: true, force: true });
});
