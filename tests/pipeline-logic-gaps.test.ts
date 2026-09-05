import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "os";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { reanalyzeIncoming } from "../src/inference/reanalyze.js";
import {
  capabilitiesForSession,
  exportableCapabilities,
  relatedResource,
  reviewSessionIds,
  sameResource,
  sessionBusinessPageKeys
} from "../src/inference/export-scope.js";
import { finalizeCapabilities, finalizeSessionSlice } from "../src/inference/finalize-capabilities.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { buildApprovedRoutes, collectRouteIssues } from "../src/planner/routes.js";
import { StudioService } from "../src/studio-service.js";
import { writeJson, appendJsonl } from "../src/utils.js";

const PORTAL = "https://example.test/app/#/Home";
const ORDER_PAGE = "https://example.test/erp/order";
const LEAVE_PAGE = "https://example.test/oa/duty/leave";

function cap(partial: Partial<CapabilityContract> & Pick<CapabilityContract, "id" | "operation" | "transport">): CapabilityContract {
  const sideEffect = ["create", "update", "review", "delete", "upload", "action"].includes(partial.operation);
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
    generated: { source: "heuristic", generatedAt: "2026-09-04T00:00:00.000Z" },
    ...partial
  };
}

function network(id: string, sessionId: string, pageUrl: string, method: string, url: string, extra: Partial<EvidenceEvent> = {}): EvidenceEvent {
  return {
    id,
    kind: "network",
    sessionId,
    at: "2026-09-04T00:00:00.000Z",
    pageUrl,
    request: { method, url, resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true } },
    ...extra
  } as EvidenceEvent;
}

async function writeSession(
  recordingsDir: string,
  session: { id: string; startUrl: string; startedAt: string; pageKeys?: string[] },
  events: EvidenceEvent[]
) {
  const dir = path.join(recordingsDir, session.id);
  const eventsFile = path.join(dir, "events.jsonl");
  await writeJson(path.join(dir, "session.json"), { ...session, name: session.id, eventsFile });
  for (const event of events) await appendJsonl(eventsFile, event);
}

