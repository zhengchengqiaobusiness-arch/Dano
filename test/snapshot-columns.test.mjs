import assert from "node:assert/strict";
import test from "node:test";
import { annotateColumns } from "../src/browser/snapshot.mjs";

test("table cells keep the column header from the same snapshot", () => {
  const nodes = annotateColumns([
    { role: "columnheader", name: "应填数量" },
    { role: "columnheader", name: "已填数量" },
    { role: "row", name: "" },
    { role: "cell", name: "18" },
    { role: "cell", name: "0" },
  ]);
  assert.equal(nodes[3].column, "应填数量");
  assert.equal(nodes[4].column, "已填数量");
});
