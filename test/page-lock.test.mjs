import assert from "node:assert/strict";
import test from "node:test";
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
