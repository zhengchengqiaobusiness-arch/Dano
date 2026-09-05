import test from "node:test";
import assert from "node:assert/strict";
import { inferOperation, inferUiOperationIntent, normalizeUrl } from "../src/inference/heuristics.js";
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
  const search = event("POST", "https://x.test/portal/public/getAllZy");
  search.request.body = { gjz: "123", pageNo: 1, pageSize: 10 };
  search.response = { status: 200, headers: {}, body: { data: { list: [{ id: "1", title: "a" }] } } };
  assert.equal(inferOperation(search), "query");
  const ask = event("POST", "https://x.test/dataiq/sjws_chat");
  ask.request.body = { sys_query: "123123", wybs: "51e561cb-49e9-4f96-817a-2d0a7e2a4360" };
  ask.response = { status: 200, headers: {}, body: "data: {\"event\":\"answer\"}" };
  assert.equal(inferOperation(ask), "query");
  const balance = event("POST", "https://x.test/oa/duty-leave/get-leave-balance-my");
  balance.request.query = { leaveType: "1" };
  balance.response = { status: 200, headers: {}, body: { success: true, data: { remainingDays: 8 } } };
  assert.equal(inferOperation(balance), "query");
});

test("classifies the business operation the person actually selected on a page", () => {
  assert.equal(inferUiOperationIntent("搜索", "https://x.test/oa/seal/list"), "query");
  assert.equal(inferUiOperationIntent("新增", "https://x.test/oa/seal/list"), "create");
  assert.equal(inferUiOperationIntent("修改", "https://x.test/oa/seal/list"), "update");
  assert.equal(inferUiOperationIntent("删除", "https://x.test/oa/seal/list"), "delete");
  assert.equal(inferUiOperationIntent("提交审批", "https://x.test/oa/doc/list"), "review");
  assert.equal(inferUiOperationIntent("撤销申请", "https://x.test/oa/leave/list"), "action");
  assert.equal(inferUiOperationIntent("保存", "https://x.test/oa/seal/form/add/seal_apply"), "create");
  assert.equal(inferUiOperationIntent("保存", "https://x.test/oa/seal/form/edit/42"), "update");
  assert.equal(inferUiOperationIntent("重置", "https://x.test/oa/seal/list"), undefined);
});

test("a save request from an add page stays create even when a nearby field label contains list", () => {
  const create = event("POST", "https://x.test/prod-api/oa/sealApply");
  create.pageUrl = "https://x.test/oa/commonApply/sealApply/form/add/seal_apply";
  create.request.body = { billType: "seal_apply", sealId: "recorded-id" };
  create.response = { status: 200, headers: {}, body: { code: 200, data: { id: "created-id" } } };
  assert.equal(inferOperation(create, {
    text: "保存",
    label: "00抄送列表选择抄送用户",
    pageUrl: create.pageUrl
  } as any), "create");
});
