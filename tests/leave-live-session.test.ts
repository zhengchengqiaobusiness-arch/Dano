import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { reviewCatalog, reviewSession } from "../src/review/catalog-review.js";
import { sessionCatalogSlice } from "../src/inference/export-scope.js";
import { exportSkill } from "../src/export/skill-exporter.js";
import { buildApprovedRoutes } from "../src/planner/routes.js";
import { StudioService } from "../src/studio-service.js";
import { writeJson, appendJsonl } from "../src/utils.js";

const PAGE = "http://admin.dianshixinxi.com:90/oa/duty/leave";
const QUERY_URL = "http://admin.dianshixinxi.com:90/admin-api/oa/duty-leave/page?pageNo=1&pageSize=10&type=3&processStatus=0&reason=1";
const CREATE_URL = "http://admin.dianshixinxi.com:90/admin-api/oa/duty-leave/submit-process";

const RECORDED_CREATE = {
  type: 2,
  reason: "123123",
  startTime: 1789401600000,
  endTime: 1789920000000,
  day: 6,
  leaveBalance: 0,
  projectCode: "12312",
  projectName: "123",
  actualStartTime: 1789315200000,
  actualEndTime: 1790611200000,
  actualDay: 123,
  billType: "oa_duty_leave",
  processDefKey: "oa_duty_leave",
  attachments: [] as unknown[],
  startUserSelectAssignees: { Activity_0ag2wyz: [175] }
};

function queryForm() {
  return [
    { name: "type", label: "请假类型", type: "select", value: "年假", options: [
      { value: "1", label: "病假" }, { value: "2", label: "事假" }, { value: "3", label: "年假" }
    ] },
    { name: "createTime[0]", label: "开始日期", type: "date", value: "2026-09-15", rangeIndex: 0 },
    { name: "createTime[1]", label: "结束日期", type: "date", value: "2026-09-17", rangeIndex: 1 },
    { name: "processStatus", label: "审批结果", type: "select", value: "未提交", options: [
      { value: "0", label: "未提交" }, { value: "1", label: "审批中" }
    ] },
    { name: "reason", label: "原因", type: "text", value: "1" }
  ];
}

function createForm() {
  return [
    { name: "type", label: "请假类型", type: "select", value: "事假", required: true, options: [
      { value: "1", label: "病假" }, { value: "2", label: "事假" }, { value: "3", label: "年假" }
    ] },
    { name: "projectCode", label: "请输入项目编码", type: "text", value: "12312" },
    { name: "projectName", label: "请输入项目名称", type: "text", value: "123" },
    { name: "startTime", label: "开始时间", type: "date", value: "2026-09-15", required: true },
    { name: "endTime", label: "结束时间", type: "date", value: "2026-09-21", required: true },
    { name: "day", label: "请假天数", type: "number", value: 6, required: true },
    { name: "actualStartTime", label: "实际开始", type: "date", value: "2026-09-14" },
    { name: "actualEndTime", label: "实际结束", type: "date", value: "2026-09-29" },
    { name: "actualDay", label: "实际天数", type: "number", value: 123 },
    { name: "reason", label: "原因", type: "textarea", value: "123123", required: true },
    { label: "领导审批", type: "picker", value: "管理员" },
    { label: "人力审批", type: "picker", value: "c cs001", required: true }
  ];
}