function studioOf(temporary: string, recordingsDir: string, catalogDir: string) {
  return new StudioService({
    rootDir: temporary,
    dataDir: temporary,
    recordingsDir,
    catalogDir,
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
}

test("same resource does not treat sibling names under a version prefix as one family", () => {
  assert.equal(sameResource("/oa/duty-leave/page", "/oa/duty-leave/submit-process"), true);
  assert.equal(sameResource("/erp/purchase-order/page", "/erp/purchase-order/create"), true);
  assert.equal(sameResource("/system/user/page", "/system/user/update-status"), true);
  assert.equal(sameResource("/dcensus/v1.0/qzqdsl/getQzqdSlList", "/dcensus/v1.0/qzqdsl/createQzqdSl"), true);
  assert.equal(sameResource("/api/v1/orders", "/api/v1/users"), false);
  assert.equal(sameResource("/erp/foo/bar", "/erp/foo/baz"), false);
  assert.equal(sameResource("/oa/duty-leave/page", "/erp/purchase-order/create"), false);
  assert.equal(relatedResource("/oa/duty-leave/submit-process", "/oa/duty-leave/leave-balance/my"), true);
  assert.equal(relatedResource("/oa/duty-leave/submit-process", "/oa/duty-leave/get-leave-balance-my"), true);
  assert.equal(relatedResource("/erp/purchase/order/create", "/erp/purchase/product/simple-list"), true);
  assert.equal(relatedResource("/oa/duty-leave/submit-process", "/system/user/page"), false);
  assert.equal(relatedResource("/api/v1/orders", "/api/v1/users"), false);
});

test("exportable catalog drops a verified query that only shares an API version prefix with the write", () => {
  const write = cap({
    id: "create-order",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/api/v1/orders", origin: "https://x", pathTemplate: "/api/v1/orders" }
  });
  const otherQuery = cap({
    id: "query-users",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/api/v1/users", origin: "https://x", pathTemplate: "/api/v1/users" },
    inputForm: [{
      path: "$.name", name: "name", label: "姓名", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
    }]
  });
  assert.deepEqual(exportableCapabilities([write, otherQuery]).map(item => item.id), ["create-order"]);
});

test("portal startUrl does not pull another menu's recording into this page's review set", () => {
  const currentEvents = [
    network("nav-home", "leave-now", PORTAL, "GET", "https://example.test/im/unread-count"),
    network("leave-net", "leave-now", LEAVE_PAGE, "GET", "https://example.test/oa/duty-leave/page")
  ];
  const ids = reviewSessionIds([
    { id: "leave-now", startUrl: PORTAL },
    { id: "purchase-old", startUrl: PORTAL },
    { id: "leave-old-direct", startUrl: LEAVE_PAGE },
    { id: "leave-old-portal", startUrl: PORTAL, pageKeys: [LEAVE_PAGE] },
    { id: "purchase-keyed", startUrl: PORTAL, pageKeys: [ORDER_PAGE] }
  ], "leave-now", currentEvents);
  assert.equal(ids.has("leave-now"), true);
  assert.equal(ids.has("leave-old-direct"), false);
  assert.equal(ids.has("leave-old-portal"), false);
  assert.equal(ids.has("purchase-old"), false);
  assert.equal(ids.has("purchase-keyed"), false);
  assert.deepEqual(sessionBusinessPageKeys(currentEvents, PORTAL), [LEAVE_PAGE]);
});

test("empty session events do not fall back to the whole catalog", () => {
  const other = cap({
    id: "create-order",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/erp/order/create", origin: "https://x", pathTemplate: "/erp/order/create" },
    evidence: [{ eventId: "purchase-net", sessionId: "p", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }]
  });
  assert.deepEqual(capabilitiesForSession([other], [], []).map(item => item.id), []);
});

test("reanalyze keeps verified lookup identity and previously verified fields", () => {
  const existingLookup = cap({
    id: "query-user",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/system/user/page", origin: "https://x", pathTemplate: "/system/user/page" },
    inputForm: [{
      path: "$.username", name: "username", label: "用户名", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "采购页", widget: "text"
    }],
    validation: { version: 2, status: "verified", checks: [{ name: "recorded-network-evidence", ok: true, detail: "ok" }] }
  });
  const incomingLookup = cap({
    id: "query-user-again",
    operation: "query",
    transport: existingLookup.transport,
    inputForm: [{
      path: "$.nickname", name: "nickname", label: "昵称", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "请假页", widget: "text"
    }],
    validation: { version: 2, status: "candidate", checks: [] }
  });
  const incomingCreate = cap({
    id: "create-leave-again",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/oa/duty-leave/submit-process", origin: "https://x", pathTemplate: "/oa/duty-leave/submit-process" },
    validation: { version: 2, status: "candidate", checks: [] }
  });
  const next = reanalyzeIncoming([incomingCreate, incomingLookup], [existingLookup]);
  const kept = next.find(item => item.transport.pathTemplate.includes("/system/user/page"))!;
  assert.equal(kept.id, "query-user");
  assert.equal(kept.validation.status, "verified");
  assert.equal(kept.inputForm.some(field => field.name === "username" && field.sourceDetail === "采购页"), true);
  assert.equal(kept.inputForm.some(field => field.name === "nickname"), true);
});

test("cyclic approved bindings skip that route but report why", () => {
  const left = cap({
    id: "query-a",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/a", origin: "https://x", pathTemplate: "/a" },
    bindings: [{
      id: "bind-b-to-a", fromCapabilityId: "query-b", fromPath: "$.id", toPath: "$.id",
      confidence: 1, evidenceIds: [], approved: true, approvalSource: "human", approvedAt: "2026-09-04T00:00:00.000Z"
    }]
  });
  const right = cap({
    id: "query-b",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/b", origin: "https://x", pathTemplate: "/b" },
    bindings: [{
      id: "bind-a-to-b", fromCapabilityId: "query-a", fromPath: "$.id", toPath: "$.id",
      confidence: 1, evidenceIds: [], approved: true, approvalSource: "human", approvedAt: "2026-09-04T00:00:00.000Z"
    }]
  });
  assert.deepEqual(buildApprovedRoutes([left, right]), []);
  const issues = collectRouteIssues([left, right]);
  assert.equal(issues.length > 0, true);
  assert.match(issues.map(item => item.reason).join(" "), /循环|缺失/);
});

test("finalize on export verifies this page's candidate without unverifying a lookup recorded elsewhere", () => {
  const query = cap({
    id: "query-leave",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/oa/duty-leave/page", origin: "https://x", pathTemplate: "/oa/duty-leave/page" },
    inputForm: [{
      path: "$.reason", name: "reason", label: "原因", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
    }],
    evidence: [
      { eventId: "leave-ui", sessionId: "leave", kind: "ui", at: "2026-09-04T00:00:00.000Z" },
      { eventId: "leave-query", sessionId: "leave", kind: "network", at: "2026-09-04T00:00:00.000Z", status: 200 }
    ],
    validation: { version: 2, status: "candidate", checks: [{ name: "reanalyze", ok: false, detail: "需要再次验证" }] }
  });
  const lookup = cap({
    id: "query-user",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/system/user/page", origin: "https://x", pathTemplate: "/system/user/page" },
    inputForm: [{
      path: "$.username", name: "username", label: "用户", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "采购页", widget: "text"
    }],
    evidence: [{ eventId: "user-net", sessionId: "purchase", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }],
    validation: { version: 2, status: "verified", checks: [{ name: "recorded-network-evidence", ok: true, detail: "ok" }] }
  });
  const events: EvidenceEvent[] = [{
    id: "leave-ui", kind: "ui", sessionId: "leave", at: "2026-09-04T00:00:00.000Z",
    pageUrl: LEAVE_PAGE, eventType: "input",
    form: [{ name: "reason", label: "原因", type: "text", value: "事假" }]
  },
    network("leave-query", "leave", LEAVE_PAGE, "GET", "https://x/oa/duty-leave/page", {
      request: { method: "GET", url: "https://x/oa/duty-leave/page", resourceType: "xhr", headers: {}, query: { reason: "事假" } },
      response: { status: 200, headers: {}, body: { data: { list: [] } } }
    } as Partial<EvidenceEvent>)
  ];
  const next = finalizeSessionSlice([query, lookup], events, [query, lookup]);
  assert.equal(next.find(item => item.id === "query-leave")?.validation.status, "verified");
  const kept = next.find(item => item.id === "query-user")!;
  assert.equal(kept.validation.status, "verified");
  assert.equal(kept.inputForm[0]?.sourceDetail, "采购页");
});

test("review after restart uses the latest recording, not a leftover analyzed page", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "pipeline-persist-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const leaveEvents: EvidenceEvent[] = [
    network("leave-query", "leave", LEAVE_PAGE, "GET", "https://x/oa/duty-leave/page", {
      request: { method: "GET", url: "https://x/oa/duty-leave/page", resourceType: "xhr", headers: {}, query: { reason: "1" } },
      response: { status: 200, headers: {}, body: { data: { list: [] } } }
    } as Partial<EvidenceEvent>)
  ];
  const otherEvents: EvidenceEvent[] = [
    network("other-net", "other-new", ORDER_PAGE, "GET", "https://x/erp/purchase-order/page")
  ];
  const leaveQuery = cap({
    id: "query-leave",
    operation: "query",
    title: "查询请假",
    transport: { method: "GET", urlTemplate: "https://x/oa/duty-leave/page", origin: "https://x", pathTemplate: "/oa/duty-leave/page" },
    inputForm: [{
      path: "$.reason", name: "reason", label: "原因", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text",
      candidates: { type: "static", values: [{ value: "1", label: "1" }] }
    }],
    evidence: [{ eventId: "leave-query", sessionId: "leave", kind: "network", at: "2026-09-04T00:00:00.000Z", status: 200 }]
  });
  const purchaseQuery = cap({
    id: "query-purchase",
    operation: "query",
    title: "查询采购",
    transport: { method: "GET", urlTemplate: "https://x/erp/purchase-order/page", origin: "https://x", pathTemplate: "/erp/purchase-order/page" },
    evidence: [{ eventId: "other-net", sessionId: "other-new", kind: "network", at: "2026-09-04T01:00:00.000Z", status: 200 }]
  });
  await writeJson(path.join(catalogDir, "capabilities.json"), [leaveQuery, purchaseQuery]);
  await writeSession(recordingsDir, { id: "leave", startUrl: LEAVE_PAGE, startedAt: "2026-09-03T00:00:00.000Z" }, leaveEvents);
  await writeSession(recordingsDir, { id: "other-new", startUrl: ORDER_PAGE, startedAt: "2026-09-04T00:00:00.000Z" }, otherEvents);
  const first = studioOf(temporary, recordingsDir, catalogDir);
  await first.analyze("leave", false);
  const restarted = studioOf(temporary, recordingsDir, catalogDir);
  const { capabilities } = await restarted.review();
  assert.equal(capabilities.some(item => item.transport.pathTemplate.includes("duty-leave")), false);
  assert.equal(capabilities.some(item => item.transport.pathTemplate.includes("purchase-order")), true);
  await rm(temporary, { recursive: true, force: true });
});

