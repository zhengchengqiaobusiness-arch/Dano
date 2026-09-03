import test from "node:test";
import assert from "node:assert/strict";
import { inferOperation, normalizeUrl } from "../src/inference/heuristics.js";
import type { NetworkEvidence } from "../src/domain.js";

function event(method: string, url: string): NetworkEvidence {
  return {
    id: "n1",
    kind: "network",
    sessionId: "s1",
    at: new Date().toISOString(),
    request: {
      method,
      url,
      resourceType: "xhr",
      headers: {},
      query: {}
    },
    response: { status: 200, headers: {}, body: {} }
  };
}

test("normalizes concrete ids and query values", () => {
  const value = normalizeUrl("https://example.com/api/orders/123?status=open&page=2");
  assert.equal(value.urlTemplate, "https://example.com/api/orders/{id}?page={page}&status={status}");
});

test("classifies common methods", () => {
  assert.equal(inferOperation(event("GET", "https://x.test/users")), "query");
  assert.equal(inferOperation(event("DELETE", "https://x.test/users/1")), "delete");
  assert.equal(inferOperation(event("PATCH", "https://x.test/users/1")), "update");
  assert.equal(inferOperation(event("POST", "https://x.test/auth/login")), "authenticate");
  assert.equal(inferOperation(event("POST", "https://x.test/files/upload")), "upload");
  assert.equal(inferOperation(event("GET", "https://x.test/report/download")), "download");
  assert.equal(inferOperation(event("POST", "https://x.test/orders/recalculate")), "action");
  assert.equal(inferOperation(event("GET", "https://x.test/bpm/process-instance/get-approval-detail")), "query");
  assert.equal(inferOperation(event("POST", "https://x.test/bpm/task/approve")), "review");
  assert.equal(inferOperation(event("POST", "https://x.test/oa/duty-leave/create"), { text: "保存草稿" } as any), "create");
  assert.equal(inferOperation(event("POST", "https://x.test/oa/duty-leave/submit-process"), {
    text: "提交",
    pageUrl: "https://x.test/oa/duty/leaveapply/create"
  } as any), "create");
  assert.equal(inferOperation(event("POST", "https://x.test/orders/recalculate"), {
    text: "重算",
    pageUrl: "https://x.test/orders"
  } as any), "action");
});
