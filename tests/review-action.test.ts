import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { mkdtemp, rm } from "node:fs/promises";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { StudioService } from "../src/studio-service.js";
import { writeJson, appendJsonl } from "../src/utils.js";

const DOC_LIST = "https://example.test/web/#/oa/doc";
const DOC_FORM = "https://example.test/web/#/oa/doc-info";
const LEAVE_PAGE = "https://example.test/web/#/oa/duty/leave";

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

test("evaluateRerecord blocks the same business pages after a non-major review", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "bss-review-action-"));
  await writeJson(path.join(temporary, "studio-state.json"), {
    lastReview: {
      sessionId: "now",
      status: "blocked",
      next: "re-analyze",
      allowRerecord: false,
      pageKeys: [DOC_LIST, DOC_FORM],
      startUrl: DOC_LIST,
      signature: "caller-fields-backed-by-ui:query-doc:"
    }
  });
  const studio = studioOf(temporary);
  const sameList = await studio.evaluateRerecord(`${DOC_LIST}?billCode=A1`);
  const sameForm = await studio.evaluateRerecord(DOC_FORM);
  const other = await studio.evaluateRerecord(LEAVE_PAGE);
  assert.equal(sameList.allowed, false);
  assert.match(sameList.allowed ? "" : sameList.message, /禁止|不要重新录制|不要 business_skill_record_start/);
  assert.equal(sameForm.allowed, false);
  assert.equal(other.allowed, true);
  await assert.rejects(
    () => studio.startRecording(DOC_LIST, "again"),
    /禁止|不要重新录制|不要 business_skill_record_start/
  );
  await rm(temporary, { recursive: true, force: true });
});

test("evaluateRerecord allows a new recording when review asked to re-record", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "bss-review-action-"));
  await writeJson(path.join(temporary, "studio-state.json"), {
    lastReview: {
      sessionId: "now",
      status: "blocked",
      next: "re-record",
      allowRerecord: true,
      pageKeys: [DOC_LIST],
      startUrl: DOC_LIST,
      signature: "successful-response:create-doc:"
    }
  });
  const studio = studioOf(temporary);
  assert.equal((await studio.evaluateRerecord(DOC_LIST)).allowed, true);
  await rm(temporary, { recursive: true, force: true });
});

test("a second review with the same findings stops instead of analyzing again", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "bss-review-action-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const events: EvidenceEvent[] = [{
    id: "net-create",
    kind: "network",
    sessionId: "now",
    at: "2026-09-05T08:00:02.000Z",
    pageUrl: DOC_FORM,
    request: {
      method: "POST",
      url: "https://example.test/oa/doc/submit",
      resourceType: "xhr",
      headers: {},
      query: {},
      body: { title: "A" }
    },
    response: { status: 200, headers: {}, body: { data: 1 } }
  }];
  const create = cap({
    id: "create-doc",
    operation: "create",
    title: "新建单据",
    transport: { method: "POST", urlTemplate: "https://example.test/oa/doc/submit", origin: "https://example.test", pathTemplate: "/oa/doc/submit" },
    inputForm: [{
      path: "$.title", name: "title", label: "标题", valueType: "string", source: "binding",
      required: false, requiredBasis: "not-observed", systemHandled: true,
      sourceDetail: "由已确认绑定从 query-ghost$.id 提供", widget: "text",
      defaultRule: "from:query-ghost:$.id"
    }],
    evidence: [{ eventId: "net-create", sessionId: "now", kind: "network", at: "2026-09-05T08:00:02.000Z", status: 200 }],
    bindings: [{
      id: "human-missing",
      fromCapabilityId: "query-ghost",
      fromPath: "$.id",
      toPath: "$.title",
      confidence: 1,
      evidenceIds: [],
      approved: true,
      approvalSource: "human"
    }]
  });
  const dir = path.join(recordingsDir, "now");
  await writeJson(path.join(dir, "session.json"), {
    id: "now",
    name: "now",
    startUrl: DOC_FORM,
    startedAt: "2026-09-05T08:00:00.000Z",
    pageKeys: [DOC_FORM],
    eventsFile: path.join(dir, "events.jsonl")
  });
  await appendJsonl(path.join(dir, "events.jsonl"), events[0]);
  await writeJson(path.join(catalogDir, "capabilities.json"), [create] as CapabilityContract[]);
  const studio = studioOf(temporary);
  const first = await studio.review("now");
  assert.equal(first.review.status, "blocked", first.review.summary);
  assert.equal(first.review.next, "re-analyze", first.review.summary);
  const second = await studio.review("now");
  assert.equal(second.review.status, "blocked");
  assert.equal(second.review.next, "re-analyze");
  assert.match(second.review.summary, /审核结果与上次相同/);
  assert.equal((await studio.evaluateRerecord(DOC_FORM)).allowed, false);
  assert.equal((await studio.evaluateRerecord(LEAVE_PAGE)).allowed, true);
  await rm(temporary, { recursive: true, force: true });
});
