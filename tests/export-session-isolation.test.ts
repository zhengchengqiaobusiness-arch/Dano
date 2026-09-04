import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import type { CapabilityContract, EvidenceEvent } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import { finalizeCapabilities } from "../src/inference/finalize-capabilities.js";
import { capabilitiesForSession, reviewSessionIds, sessionCatalogSlice } from "../src/inference/export-scope.js";
import { normalizeSkillName } from "../src/export/skill-exporter.js";
import { StudioService } from "../src/studio-service.js";
import { writeJson, appendJsonl } from "../src/utils.js";

const TODO_PAGE = "http://admin.dianshixinxi.com:90/workspace/todo";
const LEAVE_PAGE = "http://admin.dianshixinxi.com:90/oa/duty/leave";

function todoEvents(): EvidenceEvent[] {
  return [{
    id: "todo-ui", kind: "ui", sessionId: "todo-now", at: "2026-09-04T02:36:00.000Z",
    pageUrl: TODO_PAGE, eventType: "input",
    form: [{ name: "name", label: "任务名称", type: "text", value: "样例任务" }]
  }, {
    id: "todo-search", kind: "ui", sessionId: "todo-now", at: "2026-09-04T02:36:01.000Z",
    pageUrl: TODO_PAGE, eventType: "click", text: "搜索", label: "搜索"
  }, {
    id: "todo-net", kind: "network", sessionId: "todo-now", at: "2026-09-04T02:36:02.000Z",
    pageUrl: TODO_PAGE, correlatedUiEvidenceId: "todo-search",
    request: {
      method: "GET",
      url: "http://admin.dianshixinxi.com:90/admin-api/bpm/task/todo-page?pageNo=1&pageSize=10&name=%E6%A0%B7%E4%BE%8B%E4%BB%BB%E5%8A%A1",
      resourceType: "xhr", headers: {}, query: { pageNo: "1", pageSize: "10", name: "样例任务" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: { list: [], total: 0 }, msg: "" } }
  }];
}

function leaveEvents(): EvidenceEvent[] {
  return [{
    id: "leave-ui", kind: "ui", sessionId: "leave-old", at: "2026-09-03T13:00:00.000Z",
    pageUrl: LEAVE_PAGE, eventType: "click", text: "提交", label: "提交",
    form: [{ name: "reason", label: "原因", type: "text", value: "年假", required: true }]
  }, {
    id: "leave-net", kind: "network", sessionId: "leave-old", at: "2026-09-03T13:00:01.000Z",
    pageUrl: LEAVE_PAGE, correlatedUiEvidenceId: "leave-ui",
    request: {
      method: "POST",
      url: "http://admin.dianshixinxi.com:90/admin-api/oa/duty-leave/submit-process",
      resourceType: "xhr", headers: {}, query: {},
      body: { reason: "年假" }
    },
    response: { status: 200, headers: {}, body: { code: 0, data: 1, msg: "" } }
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

function mixedCatalog() {
  const leave = leaveEvents();
  const todo = todoEvents();
  return {
    leave,
    todo,
    catalog: [
      ...finalizeCapabilities(buildCapabilityCandidates(leave), leave),
      ...finalizeCapabilities(buildCapabilityCandidates(todo), todo)
    ] as CapabilityContract[]
  };
}

test("chinese export name does not inherit another page write slug", () => {
  const { catalog } = mixedCatalog();
  const slug = normalizeSkillName("待办任务", catalog);
  assert.doesNotMatch(slug, /duty-leave|submit-process/);
});

test("review and slice stay on this recording only", () => {
  const { catalog, leave, todo } = mixedCatalog();
  const ids = reviewSessionIds([
    { id: "todo-now", startUrl: TODO_PAGE },
    { id: "leave-old", startUrl: LEAVE_PAGE }
  ], "todo-now", todo);
  assert.deepEqual([...ids], ["todo-now"]);
  assert.equal(capabilitiesForSession(catalog, [...leave, ...todo], todo).some(item => item.transport.pathTemplate.includes("submit-process")), false);
  assert.equal(sessionCatalogSlice(catalog, [...leave, ...todo], todo).some(item => item.transport.pathTemplate.includes("submit-process")), false);
  assert.equal(sessionCatalogSlice(catalog, [...leave, ...todo], todo).some(item => item.transport.pathTemplate.includes("todo-page")), true);
});

test("studio export of the latest todo session does not pack the previous leave skill", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "export-session-iso-"));
  const recordingsDir = path.join(temporary, "recordings");
  const catalogDir = path.join(temporary, "catalog");
  const { catalog, leave, todo } = mixedCatalog();
  await writeJson(path.join(catalogDir, "capabilities.json"), catalog);
  await writeJson(path.join(temporary, "studio-state.json"), { lastAnalyzedSessionId: "leave-old" });
  await writeSession(recordingsDir, { id: "leave-old", startUrl: LEAVE_PAGE, startedAt: "2026-09-03T13:00:00.000Z" }, leave);
  await writeSession(recordingsDir, { id: "todo-now", startUrl: TODO_PAGE, startedAt: "2026-09-04T02:36:00.000Z" }, todo);
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
  const exported = await studio.exportManaged("待办任务", true);
  const skill = await readFile(path.join(exported.directory, "SKILL.md"), "utf8");
  const contract = await readFile(path.join(exported.directory, "references", "CONTRACT.json"), "utf8");
  assert.doesNotMatch(exported.directory, /duty-leave|submit-process/);
  assert.doesNotMatch(exported.name, /duty-leave|submit-process/);
  assert.doesNotMatch(skill, /duty-leave|submit-process|请假/);
  assert.doesNotMatch(contract, /duty-leave|submit-process/);
  assert.match(contract, /todo-page/);
  await rm(temporary, { recursive: true, force: true });
});
