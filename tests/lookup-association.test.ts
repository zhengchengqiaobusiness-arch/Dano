import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import os from "os";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { relatedLookupCapabilities, sessionCatalogSlice } from "../src/inference/export-scope.js";
import { StudioService } from "../src/studio-service.js";
import { writeJson, appendJsonl } from "../src/utils.js";

const PAGE = "http://admin.example.test/oa/duty/leave";
const CREATE_URL = "http://admin.example.test/admin-api/oa/duty-leave/submit-process";
const LIST_URL = "http://admin.example.test/admin-api/oa/duty-leave/page?pageNo=1&pageSize=10";
const BALANCE_URL = "http://admin.example.test/admin-api/oa/duty-leave/leave-balance/my?leaveType=1";

function fieldByName(capability: CapabilityContract, name: string) {
  return capability.inputForm.find(field => field.name === name);
}

function writeEvents(balanceBody: unknown): EvidenceEvent[] {
  return [{
    id: "ui-create", kind: "ui", sessionId: "leave-now", at: "2026-09-04T04:00:00.000Z",
    pageUrl: `${PAGE}apply/create`, eventType: "snapshot",
    form: [
      { name: "type", label: "请假类型", type: "select", required: true, value: "病假" },
      { name: "day", label: "请假天数", type: "number", required: true, value: 1 },
      { name: "reason", label: "原因", type: "textarea", required: true, value: "样例-原因" }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "leave-now", at: "2026-09-04T04:00:02.000Z",
    pageUrl: `${PAGE}apply/create`, eventType: "click", text: "提交", label: "提交"
  }, {
    id: "net-balance", kind: "network", sessionId: "leave-now", at: "2026-09-04T04:00:00.400Z",
    pageUrl: `${PAGE}apply/create`,
    request: { method: "GET", url: BALANCE_URL, resourceType: "xhr", headers: {}, query: { leaveType: "1" } },
    response: { status: 200, headers: {}, body: balanceBody }
  }, {
    id: "net-list", kind: "network", sessionId: "leave-now", at: "2026-09-04T04:00:00.200Z",
    pageUrl: PAGE,
    request: { method: "GET", url: LIST_URL, resourceType: "xhr", headers: {}, query: { pageNo: "1", pageSize: "10" } },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [{ no: "QJD1", leaveBalance: 0 }], total: 1 } } }
  }, {
    id: "net-create", kind: "network", sessionId: "leave-now", at: "2026-09-04T04:00:03.000Z",
    pageUrl: `${PAGE}apply/create`, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: CREATE_URL, resourceType: "xhr", headers: {}, query: {},
      body: { type: 1, day: 1, reason: "样例-原因", leaveBalance: 0, billType: "oa_duty_leave" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 17 } }
  }];
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

test("a same-resource scalar lookup binds a write field even when response key names differ", () => {
  const events = writeEvents({ success: true, data: { remainingDays: 0 } });
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const create = catalog.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const balance = fieldByName(create, "leaveBalance");
  assert.match(balance?.defaultRule || "", /^from:.+remainingDays/);
  assert.doesNotMatch(balance?.defaultRule || "", /literal:0|\/page/);
  assert.equal(balance?.source, "binding");
});

test("a uniquely related lookup still binds when the recorded write sample is a stale zero", () => {
  const events = writeEvents({ success: true, data: { remainingDays: 8 } });
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const create = catalog.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.match(fieldByName(create, "leaveBalance")?.defaultRule || "", /^from:.+remainingDays/);
});

