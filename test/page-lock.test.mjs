import assert from "node:assert/strict";
import test from "node:test";
import { ACT_WALL_MS, runAction } from "../src/browser/actions.mjs";
import { withPage } from "../src/browser/session.mjs";

test("page operations on one recording run in order", async () => {
  const order = [];
  const first = withPage("lock-a", async () => {
    order.push("a-start");
    await new Promise((resolve) => setTimeout(resolve, 20));
    order.push("a-end");
  });
  const second = withPage("lock-a", async () => {
    order.push("b");
  });
  await first;
  await second;
  assert.deepEqual(order, ["a-start", "a-end", "b"]);
});

test("runAction past the wall returns lastPublished without taking a new snapshot", { timeout: ACT_WALL_MS + 15000 }, async () => {
  const id = "lock-timeout-wall";
  const order = [];
  const hold = withPage(id, async () => {
    order.push("hold-start");
    await new Promise((resolve) => setTimeout(resolve, ACT_WALL_MS + 800));
    order.push("hold-end");
  });
  const result = await runAction(id, { action: "snapshot" });
  assert.equal(result.ok, false);
  assert.equal(result.error, "act_timeout");
  assert.ok(result.snapshot);
  assert.equal(order.includes("hold-end"), false);
  order.push("timeout");
  const after = withPage(id, async () => {
    order.push("after");
  });
  await hold;
  await after;
  assert.deepEqual(order, ["hold-start", "timeout", "hold-end", "after"]);
});
