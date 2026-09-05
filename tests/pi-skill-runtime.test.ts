import test from "node:test";
import assert from "node:assert/strict";
import type { CapabilityContract, EvidenceEvent, InputFormField } from "../src/domain.js";
import { buildCapabilityCandidates } from "../src/inference/build-candidates.js";
import {
  applyDeterministicCatalogJudgment,
  applyExactEvidenceJoin,
  fallbackRole
} from "../src/inference/pi-skill-runtime.js";

function field(partial: Partial<InputFormField> & Pick<InputFormField, "name">): InputFormField {
  return {
    path: partial.path || `$.${partial.name}`,
    label: partial.label || partial.name,
    valueType: partial.valueType || "string",
    source: partial.source || "system",
    required: partial.required ?? false,
    requiredBasis: partial.requiredBasis || "not-observed",
    systemHandled: partial.systemHandled ?? true,
    sourceDetail: partial.sourceDetail || "",
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
    sideEffect: false,
    confirmation: { required: false },
    completion: { acceptedHttpStatuses: [200] },
    bindings: [],
    validation: { version: 2, status: "candidate", checks: [] },
    generated: { source: "heuristic", generatedAt: "2026-09-06T00:00:00.000Z" },
    ...partial
  };
}

test("exact name join marks the same-name page field as caller and keeps every request key", () => {
  const events: EvidenceEvent[] = [{
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/leave", eventType: "input",
    form: [
      { name: "reason", label: "请输入原因", type: "text", value: "事假说明" },
      { name: "remark", label: "备注", type: "textarea", value: "无关" }
    ]
  }, {
    id: "net", kind: "network", sessionId: "s", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/leave",
    request: {
      method: "POST", url: "https://x/oa/leave/create", resourceType: "xhr", headers: {}, query: {},
      body: { reason: "事假说明", token: "hidden-token", pageNo: 1 }
    },
    response: { status: 200, headers: {}, body: { success: true } }
  }];
  const clustered = buildCapabilityCandidates(events);
  const judged = applyDeterministicCatalogJudgment(clustered, events);
  const create = judged.find(item => item.transport.pathTemplate.endsWith("/leave/create"))!;
  const names = create.inputForm.map(item => item.name);
  assert.equal(names.includes("reason"), true);
  assert.equal(names.includes("token"), true);
  assert.equal(names.includes("pageNo"), true);
  assert.equal(create.inputForm.find(item => item.name === "reason")?.source, "caller");
  assert.equal(create.inputForm.find(item => item.name === "reason")?.label, "原因");
  assert.equal(create.inputForm.find(item => item.name === "token")?.source, "system");
  assert.equal(create.inputForm.find(item => item.name === "token")?.defaultRule, 'literal:"hidden-token"');
  assert.equal(create.inputForm.find(item => item.name === "pageNo")?.source, "system");
  assert.equal(create.inputForm.some(item => item.path === "$.invented"), false);
});

test("leftover one-to-one does not bind an unrelated remark to a hidden token", () => {
  const capability = cap({
    id: "write",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/a", origin: "https://x", pathTemplate: "/a" },
    inputForm: [field({ name: "token", source: "system" })],
    evidence: [{ eventId: "ui", sessionId: "s", kind: "ui", at: "2026-09-06T00:00:00.000Z" }]
  });
  const events: EvidenceEvent[] = [{
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/a", eventType: "input",
    form: [{ name: "remark", label: "备注", type: "textarea", value: "说明文字" }]
  }];
  const joined = applyExactEvidenceJoin(capability, events);
  assert.notEqual(joined.inputForm[0]?.source, "caller");
  assert.notEqual(joined.inputForm[0]?.label, "备注");
});

