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
  ]);
  assert.equal(index.count, 3);
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
  });
  assert.equal(index.items[2].kind, "page_navigated");
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