test("analyze then export finalizes this page so the query is not dropped", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "pipeline-export-finalize-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "order-now", at: "2026-09-04T12:00:00.000Z",
    pageUrl: ORDER_PAGE, eventType: "input",
    form: [
      { name: "productId", label: "产品", type: "select", required: true, value: "苹果" },
      { name: "count", label: "数量", type: "number", required: true, value: 2 }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "order-now", at: "2026-09-04T12:00:01.000Z",
    pageUrl: ORDER_PAGE, eventType: "click", text: "确定", label: "确定"
  }, {
    id: "net-product", kind: "network", sessionId: "order-now", at: "2026-09-04T12:00:00.500Z",
    pageUrl: ORDER_PAGE,
    request: { method: "GET", url: "https://example.test/admin-api/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果" }] } }
  }, {
    id: "net-page", kind: "network", sessionId: "order-now", at: "2026-09-04T12:00:00.200Z",
    pageUrl: ORDER_PAGE,
    request: { method: "GET", url: "https://example.test/admin-api/order/page", resourceType: "xhr", headers: {}, query: { productId: "9" } },
    response: { status: 200, headers: {}, body: { success: true, data: { list: [] } } }
  }, {
    id: "net-create", kind: "network", sessionId: "order-now", at: "2026-09-04T12:00:02.000Z",
    pageUrl: ORDER_PAGE, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/order/create", resourceType: "xhr", headers: {}, query: {},
      body: { productId: 9, count: 2 }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }];
  await writeSession(recordingsDir, { id: "order-now", startUrl: ORDER_PAGE, startedAt: "2026-09-04T12:00:00.000Z" }, events);
  await writeJson(path.join(catalogDir, "capabilities.json"), []);
  const studio = studioOf(temporary, recordingsDir, catalogDir);
  await studio.analyze("order-now", false);
  const result = await studio.export("订单", path.join(temporary, "dist", "skills"));
  const skill = await readFile(path.join(result.dir, "SKILL.md"), "utf8");
  const contract = await readFile(path.join(result.dir, "references", "CONTRACT.json"), "utf8");
  assert.match(skill, /查询|新建|订单/);
  assert.match(contract, /\/order\/page|\/order\/create/);
  assert.match(contract, /"status": "verified"/);
  await rm(temporary, { recursive: true, force: true });
});

