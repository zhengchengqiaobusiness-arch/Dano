import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "os";
import { mkdtemp, rm } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { reviewSessionIds } from "../src/inference/export-scope.js";
import { StudioService } from "../src/studio-service.js";
import { writeJson, appendJsonl } from "../src/utils.js";

const ORDER_PAGE = "https://example.test/erp/order";
const OTHER_PAGE = "https://example.test/oa/duty/leave";

function fatRows(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    id: index + 100,
    name: `节点${index}`,
    unitName: "盒",
    extra: "盒",
    title: `节点${index}`
  }));
}

function orderEvents(): EvidenceEvent[] {
  return [{
    id: "ui-form", kind: "ui", sessionId: "order-now", at: "2026-09-03T12:00:00.000Z",
    pageUrl: ORDER_PAGE, eventType: "input",
    form: [
      { name: "productId", label: "产品", type: "select", required: true, value: "苹果" },
      { name: "count", label: "数量", type: "number", required: true, value: 2 },
      { name: "productPrice", label: "单价", type: "number", required: true, value: 10 },
      { name: "unitName", label: "单位", type: "readonly", value: "盒" }
    ]
  }, {
    id: "ui-submit", kind: "ui", sessionId: "order-now", at: "2026-09-03T12:00:01.000Z",
    pageUrl: ORDER_PAGE, eventType: "click", text: "确定", label: "确定"
  }, {
    id: "net-product", kind: "network", sessionId: "order-now", at: "2026-09-03T12:00:00.500Z",
    pageUrl: ORDER_PAGE,
    request: { method: "GET", url: "https://example.test/admin-api/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果", unitName: "盒" }] } }
  }, {
    id: "net-create", kind: "network", sessionId: "order-now", at: "2026-09-03T12:00:02.000Z",
    pageUrl: ORDER_PAGE, correlatedUiEvidenceId: "ui-submit",
    request: {
      method: "POST", url: "https://example.test/admin-api/order/create", resourceType: "xhr", headers: {}, query: {},
      body: { productId: 9, count: 2, productPrice: 10, unitName: "盒", amount: 20 }
    },
    response: { status: 200, headers: {}, body: { success: true, data: 1 } }
  }];
}

function otherPagePoison(sessionId: string, at: string): EvidenceEvent[] {
  return [{
    id: `${sessionId}-net-product`, kind: "network", sessionId, at,
    pageUrl: OTHER_PAGE,
    request: { method: "GET", url: "https://example.test/admin-api/product/simple-list", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { success: true, data: [{ id: 9, name: "苹果", unitName: "盒", extra: "盒" }, ...fatRows(400)] } }
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

test("review session ids stay on the current page and skip other-page history", () => {
  const ids = reviewSessionIds([
    { id: "order-now", startUrl: ORDER_PAGE },
    { id: "leave-old", startUrl: OTHER_PAGE },
    { id: "order-old", startUrl: ORDER_PAGE }
  ], "order-now", orderEvents());
  assert.equal(ids.has("order-now"), true);
  assert.equal(ids.has("order-old"), false);
  assert.equal(ids.has("leave-old"), false);
});

test("finalize on this page keeps unique from-rules that other-page history would poison", () => {
  const current = orderEvents();
  const catalog = finalizeCapabilities(buildCapabilityCandidates(current), current);
  const create = catalog.find(item => item.transport.pathTemplate.includes("/order/create"))!;
  assert.match(create.inputForm.find(field => field.name === "unitName")?.defaultRule || "", /^from:.+\.unitName\|via:productId$/);

  const mixed = [...current, ...otherPagePoison("leave-old", "2026-09-01T00:00:00.000Z")];
  const poisoned = finalizeCapabilities(buildCapabilityCandidates(mixed), mixed);
  const unit = poisoned.find(item => item.transport.pathTemplate.includes("/order/create"))!.inputForm.find(field => field.name === "unitName")!;
  assert.equal(unit.defaultRule?.startsWith("from:") ?? false, false);
});

test("validate only loads this page's sessions and keeps the recorded request shape", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-review-scope-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const current = orderEvents();
  const catalog = finalizeCapabilities(buildCapabilityCandidates(current), current);
  await writeJson(path.join(catalogDir, "capabilities.json"), catalog as CapabilityContract[]);
  await writeSession(recordingsDir, { id: "order-now", startUrl: ORDER_PAGE, startedAt: "2026-09-03T12:00:00.000Z" }, current);
  for (let index = 0; index < 12; index += 1) {
    await writeSession(
      recordingsDir,
      { id: `leave-old-${index}`, startUrl: OTHER_PAGE, startedAt: `2026-09-01T0${String(index).padStart(2, "0")}:00:00.000Z` },
      otherPagePoison(`leave-old-${index}`, `2026-09-01T0${String(index).padStart(2, "0")}:00:01.000Z`)
    );
  }
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
  const { capabilities } = await studio.review();
  const elapsed = Date.now() - started;
  const create = capabilities.find(item => item.transport.pathTemplate.includes("/order/create"))!;
  assert.ok(create, capabilities.map(item => item.transport.pathTemplate).join(","));
  assert.match(create.inputForm.find(field => field.name === "unitName")?.defaultRule || "", /^from:.+\.unitName\|via:productId$/);
  assert.equal(create.inputForm.find(field => field.name === "count")?.source, "caller");
  assert.equal(create.inputForm.find(field => field.name === "productId")?.source, "caller");
  assert.ok(elapsed < 2_000, `review took ${elapsed}ms`);
  await rm(temporary, { recursive: true, force: true });
});
