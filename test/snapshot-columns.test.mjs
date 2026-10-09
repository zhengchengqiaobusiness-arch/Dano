import assert from "node:assert/strict";
import test from "node:test";
import { annotateColumns, goalExactLines, markRequired, markReadonly, sameColumnRefs, withColumnMarks } from "../src/browser/snapshot.mjs";

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
  const nested = annotateColumns([
    { role: "columnheader", name: "应填数量", indent: 2 },
    { role: "row", name: "", indent: 2 },
    { role: "cell", name: "", indent: 4 },
    { role: "textbox", name: "请输入", indent: 6 },
  ]);
  assert.equal(nested[2].column, "应填数量");
  assert.equal(nested[3].column, "应填数量");
  const starred = annotateColumns([
    { role: "columnheader", name: "数量 *", indent: 2 },
    { role: "row", name: "", indent: 2 },
    { role: "cell", name: "", indent: 4 },
    { role: "textbox", name: "请输入", indent: 6 },
  ]);
  assert.equal(starred[3].column, "数量 *");
  assert.equal(markRequired(null, false, starred[3].column), true);
  assert.equal(markRequired("true", false, "标题"), true);
  assert.equal(markRequired(null, false, "标题"), false);
  assert.equal(markReadonly("true", false, null), true);
});

test("column marks land on the published line and overlay matches come first", () => {
  const yaml = [
    '- button "确定" [ref=f0:e1@1]',
    '- dialog "新建" [ref=f0:e2@1]:',
    '  - table [ref=f0:e3@1]:',
    '    - columnheader "数量 *" [ref=f0:e4@1]',
    '    - row [ref=f0:e5@1]:',
    '      - cell [ref=f0:e6@1]:',
    '        - textbox "请输入" [ref=f0:e7@1]',
    '  - button "确定" [ref=f0:e8@1]',
  ].join("\n");
  const marked = withColumnMarks(yaml);
  assert.match(marked, /textbox "请输入" col "数量 \*" \[ref=f0:e7@1\]/);
  const exact = goalExactLines(
    [
      { role: "button", name: "确定", ref: "f0:e1@1" },
      { role: "button", name: "确定", ref: "f0:e8@1" },
    ],
    "点确定",
    ["f0:e8@1"],
  );
  assert.equal(exact[0].ref, "f0:e8@1");
});
