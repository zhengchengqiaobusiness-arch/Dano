/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { buildActionTimeline, requestShapeFromEvents } from "../src/evidence-facts.mjs";

test("动作台账带 actor，并挂上随后的 xhr/fetch", () => {
  const timeline = buildActionTimeline([
    { seq: 1, kind: "interaction", payload: { actor: "human", kind: "pointer_move" } },
    { seq: 2, kind: "interaction", payload: { actor: "human", kind: "click", label: "查询" } },
    { seq: 3, kind: "network_request", payload: { method: "GET", url: "https://x.test/api/list?q=1", resource_type: "xhr", request_id: "r1" } },
    { seq: 4, kind: "interaction", payload: { actor: "pi", kind: "click", label: "新增" } },
    { seq: 5, kind: "network_request", payload: { method: "POST", url: "https://x.test/api/create", resource_type: "fetch", request_id: "r2" } },
  ]);
  assert.equal(timeline.count, 2);
  assert.equal(timeline.actions[0].actor, "human");
  assert.equal(timeline.actions[0].label, "查询");
  assert.equal(timeline.actions[0].requests[0].request_id, "r1");
  assert.equal(timeline.actions[1].actor, "pi");
  assert.equal(timeline.actions[1].requests[0].method, "POST");
});

test("请求形状摊开 query/body 键，不判断来源", () => {
  const shape = requestShapeFromEvents([
    {
      seq: 9,
      kind: "network_request",
      payload: {
        method: "POST",
        url: "https://x.test/api/leave?dept=1",
        request_id: "req_9",
        body: { text: JSON.stringify({ days: 2, reason: "事假" }) },
      },
    },
  ], 9);
  assert.equal(shape.found, true);
  assert.equal(shape.method, "POST");
  assert.ok(shape.query_keys.some((item) => item.key === "dept"));
  assert.ok(shape.body_keys.some((item) => item.key === "days"));
  assert.ok(shape.body_keys.some((item) => item.key === "reason"));
});