function liveLeaveEvents(): EvidenceEvent[] {
  return [{
    id: "ui-query", kind: "ui", sessionId: "leave-now", at: "2026-09-03T13:00:00.000Z",
    pageUrl: PAGE, eventType: "snapshot", form: queryForm()
  }, {
    id: "ui-search", kind: "ui", sessionId: "leave-now", at: "2026-09-03T13:00:01.000Z",
    pageUrl: PAGE, eventType: "click", text: "搜索", label: "搜索", form: queryForm()
  }, {
    id: "ui-create", kind: "ui", sessionId: "leave-now", at: "2026-09-03T13:01:00.000Z",
    pageUrl: `${PAGE}apply/create`, eventType: "snapshot", form: createForm()
  }, {
    id: "ui-submit", kind: "ui", sessionId: "leave-now", at: "2026-09-03T13:01:02.000Z",
    pageUrl: `${PAGE}apply/create`, eventType: "click", text: "提交", label: "提交", form: createForm()
  }, {
    id: "net-im", kind: "network", sessionId: "leave-now", at: "2026-09-03T13:00:00.050Z",
    pageUrl: PAGE,
    request: { method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/im/conversation/list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: Array.from({ length: 80 }, (_, index) => ({ id: index, name: `会话${index}` })) } }
  }, {
    id: "net-auth", kind: "network", sessionId: "leave-now", at: "2026-09-03T13:00:00.060Z",
    pageUrl: PAGE,
    request: { method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/system/auth/get-permission-info", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { permissions: ["oa:leave:query"] } } }
  }, {
    id: "net-dict", kind: "network", sessionId: "leave-now", at: "2026-09-03T13:00:00.100Z",
    pageUrl: PAGE,
    request: { method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/system/dict-data/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: [
      { dictType: "oa_duty_leave_type", value: "1", label: "病假" },
      { dictType: "oa_duty_leave_type", value: "2", label: "事假" },
      { dictType: "oa_duty_leave_type", value: "3", label: "年假" }
    ] } }
  }, {
    id: "net-users", kind: "network", sessionId: "leave-now", at: "2026-09-03T13:01:00.200Z",
    pageUrl: `${PAGE}apply/create`,
    request: { method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [
      { id: 175, username: "c", nickname: "cs001" },
      { id: 1, username: "admin", nickname: "管理员" }
    ] } } }
  }, {
    id: "net-balance", kind: "network", sessionId: "leave-now", at: "2026-09-03T13:01:00.400Z",
    pageUrl: `${PAGE}apply/create`,
    request: { method: "GET", url: "http://admin.dianshixinxi.com:90/admin-api/oa/duty-leave/leave-balance/my", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: { leaveBalance: 0 } } }
  }, {
    id: "net-query", kind: "network", sessionId: "leave-now", at: "2026-09-03T13:00:02.000Z",
    pageUrl: PAGE, correlatedUiEvidenceId: "ui-search",
    request: {
      method: "GET",
      url: QUERY_URL,
      resourceType: "xhr",
      headers: {},
      query: { pageNo: "1", pageSize: "10", type: "3", processStatus: "0", reason: "1" }
    },
    response: {
      status: 200,
      headers: {},
      body: { code: 0, data: { list: [{ no: "QJD202609030016", leaveBalance: 0, processStatus: 0 }], total: 1 } }
    }
  }, {
    id: "net-create", kind: "network", sessionId: "leave-now", at: "2026-09-03T13:01:03.000Z",
    pageUrl: `${PAGE}apply/create`, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST",
      url: CREATE_URL,
      resourceType: "xhr",
      headers: {},
      query: {},
      body: RECORDED_CREATE
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 17 } }
  }];
}

function liveCatalog() {
  const events = liveLeaveEvents();
  return { events, catalog: finalizeCapabilities(buildCapabilityCandidates(events), events) };
}

function fieldByName(capability: CapabilityContract, name: string) {
  return capability.inputForm.find(field => field.name === name);
}

