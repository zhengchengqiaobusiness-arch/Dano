import assert from "node:assert/strict";
import test from "node:test";
import { annotateColumns, goalExactLines, sameColumnRefs } from "../src/browser/snapshot.mjs";

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
  const exact = goalExactLines(nodes, "点击应填数量");
  assert.equal(exact.length, 1);
  assert.equal(exact[0].role, "cell");
  const refs = sameColumnRefs('- columnheader "应填数量" ref=f0:e1@2\n- cell "18" 应填数量 ref=f0:e9@2\n', 'columnheader "应填数量"');
  assert.deepEqual(refs, ["f0:e9@2"]);
});