test("a distinctive unique value binds exactly one field and leaves 0/1 alone", () => {
  const events: EvidenceEvent[] = [{
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/home", eventType: "change",
    form: [{ label: "请输入关键字", type: "search", value: "社会信用" }]
  }, {
    id: "net", kind: "network", sessionId: "s", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/home",
    request: {
      method: "POST", url: "https://x/search/getAllZy", resourceType: "xhr", headers: {}, query: {},
      body: { gjz: "社会信用", pageNo: 1, pageSize: 10, zylx: "0" }
    },
    response: { status: 200, headers: {}, body: { success: true, data: { list: [], total: 0 } } }
  }];
  const judged = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const search = judged.find(item => item.transport.pathTemplate.endsWith("/getAllZy"))!;
  assert.equal(search.inputForm.find(item => item.name === "gjz")?.source, "caller");
  assert.match(search.inputForm.find(item => item.name === "gjz")?.label || "", /关键字/);
  assert.equal(search.inputForm.find(item => item.name === "zylx")?.source, "system");
  assert.equal(search.inputForm.find(item => item.name === "zylx")?.defaultRule, 'literal:"0"');
  assert.equal(search.role, "primary");
});

test("exact list id joins a directory lookup and ignores a sibling business page", () => {
  const events: EvidenceEvent[] = [{
    id: "ui", kind: "ui", sessionId: "s", at: "2026-09-06T00:00:00.000Z",
    pageUrl: "https://x/oa/leave", eventType: "submit",
    form: [{ name: "assigneeId", label: "审批人", type: "picker", value: "张三" }]
  }, {
    id: "users", kind: "network", sessionId: "s", at: "2026-09-06T00:00:00.100Z",
    request: { method: "GET", url: "https://x/system/user/page", resourceType: "xhr", headers: {}, query: {} },
    response: { status: 200, headers: {}, body: { data: { list: [{ id: 174, nickname: "张三" }, { id: 1, nickname: "管理员" }] } } }
  }, {
    id: "docs", kind: "network", sessionId: "s", at: "2026-09-06T00:00:00.200Z",
    request: { method: "POST", url: "https://x/oa/doc/page", resourceType: "xhr", headers: {}, query: {}, body: { pageNo: 1 } },
    response: { status: 200, headers: {}, body: { data: { list: [{ id: 174, name: "制度A" }, { id: 9, name: "制度B" }] } } }
  }, {
    id: "net", kind: "network", sessionId: "s", at: "2026-09-06T00:00:01.000Z",
    pageUrl: "https://x/oa/leave", correlatedUiEvidenceId: "ui",
    request: {
      method: "POST", url: "https://x/oa/leave/create", resourceType: "xhr", headers: {}, query: {},
      body: { assigneeId: 174, reason: "事假" }
    },
    response: { status: 200, headers: {}, body: { success: true } }
  }];
  const judged = applyDeterministicCatalogJudgment(buildCapabilityCandidates(events), events);
  const create = judged.find(item => item.transport.pathTemplate.endsWith("/leave/create"))!;
  const assignee = create.inputForm.find(item => item.name === "assigneeId");
  assert.equal(assignee?.source, "caller");
  assert.equal(assignee?.candidates?.type, "capability");
  const candidateId = assignee?.candidates?.type === "capability" ? assignee.candidates.capabilityId : "";
  assert.match(candidateId, /user/);
  assert.doesNotMatch(candidateId, /doc/);
});

test("fallback role keeps a write primary and marks companion queries as lookup", () => {
  const write = cap({
    id: "create",
    operation: "create",
    transport: { method: "POST", urlTemplate: "https://x/leave/create", origin: "https://x", pathTemplate: "/leave/create" },
    inputForm: [field({ name: "reason", source: "caller", systemHandled: false })]
  });
  const lookup = cap({
    id: "users",
    operation: "query",
    transport: { method: "GET", urlTemplate: "https://x/user/page", origin: "https://x", pathTemplate: "/user/page" },
    inputForm: [field({ name: "pageNo", source: "system" })]
  });
  assert.equal(fallbackRole(write, [write, lookup]), "primary");
  assert.equal(fallbackRole(lookup, [write, lookup]), "lookup");
});