test("from-rule matching indexes fat lookup lists instead of rescanning every field", () => {
  const rows = Array.from({ length: 800 }, (_, index) => ({
    id: index + 1,
    name: `商品${index}`,
    unitName: "件"
  }));
  rows[8] = { id: 9, name: "苹果", unitName: "盒" };
  const events: EvidenceEvent[] = [{
    id: "ui-form", kind: "ui", sessionId: "order-now", at: "2026-09-04T12:00:00.000Z",
    pageUrl: ORDER_PAGE, eventType: "input",
    form: [
      { name: "productId", label: "产品", type: "select", required: true, value: "苹果" },
      { name: "count", label: "数量", type: "number", required: true, value: 2 },
      { name: "productPrice", label: "单价", type: "number", required: true, value: 10 },
      { name: "unitName", label: "单位", type: "readonly", value: "盒" }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "order-now", at: "2026-09-04T12:00:01.000Z",
    pageUrl: ORDER_PAGE, eventType: "click", text: "确定", label: "确定"
  }, {
    id: "net-product", kind: "network", sessionId: "order-now", at: "2026-09-04T12:00:00.500Z",
    pageUrl: ORDER_PAGE,
    request: { method: "GET", url: "https://example.test/admin-api/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: rows } }
  }, {
    id: "net-create", kind: "network", sessionId: "order-now", at: "2026-09-04T12:00:02.000Z",
    pageUrl: ORDER_PAGE, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/order/create", resourceType: "xhr", headers: {}, query: {},
      body: { productId: 9, count: 2, productPrice: 10, unitName: "盒", amount: 20 }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }];
  const started = Date.now();
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const elapsed = Date.now() - started;
  const create = catalog.find(item => item.transport.pathTemplate.includes("/order/create"))!;
  assert.match(create.inputForm.find(field => field.name === "unitName")?.defaultRule || "", /^(from:.+|literal:)/);
  assert.ok(elapsed < 400, `finalize on 800-row lookup took ${elapsed}ms`);
});

test("export after a passed review does not reload other-page history or rewrite the catalog", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "pipeline-export-fast-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const current = [{
    id: "ui-form", kind: "ui", sessionId: "order-now", at: "2026-09-04T12:00:00.000Z",
    pageUrl: ORDER_PAGE, eventType: "input",
    form: [
      { name: "productId", label: "产品", type: "select", required: true, value: "苹果" },
      { name: "count", label: "数量", type: "number", required: true, value: 2 },
      { name: "unitName", label: "单位", type: "readonly", value: "盒" }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "order-now", at: "2026-09-04T12:00:01.000Z",
    pageUrl: ORDER_PAGE, eventType: "click", text: "确定", label: "确定"
  }, {
    id: "net-product", kind: "network", sessionId: "order-now", at: "2026-09-04T12:00:00.500Z",
    pageUrl: ORDER_PAGE,
    request: { method: "GET", url: "https://example.test/admin-api/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果", unitName: "盒" }] } }
  }, {
    id: "net-create", kind: "network", sessionId: "order-now", at: "2026-09-04T12:00:02.000Z",
    pageUrl: ORDER_PAGE, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/order/create", resourceType: "xhr", headers: {}, query: {},
      body: { productId: 9, count: 2, unitName: "盒" }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }] as EvidenceEvent[];
  const catalog = finalizeCapabilities(buildCapabilityCandidates(current), current);
  await writeJson(path.join(catalogDir, "capabilities.json"), catalog);
  await writeSession(recordingsDir, { id: "order-now", startUrl: ORDER_PAGE, startedAt: "2026-09-04T12:00:00.000Z" }, current);
  const studio = studioOf(temporary, recordingsDir, catalogDir);
  await studio.review("order-now");
  const started = Date.now();
  const result = await studio.export("订单", path.join(temporary, "dist", "skills"), [], "order-now");
  const elapsed = Date.now() - started;
  assert.ok(result.dir);
  assert.ok(elapsed < 300, `export after review took ${elapsed}ms`);
  await rm(temporary, { recursive: true, force: true });
});