function cyclicOtherPage(): CapabilityContract {
  return {
    id: "query-post-qzqdsl-getqzqdsllist",
    kind: "atomic",
    title: "查询三定职能清单",
    description: "其它页残留",
    operation: "query",
    confidence: 1,
    transport: {
      method: "POST",
      urlTemplate: "http://10.255.158.85/appgateway/dcensus/v1.0/qzqdsl/getQzqdSlList",
      origin: "http://10.255.158.85",
      pathTemplate: "/appgateway/dcensus/v1.0/qzqdsl/getQzqdSlList"
    },
    inputSchema: { type: "object", properties: { id: { type: "string" } } },
    outputSchema: { type: "object", properties: { data: { type: "array", items: { type: "object", properties: { id: { type: "string" } } } } } },
    inputForm: [{
      path: "$.id", name: "id", label: "编号", valueType: "string", source: "caller",
      required: false, requiredBasis: "not-observed", systemHandled: false, sourceDetail: "页面", widget: "text"
    }],
    evidence: [{ eventId: "qzqd-net", sessionId: "qzqd-old", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }],
    sideEffect: false,
    confirmation: { required: false },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [{
      id: "bind-qzqd-cycle",
      fromCapabilityId: "query-post-qzqdsl-getqzqdsllist",
      fromPath: "$.data[*].id",
      toPath: "$.id",
      confidence: 1,
      evidenceIds: ["qzqd-net"],
      approved: true,
      approvalSource: "human",
      approvedAt: "2026-09-01T00:00:00.000Z"
    }],
    validation: { version: 2, status: "verified", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-01T00:00:00.000Z" }
  };
}

async function writeSession(
  recordingsDir: string,
  session: { id: string; startUrl: string; startedAt: string },
  events: EvidenceEvent[]
) {
  const dir = path.join(recordingsDir, session.id);
  const eventsFile = path.join(dir, "events.jsonl");
  await writeJson(path.join(dir, "session.json"), { ...session, name: session.id, eventsFile });
  for (const event of events) await appendJsonl(eventsFile, event);
}

test("live leave session binds balance from the balance query, not the list 0 filter", () => {
  const { catalog } = liveCatalog();
  const create = catalog.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.ok(create, catalog.map(item => item.transport.pathTemplate).join(","));
  const balance = fieldByName(create, "leaveBalance");
  assert.ok(balance, "leaveBalance field missing");
  assert.match(balance!.defaultRule || "", /from:.+(leave-balance|get-balance|leaveBalance)/);
  assert.doesNotMatch(balance!.defaultRule || "", /literal:0/);
  assert.equal(balance!.source, "binding");
  assert.match(balance!.sourceDetail || "", /leave-balance\/my|已录制查询/);
});

test("live leave session binds the unnamed 人力审批 picker to Activity_* without freezing 0", () => {
  const { catalog, events } = liveCatalog();
  const create = catalog.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const assignee = fieldByName(create, "Activity_0ag2wyz");
  assert.ok(assignee, "Activity_0ag2wyz missing");
  assert.equal(assignee!.source, "caller");
  assert.equal(assignee!.label, "人力审批");
  assert.equal(assignee!.candidates?.type, "capability");
  assert.match(assignee!.sourceDetail || "", /user\/page|已录制查询/);
  const review = reviewCatalog(catalog, events);
  assert.equal(review.status, "passed", review.summary);
  assert.equal(review.primaryTitles.some(title => /oa\/duty-leave|请假/.test(title)), true);
  assert.equal(review.primaryTitles.some(title => /im|permission|三定|qzqdsl/.test(title)), false);
});

test("a leftover required picker still binds when the selected user is not on the first recorded page", () => {
  const events = liveLeaveEvents().map(event => {
    if (event.id !== "net-users" || event.kind !== "network") return event;
    return {
      ...event,
      response: { status: 200, headers: {}, body: { data: { list: [{ id: 1, username: "admin", nickname: "管理员" }] } } }
    };
  });
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const create = catalog.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const assignee = fieldByName(create, "Activity_0ag2wyz");
  assert.equal(assignee?.source, "caller");
  assert.equal(assignee?.label, "人力审批");
});

test("session slice drops IM and login noise so review does not walk them", () => {
  const { catalog, events } = liveCatalog();
  const slice = sessionCatalogSlice(catalog, events, events);
  assert.equal(slice.some(item => /\/im\/|get-permission-info/.test(item.transport.pathTemplate)), false);
  assert.equal(slice.some(item => item.transport.pathTemplate.includes("submit-process")), true);
  const { review } = reviewSession(slice, events, events);
  assert.equal(review.status, "passed", review.summary);
});

test("exporting the leave page skips another page's cyclic binding instead of failing the whole skill", async () => {
  const { catalog, events } = liveCatalog();
  const mixed = [...catalog, cyclicOtherPage()];
  assert.doesNotThrow(() => buildApprovedRoutes(mixed));
  const temporary = await mkdtemp(path.join(os.tmpdir(), "leave-live-export-"));
  try {
    const result = await exportSkill(temporary, "请假申请", mixed, [], events);
    const skill = await readFile(path.join(result.dir, "SKILL.md"), "utf8");
    const contract = await readFile(path.join(result.dir, "references", "CONTRACT.json"), "utf8");
    assert.doesNotMatch(skill, /qzqdsl|三定职能/);
    assert.doesNotMatch(contract, /qzqdsl|getQzqdSlList/);
    assert.match(contract, /duty-leave|submit-process/);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("studio export and review stay on this leave session and ignore other-page cycles", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "leave-live-studio-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const { catalog, events } = liveCatalog();
  const fatOther: EvidenceEvent[] = [{
    id: "qzqd-net", kind: "network", sessionId: "qzqd-old", at: "2026-09-01T00:00:01.000Z",
    pageUrl: "http://10.255.158.85/app/#/qzqdsl",
    request: { method: "POST", url: "http://10.255.158.85/appgateway/dcensus/v1.0/qzqdsl/getQzqdSlList", resourceType: "xhr", headers: {}, query: {}, body: {} },
    response: { status: 200, headers: {}, body: { data: Array.from({ length: 400 }, (_, index) => ({ id: index, name: `节点${index}`, unitName: "盒" })) } }
  }];
  const merged = catalog.map(item => {
    if (!item.transport.pathTemplate.includes("/user/page")) return item;
    return {
      ...item,
      evidence: [
        ...item.evidence,
        { eventId: "qzqd-net", sessionId: "qzqd-old", kind: "network" as const, at: "2026-09-01T00:00:01.000Z", status: 200 }
      ]
    };
  });
  await writeJson(path.join(catalogDir, "capabilities.json"), [...merged, cyclicOtherPage()] as CapabilityContract[]);
  await writeSession(recordingsDir, { id: "leave-now", startUrl: PAGE, startedAt: "2026-09-03T13:00:00.000Z" }, events);
  await writeSession(recordingsDir, { id: "qzqd-old", startUrl: "http://10.255.158.85/app/#/qzqdsl", startedAt: "2026-09-01T00:00:00.000Z" }, fatOther);
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
  const started = Date.now();
  const { capabilities, review } = await studio.review();
  const elapsed = Date.now() - started;
  assert.ok(elapsed < 2_000, `review took ${elapsed}ms`);
  assert.equal(review.status, "passed", review.summary);
  assert.equal(capabilities.some(item => item.id === "query-post-qzqdsl-getqzqdsllist"), false);
  const create = capabilities.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.match(fieldByName(create, "leaveBalance")?.defaultRule || "", /^from:/);
  assert.equal(fieldByName(create, "Activity_0ag2wyz")?.source, "caller");
  const exported = await studio.exportManaged("请假申请", true);
  const skill = await readFile(path.join(exported.directory, "SKILL.md"), "utf8");
  assert.doesNotMatch(skill, /qzqdsl|三定职能/);
  await rm(temporary, { recursive: true, force: true });
});