test("catalog leftover same-resource lookup is used when this recording missed the fetch", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "lookup-assoc-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const current = writeEvents({ success: true, data: { remainingDays: 0 } }).filter(event => event.id !== "net-balance");
  const previous: EvidenceEvent[] = [{
    id: "old-balance", kind: "network", sessionId: "leave-old", at: "2026-09-03T04:00:00.400Z",
    pageUrl: `${PAGE}apply/create`,
    request: { method: "GET", url: BALANCE_URL, resourceType: "xhr", headers: {}, query: { leaveType: "1" } },
    response: { status: 200, headers: {}, body: { success: true, data: { remainingDays: 8 } } }
  }];
  const prior = finalizeCapabilities(buildCapabilityCandidates([...previous, ...current.map(event => ({
    ...event,
    id: `old-${event.id}`,
    sessionId: "leave-old"
  }))]), [...previous, ...current]);
  const lookup = prior.find(item => /leave-balance|get-leave-balance/.test(item.transport.pathTemplate));
  assert.ok(lookup, prior.map(item => item.transport.pathTemplate).join(","));
  await writeJson(path.join(catalogDir, "capabilities.json"), prior.filter(item => item.id === lookup!.id));
  await writeSession(recordingsDir, { id: "leave-now", startUrl: PAGE, startedAt: "2026-09-04T04:00:00.000Z" }, current);
  await writeSession(recordingsDir, { id: "leave-old", startUrl: PAGE, startedAt: "2026-09-03T04:00:00.000Z" }, previous);
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
  await studio.analyze("leave-now", false);
  const { capabilities, review } = await studio.review("leave-now");
  const create = capabilities.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  assert.match(fieldByName(create, "leaveBalance")?.defaultRule || "", /^from:.+remainingDays/);
  assert.equal(review.findings.some(item => item.fieldPath === "$.leaveBalance"), false, review.summary);
  await rm(temporary, { recursive: true, force: true });
});

test("an unrelated page lookup is not pulled into this write slice", () => {
  const events = writeEvents({ success: true, data: { remainingDays: 8 } });
  const catalog = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const other: CapabilityContract = {
    ...catalog[0]!,
    id: "query-erp-stock",
    operation: "query",
    transport: {
      method: "GET",
      urlTemplate: "http://admin.example.test/admin-api/erp/product/get-stock",
      origin: "http://admin.example.test",
      pathTemplate: "/admin-api/erp/product/get-stock"
    },
    outputSchema: { type: "object", properties: { data: { type: "object", properties: { remainingDays: { type: "number" } } } } },
    evidence: [{ eventId: "erp-stock", sessionId: "erp-old", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }],
    validation: { version: 2, status: "verified", checks: [] }
  };
  const slice = sessionCatalogSlice([...catalog, other], events, events);
  assert.equal(slice.some(item => item.id === "query-erp-stock"), false);
  const related = relatedLookupCapabilities([...catalog, other], catalog.filter(item => item.operation !== "query" || item.transport.pathTemplate.includes("submit-process")));
  assert.equal(related.some(item => item.id === "query-erp-stock"), false);
});

test("approveBinding can confirm a candidate query onto an unresolved write field", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "lookup-bind-"));
  const catalogDir = path.join(temporary, "catalog");
  const events = writeEvents({ success: true, data: { remainingDays: 8 } });
  const catalog = buildCapabilityCandidates(events);
  const create = catalog.find(item => item.transport.pathTemplate.includes("submit-process"))!;
  const lookup = catalog.find(item => /leave-balance/.test(item.transport.pathTemplate))!;
  create.inputForm = create.inputForm.map(field => field.name === "leaveBalance"
    ? { ...field, source: "system", systemHandled: true, defaultRule: undefined, sourceDetail: "未能唯一对应" }
    : field);
  create.validation = { version: 2, status: "candidate", checks: [] };
  lookup.validation = { version: 2, status: "candidate", checks: [] };
  await writeJson(path.join(catalogDir, "capabilities.json"), catalog);
  const studio = new StudioService({
    rootDir: temporary,
    dataDir: temporary,
    recordingsDir: path.join(temporary, "recordings"),
    catalogDir,
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  const bound = await studio.approveBinding({
    fromCapabilityId: lookup.id,
    fromPath: "$.data.remainingDays",
    toCapabilityId: create.id,
    toPath: "$.leaveBalance"
  });
  const field = fieldByName(bound, "leaveBalance");
  assert.equal(field?.source, "binding");
  assert.equal(field?.defaultRule, `from:${lookup.id}:$.data.remainingDays`);
  await rm(temporary, { recursive: true, force: true });
});
