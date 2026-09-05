/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { buildEvidenceIndex } from "../src/evidence-index.mjs";
import { createPiToolHost } from "../src/pi-tools.mjs";
import { createHarness } from "./helpers/harness.mjs";

test("证据索引只投影已有字段，不分类也不补能力", () => {
  const index = buildEvidenceIndex([
    {
      seq: 1,
      kind: "interaction",
      payload: { kind: "click", tag: "SPAN", name: "", text: "确 认删除", href: "https://example.com/#/list", placeholder: "请输入单据编号", label: "单据编号" },
    },
    {
      seq: 2,
      kind: "network_request",
      payload: {
        method: "DELETE",
        url: "https://example.com/admin-api/items/delete?id=61",
        resource_type: "xhr",
        headers: { authorization: "secret" },
        body: { stored: "inline", text: "ignored" },
      },
    },
    {
      seq: 3,
      kind: "console",
      payload: { text: "should not appear" },
    },
    {
      seq: 5,
      kind: "network_request",
      payload: {
        method: "GET",
        url: "https://example.com/assets/app.js",
        resource_type: "script",
      },
    },
    {
      seq: 4,
      kind: "page_navigated",
      payload: { url: "https://example.com/#/list" },
    },
    {
      seq: 6,
      kind: "network_response",
      payload: {
        request_id: "req_delete",
        url: "https://example.com/admin-api/items/delete?id=61",
        status: 200,
        body: { stored: "inline", text: "{\"ok\":true}" },
      },
    },
    {
      seq: 7,
      kind: "screenshot",
      payload: { reason: "page_ready", image: { stored: "blob", blob_id: "blob_page" } },
    },
    {
      seq: 8,
      kind: "visible_control",
      payload: {
        url: "https://example.com/#/list",
        reason: "page_ready",
        controls: [
          { region: "filter", name: "billCode", label: "单据编号", placeholder: "请输入单据编号", control_kind: "input" },
          { region: "filter", name: "", label: "创建时间", placeholder: "", control_kind: "date" },
        ],
      },
    },
  ]);
  assert.equal(index.count, 6);
  assert.deepEqual(index.items[0], {
    seq: 1,
    kind: "interaction",
    action: "click",
    tag: "SPAN",
    name: "",
    text: "确 认删除",
    href: "https://example.com/#/list",
    placeholder: "请输入单据编号",
    label: "单据编号",
  });
  assert.deepEqual(index.items[1], {
    seq: 2,
    kind: "network_request",
    method: "DELETE",
    path: "/admin-api/items/delete?id=61",
    resource_type: "xhr",
    request_id: "",
  });
  assert.equal(index.items[2].kind, "page_navigated");
  assert.deepEqual(index.items[3], {
    seq: 6,
    kind: "network_response",
    status: 200,
    path: "/admin-api/items/delete?id=61",
    request_id: "req_delete",
    body_stored: "inline",
    blob_id: "",
  });
  assert.deepEqual(index.items[4], {
    seq: 7,
    kind: "screenshot",
    reason: "page_ready",
    blob_id: "blob_page",
  });
  assert.deepEqual(index.items[5], {
    seq: 8,
    kind: "visible_control",
    url: "https://example.com/#/list",
    reason: "page_ready",
    count: 2,
    labels: "单据编号、创建时间",
  });
  assert.ok(!index.items.some((item) => item.resource_type === "script"));
  assert.ok(!JSON.stringify(index).includes("secret"));
  assert.ok(!JSON.stringify(index).includes("should not appear"));
});

test("list_recording_index 返回本场事实摘要", async () => {
  const harness = await createHarness();
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "索引",
    });
    await harness.evidence.append(started.id, "interaction", {
      kind: "click",
      tag: "BUTTON",
      text: "删 除",
    });
    await harness.evidence.append(started.id, "network_request", {
      method: "DELETE",
      url: "http://example.com/api/item?id=1",
      resource_type: "xhr",
    });
    const host = createPiToolHost({
      recordingId: started.id,
      evidence: harness.evidence,
      files: harness.files,
      gate: harness.gate,
      getPiSessionId: () => harness.controller.view(started.id).piSessionId,
    });
    const index = await host.list_recording_index();
    assert.equal(index.recording_id, started.id);
    assert.ok(index.items.some((item) => item.text === "删 除"));
    assert.ok(index.items.some((item) => item.method === "DELETE" && item.path === "/api/item?id=1"));
  } finally {
    await harness.cleanup();
  }
});

test("读请求会带上响应正文，request_id 也能取到 inline body", async () => {
  const harness = await createHarness();
  try {
    const started = await harness.controller.start({
      targetUrl: "http://example.com",
      goal: "正文",
    });
    const request = await harness.evidence.append(started.id, "network_request", {
      request_id: "req_submit",
      method: "POST",
      url: "http://example.com/api/save",
      resource_type: "xhr",
      body: { stored: "inline", text: "{\"title\":\"1\"}" },
    });
    await harness.evidence.append(started.id, "network_response", {
      request_id: "req_submit",
      url: "http://example.com/api/save",
      status: 200,
      body: { stored: "inline", text: "{\"data\":21}" },
    });
    const host = createPiToolHost({
      recordingId: started.id,
      evidence: harness.evidence,
      files: harness.files,
      gate: harness.gate,
      getPiSessionId: () => harness.controller.view(started.id).piSessionId,
    });
    const item = await host.read_evidence_item({ seq: request.seq });
    assert.equal(item.response.status, 200);
    assert.equal(item.response.body.text, "{\"data\":21}");
    const byRequest = await host.read_response_blob({ blob_id: "req_submit" });
    assert.equal(byRequest.found, true);
    assert.equal(byRequest.text, "{\"data\":21}");
    const missing = await host.read_response_blob({ blob_id: "req_missing" });
    assert.equal(missing.found, false);
    assert.match(missing.error, /不要把 request_id 当成 blob_id/);
  } finally {
    await harness.cleanup();
  }
});
