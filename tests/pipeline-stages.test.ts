import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { StudioService } from "../src/studio-service.js";
import { exportableCapabilities } from "../src/inference/export-scope.js";
import { writeJson, appendJsonl } from "../src/utils.js";

const LIST = "https://example.test/web/#/oa/doc";
const FORM = "https://example.test/web/#/oa/doc-info";

function cap(partial: Partial<CapabilityContract> & Pick<CapabilityContract, "id" | "operation" | "transport">): CapabilityContract {
  const sideEffect = ["create", "update", "review", "delete", "upload"].includes(partial.operation);
  return {
    kind: "atomic",
    title: partial.title || partial.id,
    description: partial.id,
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
    generated: { source: "heuristic", generatedAt: "2026-09-05T00:00:00.000Z" },
    ...partial
  };
}

function studioOf(temporary: string) {
  return new StudioService({
    rootDir: temporary,
    dataDir: temporary,
    recordingsDir: path.join(temporary, "recordings"),
    catalogDir: path.join(temporary, "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
}

async function writeSession(recordingsDir: string, id: string, events: EvidenceEvent[]) {
  const dir = path.join(recordingsDir, id);
  const eventsFile = path.join(dir, "events.jsonl");
  await writeJson(path.join(dir, "session.json"), {
    id,
    name: id,
    startUrl: LIST,
    startedAt: "2026-09-05T08:00:00.000Z",
    pageKeys: [LIST, FORM],
    expectedOperations: ["query", "create"],
    eventsFile
  });
  for (const event of events) await appendJsonl(eventsFile, event);
}

function completeDocEvents(): EvidenceEvent[] {
  return [{
    id: "ui-search", kind: "ui", sessionId: "now", at: "2026-09-05T08:00:00.000Z",
    pageUrl: LIST, eventType: "click", text: "搜索", label: "搜索",
    form: [{ name: "billCode", label: "单据编号", type: "text", value: "A1" }]
  }, {
    id: "net-page", kind: "network", sessionId: "now", at: "2026-09-05T08:00:01.000Z",
    pageUrl: LIST, correlatedUiEvidenceId: "ui-search",
    request: { method: "GET", url: "https://example.test/oa/doc/page?billCode=A1", resourceType: "xhr", headers: {}, query: { billCode: "A1" } },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 } } }
  }, {
    id: "ui-create", kind: "ui", sessionId: "now", at: "2026-09-05T08:00:02.000Z",
    pageUrl: FORM, eventType: "click", text: "新建", label: "新建",
    form: [{ name: "title", label: "标题", type: "text", value: "日报" }]
  }, {
    id: "net-create", kind: "network", sessionId: "now", at: "2026-09-05T08:00:03.000Z",
    pageUrl: FORM, correlatedUiEvidenceId: "ui-create",
    request: {
      method: "POST", url: "https://example.test/oa/doc/create", resourceType: "xhr", headers: {}, query: {},
      body: { title: "日报", deptId: null }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 9 } }
  }];
}

test("produce-review-gate-repair-review-export stays on one session slice", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "bss-pipeline-stages-"));
  const recordingsDir = path.join(temporary, "recordings");
  const leftover = cap({
    id: "query-doc-count",
    operation: "query",
    title: "查询单据计数",
    transport: { method: "GET", urlTemplate: "https://example.test/oa/doc/get-count", origin: "https://example.test", pathTemplate: "/oa/doc/get-count" },
    evidence: [{ eventId: "old-count", sessionId: "old", kind: "network", at: "2026-09-01T00:00:00.000Z", status: 200 }],
    validation: {
      version: 2,
      status: "candidate",
      checks: [{ name: "recorded-network-evidence", ok: false, detail: "No recorded network evidence" }]
    }
  });
  await writeJson(path.join(temporary, "catalog", "capabilities.json"), [leftover] as CapabilityContract[]);
  await writeSession(recordingsDir, "now", completeDocEvents());
  const studio = studioOf(temporary);

  const produced = await studio.analyze("now", false);
  assert.equal(produced.some(item => ["query", "create"].includes(item.operation)), true, produced.map(item => item.id).join(","));
  assert.equal(produced.some(item => item.id === "query-doc-count"), false);

  const first = await studio.review("now");
  assert.deepEqual(first.capabilities.map(item => item.id).sort(), produced.map(item => item.id).sort());
  assert.equal(first.review.findings.some(item => item.capabilityId === "query-doc-count"), false, first.review.summary);
  assert.notEqual(first.review.next, "re-record", first.review.summary);

  if (first.review.status === "blocked" && first.review.next === "re-analyze") {
    const gate = await studio.evaluateRerecord(LIST);
    assert.equal(gate.allowed, false);
    const catalogBefore = await readFile(path.join(temporary, "catalog", "capabilities.json"), "utf8");
    const second = await studio.review("now");
    const catalogAfter = await readFile(path.join(temporary, "catalog", "capabilities.json"), "utf8");
    assert.match(second.review.summary, /审核结果与上次相同|不要再分析/);
    assert.equal(catalogAfter, catalogBefore);
    await assert.rejects(() => studio.export("单据", path.join(temporary, "skills"), [], "now"));
  } else {
    assert.equal(first.review.status, "passed", first.review.summary);
    assert.equal((await studio.evaluateRerecord(LIST)).allowed, true);
    const exported = exportableCapabilities(first.capabilities);
    assert.ok(exported.some(item => item.operation === "create"));
    const result = await studio.export("单据", path.join(temporary, "skills"), [], "now");
    assert.ok(result.skillName);
  }

  await rm(temporary, { recursive: true, force: true });
});

test("export owns analyze-review-repair-review-export for a raw recording", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "bss-pipeline-export-"));
  const recordingsDir = path.join(temporary, "recordings");
  await writeSession(recordingsDir, "now", completeDocEvents());
  const studio = studioOf(temporary);

  const result = await studio.exportManaged("单据", true, "now");

  assert.equal(result.primaryCount, 2);
  assert.ok(result.name);
  await rm(temporary, { recursive: true, force: true });
});
