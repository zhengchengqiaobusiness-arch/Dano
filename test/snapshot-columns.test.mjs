import assert from "node:assert/strict";
import test from "node:test";
import { annotateColumns, annotateDuplicatePaths, annotatePopup, applyDisplayedValues, changedControls, choiceClick, controlText, focusAfterChoice, frameLabel, frameSortKey, focusedUnchanged, goalExactLines, includeInSnapshot, keptFocusValue, labelFromSnapshot, labelsMatch, parseSnapshotYaml, readControlValue, refTail, sameColumnRefs, settledControlValue, siblingValue, snapshotHasControl, wrapperGeneric } from "../src/browser/snapshot.mjs";

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
  assert.equal(refs.length, 1);
  assert.match(refs[0], /f0:e9@2/);
});

test("a field name drops a nearby button's text from its own name", () => {
  const nodes = parseSnapshotYaml([
    "- button \"悬停预览，点击查看详情\" [ref=e1]",
    "- spinbutton \"* 转固数量 悬停预览，点击查看详情\" [ref=e2]",
    "- textbox \"采购日期 悬停预览，点击查看详情\" [ref=e3]",
    "- button \"确定\" [ref=e4]",
    "- textbox \"请先 确定\" [ref=e5]",
  ].join("\n"));
  assert.equal(nodes.find((node) => node.ariaRef === "e1").name, "悬停预览，点击查看详情");
  assert.equal(nodes.find((node) => node.ariaRef === "e2").name, "* 转固数量");
  assert.equal(nodes.find((node) => node.ariaRef === "e3").name, "采购日期");
  assert.equal(nodes.find((node) => node.ariaRef === "e5").name, "请先 确定");
});

test("a wrapped control still matches the role named in the snapshot", () => {
  const yaml = ["- generic:", "  - button \"编辑\""].join("\n");
  assert.equal(labelsMatch("button \"编辑\"", labelFromSnapshot(yaml, "button \"编辑\"")), true);
  assert.equal(labelsMatch("button \"删除\"", labelFromSnapshot(yaml, "button \"删除\"")), false);
});

test("a ref matches its snapshot name and not a different control", () => {
  assert.equal(labelsMatch('button "编辑"', 'button "编辑"'), true);
  assert.equal(labelsMatch('button "编辑" 操作', 'button "编辑"'), true);
  assert.equal(labelsMatch('button "编辑"', 'button "删除"'), false);
  assert.equal(labelsMatch('button "新增 办公用品"', 'button "新增"'), false);
  assert.equal(labelsMatch('spinbutton "* 转固数量"', 'spinbutton "* 转固数量 悬停预览，点击查看详情"', ["悬停预览，点击查看详情"]), true);
  assert.equal(labelsMatch('button "新增"', 'button "新增 办公用品"', ["办公用品"]), false);
  assert.equal(labelsMatch('textbox placeholder="请输入"', 'textbox placeholder="请输入"'), true);
  assert.equal(labelsMatch('textbox placeholder="请输入" 名称', 'textbox placeholder="请输入"'), true);
});

test("a navigation menu does not hide the same-named page control", () => {
  const nodes = parseSnapshotYaml([
    "- navigation \"主导航\" [ref=e1]:",
    "  - menu \"菜单\" [ref=e2]:",
    "    - menuitem \"编辑\" [ref=e3]",
    "- button \"编辑\" [ref=e4]",
  ].join("\n"));
  const exact = goalExactLines(nodes, "点击编辑");
  assert.deepEqual(exact.map((node) => node.ariaRef), ["e3", "e4"]);
});

test("a list inside the page does not hide the same-named button", () => {
  const nodes = parseSnapshotYaml([
    "- main [ref=e1]:",
    "  - listbox \"列表\" [ref=e2]:",
    "    - option \"项目\" [ref=e3]",
    "  - button \"项目\" [ref=e4]",
  ].join("\n"));
  const exact = goalExactLines(nodes, "点击项目");
  assert.deepEqual(exact.map((node) => node.ariaRef), ["e3", "e4"]);
});

test("the later menu keeps the same-named control", () => {
  const nodes = parseSnapshotYaml([
    "- button \"编辑\" [ref=e1]",
    "- menu \"操作\" [ref=e2]:",
    "  - menuitem \"编辑\" [ref=e3]",
  ].join("\n"));
  const exact = goalExactLines(nodes, "点击编辑");
  assert.deepEqual(exact.map((node) => node.ariaRef), ["e3"]);
});

test("the later dialog keeps the same-named control", () => {
  const nodes = parseSnapshotYaml([
    "- dialog \"旧\" [ref=e1]:",
    "  - button \"确定\" [ref=e2]",
    "- button \"返回\" [ref=e3]",
    "- dialog \"新\" [ref=e4]:",
    "  - button \"确定\" [ref=e5]",
  ].join("\n"));
  const exact = goalExactLines(nodes, "点击确定，然后返回");
  assert.deepEqual(exact.map((node) => node.ariaRef), ["e3", "e5"]);
});

test("a dialog keeps the same-named control inside it", () => {
  const nodes = parseSnapshotYaml([
    "- button \"确定\" [ref=e1]",
    "- button \"返回\" [ref=e2]",
    "- dialog \"提示\" [ref=e3]:",
    "  - button \"确定\" [ref=e4]",
  ].join("\n"));
  const exact = goalExactLines(nodes, "点击确定，然后返回");
  assert.deepEqual(exact.map((node) => node.ariaRef), ["e2", "e4"]);
});

test("an extra cell does not reuse the first column", () => {
  const nodes = annotateColumns([
    { role: "columnheader", name: "名称", depth: 1 },
    { role: "columnheader", name: "数量", depth: 1 },
    { role: "row", depth: 1 },
    { role: "cell", name: "甲", depth: 2 },
    { role: "cell", name: "1", depth: 2 },
    { role: "cell", name: "编辑", depth: 2 },
  ]);
  assert.equal(nodes[3].column, "名称");
  assert.equal(nodes[4].column, "数量");
  assert.equal(nodes[5].column, undefined);
  const exact = goalExactLines(nodes, "点击名称");
  assert.equal(exact.some((node) => node.name === "编辑"), false);
});

test("a checkbox column with its own header keeps that column", () => {
  const nodes = annotateColumns([
    { role: "columnheader", name: "启用", depth: 1 },
    { role: "columnheader", name: "名称", depth: 1 },
    { role: "row", depth: 1 },
    { role: "cell", name: "", depth: 2 },
    { role: "checkbox", name: "", depth: 3 },
    { role: "cell", name: "甲", depth: 2 },
  ]);
  assert.equal(nodes.find((node) => node.role === "cell" && !node.name).column, "启用");
  assert.equal(nodes.find((node) => node.name === "甲").column, "名称");
});

test("an unnamed checkbox cell does not take a data column", () => {
  const nodes = annotateColumns([
    { role: "columnheader", name: "名称", depth: 1 },
    { role: "row", depth: 1 },
    { role: "cell", name: "", depth: 2 },
    { role: "checkbox", name: "", depth: 3 },
    { role: "cell", name: "甲", depth: 2 },
  ]);
  assert.equal(nodes.find((node) => node.role === "checkbox").column, undefined);
  assert.equal(nodes.find((node) => node.name === "甲").column, "名称");
});

test("a nested control keeps its own ref and the cell keeps the column", () => {
  const nodes = parseSnapshotYaml([
    "- row:",
    "  - columnheader \"应填数量\" [ref=e1]",
    "  - columnheader \"已填数量\" [ref=e2]",
    "- row:",
    "  - cell \"19\" [ref=e3]",
    "  - cell \"58\" [ref=e4]:",
    "    - button \"下载\" [ref=e5]",
    "- textbox \"标题\" [ref=e6]: 已填写",
  ].join("\n"));
  const cell = nodes.find((node) => node.ariaRef === "e4");
  const button = nodes.find((node) => node.ariaRef === "e5");
  const field = nodes.find((node) => node.ariaRef === "e6");
  assert.equal(cell.column, "已填数量");
  assert.equal(button.column, undefined);
  assert.equal(button.depth, cell.depth + 1);
  assert.equal(field.value, "已填写");
  const exact = goalExactLines(nodes, "点击已填数量");
  assert.equal(exact.some((node) => node.ariaRef === "e4"), true);
  assert.equal(exact.some((node) => node.ariaRef === "e5"), false);
});

test("repeated row actions keep the row text, and checked or disabled stays on the control", () => {
  const nodes = parseSnapshotYaml([
    "- row:",
    "  - columnheader \"名称\" [ref=e1]",
    "  - columnheader \"操作\" [ref=e2]",
    "- row:",
    "  - cell \"中性笔\" [ref=e3]",
    "  - cell [ref=e4]:",
    "    - button \"删除\" [ref=e5]",
    "- row:",
    "  - cell \"钢笔\" [ref=e6]",
    "  - cell [ref=e7]:",
    "    - button \"删除\" [ref=e8]",
    "- checkbox \"同意\" [checked] [disabled] [ref=e9]",
    "- textbox \"名称\" [ref=e10]:",
  ].join("\n"));
  const first = nodes.find((node) => node.ariaRef === "e5");
  const second = nodes.find((node) => node.ariaRef === "e8");
  const box = nodes.find((node) => node.ariaRef === "e9");
  const field = nodes.find((node) => node.ariaRef === "e10");
  assert.match(first.row, /中性笔/);
  assert.match(second.row, /钢笔/);
  assert.equal(first.row === second.row, false);
  assert.deepEqual(box.states, ["checked", "disabled"]);
  assert.match(controlText(first, { row: true }), /row="中性笔"/);
  assert.match(controlText(box), /\[checked\] \[disabled\]/);
  const exact = goalExactLines(nodes, "删除 名称");
  assert.equal(exact.filter((node) => node.role === "button").length, 2);
  assert.equal(exact.some((node) => node.role === "textbox"), true);
  assert.equal(field.role, "textbox");
  const columnLine = `- ${controlText(nodes.find((node) => node.ariaRef === "e3"))} ref=f0:e3@1`;
  assert.match(sameColumnRefs(`${columnLine}\n`, 'columnheader "名称"')[0], /f0:e3@1/);
});

test("a later cell still separates two rows that share the earlier cells", () => {
  const nodes = parseSnapshotYaml([
    "- row:",
    "  - cell \"中性笔\" [ref=e1]",
    "  - cell \"办公\" [ref=e2]",
    "  - cell \"甲\" [ref=e3]",
    "  - cell \"草稿\" [ref=e4]",
    "  - cell [ref=e5]:",
    "    - button \"编辑\" [ref=e6]",
    "- row:",
    "  - cell \"中性笔\" [ref=e7]",
    "  - cell \"办公\" [ref=e8]",
    "  - cell \"甲\" [ref=e9]",
    "  - cell \"已提交\" [ref=e10]",
    "  - cell [ref=e11]:",
    "    - button \"编辑\" [ref=e12]",
  ].join("\n"));
  const first = nodes.find((node) => node.ariaRef === "e6");
  const second = nodes.find((node) => node.ariaRef === "e12");
  assert.match(first.row, /草稿/);
  assert.match(second.row, /已提交/);
  assert.equal(first.row === second.row, false);
});

test("an earlier cell keeps the finished row, not only the names seen so far", () => {
  const nodes = parseSnapshotYaml([
    "- row:",
    "  - cell \"\" [ref=e1]",
    "  - cell \"中性笔\" [ref=e2]",
    "- row:",
    "  - cell \"\" [ref=e3]",
    "  - cell \"钢笔\" [ref=e4]",
  ].join("\n"));
  const first = nodes.find((node) => node.ariaRef === "e1");
  const third = nodes.find((node) => node.ariaRef === "e3");
  assert.match(first.row, /中性笔/);
  assert.match(third.row, /钢笔/);
  assert.equal(first.row === third.row, false);
});

test("action buttons in a later table keep the named row at the same index", () => {
  const nodes = parseSnapshotYaml([
    "- table:",
    "  - rowgroup:",
    "    - row:",
    "      - cell \"中性笔\" [ref=e1]",
    "    - row:",
    "      - cell \"钢笔\" [ref=e2]",
    "- table:",
    "  - rowgroup:",
    "    - row:",
    "      - cell:",
    "        - button \"编辑\" [ref=e3]",
    "    - row:",
    "      - cell:",
    "        - button \"编辑\" [ref=e4]",
  ].join("\n"));
  const first = nodes.find((node) => node.ariaRef === "e3");
  const second = nodes.find((node) => node.ariaRef === "e4");
  assert.match(first.row, /中性笔/);
  assert.match(second.row, /钢笔/);
  assert.equal(first.row === second.row, false);
});

test("action buttons in a parallel table keep the named row at the same index", () => {
  const nodes = parseSnapshotYaml([
    "- rowgroup:",
    "  - row:",
    "    - columnheader \"物品名称\" [ref=e1]",
    "- rowgroup:",
    "  - row:",
    "    - cell \"中性笔\" [ref=e2]",
    "  - row:",
    "    - cell \"钢笔\" [ref=e3]",
    "- rowgroup:",
    "  - row:",
    "    - columnheader \"操作\" [ref=e4]",
    "- rowgroup:",
    "  - row:",
    "    - cell [ref=e5]:",
    "      - button \"编辑\" [ref=e6]",
    "  - row:",
    "    - cell [ref=e7]:",
    "      - button \"编辑\" [ref=e8]",
  ].join("\n"));
  const first = nodes.find((node) => node.ariaRef === "e6");
  const second = nodes.find((node) => node.ariaRef === "e8");
  assert.match(first.row, /中性笔/);
  assert.match(second.row, /钢笔/);
  assert.equal(first.row === second.row, false);
});

test("a numeric cell whose name also contains another word is not the column target", () => {
  const nodes = parseSnapshotYaml([
    "- row:",
    "  - columnheader \"已填数量\" [ref=e1]",
    "- row:",
    "  - cell \"1 下载\" [ref=e2]",
    "- row:",
    "  - cell \"0\" [ref=e3]",
    "- row:",
    "  - cell \"张三 经理\" [ref=e4]",
  ].join("\n"));
  const merged = nodes.find((node) => node.ariaRef === "e2");
  const plain = nodes.find((node) => node.ariaRef === "e3");
  const text = nodes.find((node) => node.ariaRef === "e4");
  assert.equal(merged.merged, true);
  assert.equal(plain.merged, undefined);
  assert.equal(text.merged, undefined);
  assert.match(controlText(merged), /merged/);
  assert.match(controlText(merged), /已填数量$/);
  const exact = goalExactLines(nodes, "已填数量");
  assert.equal(exact.some((node) => node.ariaRef === "e2"), false);
  assert.equal(exact.some((node) => node.ariaRef === "e3"), true);
});

test("a shell page is not treated as ready until a control appears", () => {
  assert.equal(snapshotHasControl("epoch 1 snap 1\nframe f0 https://example.test/\n- generic [active]: 标题 ref=f0:e1@1"), false);
  assert.equal(snapshotHasControl('- textbox placeholder="请输入标题" ref=f0:e2@1'), true);
  assert.equal(snapshotHasControl('- cell "19" 应填数量 ref=f0:e3@1'), true);
  assert.equal(snapshotHasControl('- spinbutton "数量" ref=f0:e4@1'), true);
  assert.equal(snapshotHasControl('- slider "进度" ref=f0:e5@1'), true);
});

test("a textbox keeps its placeholder when the accessible name is empty", () => {
  const nodes = parseSnapshotYaml([
    "- textbox [ref=e1]:",
    "  - /placeholder: 请输入标题",
    "- textbox \"名称\" [ref=e2]:",
    "  - /placeholder: \"请输入名称\"",
  ].join("\n"));
  const unnamed = nodes.find((node) => node.ariaRef === "e1");
  const named = nodes.find((node) => node.ariaRef === "e2");
  assert.equal(unnamed.name, "");
  assert.equal(unnamed.placeholder, "请输入标题");
  assert.equal(named.placeholder, "请输入名称");
  assert.match(controlText(unnamed), /placeholder="请输入标题"/);
  const exact = goalExactLines(nodes, "请输入标题");
  assert.equal(exact.some((node) => node.ariaRef === "e1"), true);
  assert.equal(exact.some((node) => node.ariaRef === "e2"), false);
});

test("a container is not the target when the same name is on a control inside it", () => {
  const nodes = parseSnapshotYaml([
    "- generic \"日报\" [ref=e1]:",
    "  - tab \"日报\" [ref=e2]",
    "- button \"新增办公用品\" [ref=e3]",
  ].join("\n"));
  const exact = goalExactLines(nodes, "日报 新增办公用品");
  assert.deepEqual(exact.map((node) => node.role), ["tab", "button"]);
});

test("two frames with the same address keep a stable order", () => {
  const frame = (guid) => ({ _guid: guid, name: () => "", url: () => "https://example.test/embed", parentFrame: () => null });
  const left = frame("b");
  const right = frame("a");
  assert.notEqual(frameSortKey(left), frameSortKey(right));
  assert.ok(frameSortKey(right) < frameSortKey(left));
});

test("a frame address keeps the page and drops the query string", () => {
  assert.equal(frameLabel("https://example.test/app/?token=abc#/items"), "https://example.test/app/#/items");
  assert.equal(frameLabel("https://example.test/app/#/items"), "https://example.test/app/#/items");
});

test("the same name in a dialog is not numbered with the page control", () => {
  const nodes = parseSnapshotYaml([
    "- textbox \"物品编码\" [ref=e1]",
    "- dialog \"dialog\" [ref=e2]:",
    "  - textbox \"物品编码\" [ref=e3]",
  ].join("\n"));
  annotateDuplicatePaths(nodes);
  const page = nodes.find((node) => node.ariaRef === "e1");
  const field = nodes.find((node) => node.ariaRef === "e3");
  assert.equal(page.row || "", "");
  assert.equal(field.row || "", "");
  assert.equal(field.popup, "dialog");
});

test("duplicate names keep an ancestor path, and identical paths are numbered", () => {
  const nodes = [
    { role: "treeitem", name: "公司", depth: 1 },
    { role: "treeitem", name: "部门", depth: 2 },
    { role: "treeitem", name: "公司", depth: 1 },
    { role: "treeitem", name: "部门", depth: 2 },
    { role: "button", name: "编辑", depth: 2, row: "甲" },
    { role: "button", name: "编辑", depth: 2, row: "甲" },
  ];
  annotateDuplicatePaths(nodes);
  assert.equal(nodes[1].row, "公司 #1");
  assert.equal(nodes[3].row, "公司 #2");
  assert.equal(nodes[0].row, "#1");
  assert.equal(nodes[2].row, "#2");
  assert.equal(nodes[4].row, "甲 #1");
  assert.equal(nodes[5].row, "甲 #2");
});

test("a leading code still matches the name written in the goal", () => {
  const nodes = parseSnapshotYaml([
    "- generic \"A05000000 家具和用具\" [ref=e1]",
    "- generic \"其他\" [ref=e2]",
  ].join("\n"));
  const exact = goalExactLines(nodes, "选择家具和用具");
  assert.deepEqual(exact.map((node) => node.ariaRef), ["e1"]);
});

test("a required mark and inner spaces still match the goal", () => {
  const nodes = [
    { role: "button", name: "* 确 认" },
    { role: "button", name: "取 消" },
  ];
  const exact = goalExactLines(nodes, "点击确认");
  assert.deepEqual(exact.map((node) => node.name), ["* 确 认"]);
});

test("spaces inside a control name still match the goal", () => {
  const nodes = [
    { role: "button", name: "确 认" },
    { role: "button", name: "取 消" },
    { role: "generic", name: "办 公" },
    { role: "button", name: "新增办公用品" },
  ];
  const exact = goalExactLines(nodes, "新增办公用品后点击确认");
  assert.deepEqual(exact.map((node) => node.name), ["确 认", "新增办公用品"]);
});

test("a required mark does not hide the field named in the goal", () => {
  const nodes = [
    { role: "textbox", name: "* 名称", depth: 1 },
    { role: "textbox", name: "备注", depth: 1 },
  ];
  const exact = goalExactLines(nodes, "填写名称");
  assert.deepEqual(exact.map((node) => node.name), ["* 名称"]);
});

test("a longer name in the goal does not also select its pieces, and a column keeps every cell", () => {
  const nodes = [
    { role: "button", name: "新增办公用品" },
    { role: "generic", name: "办公" },
    { role: "button", name: "用品" },
    { role: "button", name: "编辑" },
    { role: "cell", name: "19", column: "应填数量" },
  ];
  const exact = goalExactLines(nodes, "新增办公用品  编辑，点击应填数量");
  assert.deepEqual(exact.map((node) => node.name), ["新增办公用品", "编辑", "19"]);
  const lines = Array.from({ length: 9 }, (_, index) => `- cell "${index}" 应填数量 ref=f0:e${index}@1`);
  const column = sameColumnRefs(lines.join("\n"), 'columnheader "应填数量"');
  assert.equal(column.length, 9);
  assert.match(column[8], /f0:e8@1/);
});

test("a control keeps the required mark and the range it declares", () => {
  const line = controlText({
    role: "spinbutton",
    name: "数量",
    states: ["required"],
    min: "0",
    max: "10",
    step: "1",
    value: "3",
  });
  assert.match(line, /\[required\]/);
  assert.match(line, /min=0 max=10 step=1/);
  assert.match(line, /: 3/);
});

test("a text control's value is read from the element, and a password is not", async () => {
  const typed = {
    getAttribute: async (name) => (name === "type" ? "text" : null),
    inputValue: async () => "甲",
  };
  assert.equal(await readControlValue(typed), "甲");
  const secret = {
    getAttribute: async (name) => (name === "type" ? "password" : null),
    inputValue: async () => "secret",
  };
  assert.equal(await readControlValue(secret), "");
});

test("a new sibling name next to the focused field is the applied value", () => {
  const field = { role: "combobox", name: "类别", placeholder: "" };
  const previous = [{ ...field, depth: 2, value: "" }];
  const next = [{ ...field, depth: 2, value: "" }, { role: "generic", name: "甲", depth: 2 }];
  assert.equal(siblingValue(previous, next, field), "甲");
  assert.equal(siblingValue(next, next, field), "");
  const two = [
    { ...field, depth: 2 },
    { ...field, depth: 4, states: ["active"] },
  ];
  const after = [
    { ...field, depth: 2 },
    { role: "generic", name: "乙", depth: 2 },
    { ...field, depth: 4, states: ["active"] },
    { role: "generic", name: "甲", depth: 4 },
  ];
  assert.equal(siblingValue(two, after, field), "甲");
});

test("a sibling value stays on the field in the same popup", () => {
  const field = { role: "combobox", name: "类别", placeholder: "", popup: "提示", depth: 3 };
  const page = { role: "combobox", name: "类别", placeholder: "", popup: "", depth: 1 };
  const before = [page, field];
  const after = [
    page,
    { role: "generic", name: "页面项", depth: 1 },
    field,
    { role: "generic", name: "文具", depth: 3 },
  ];
  assert.equal(siblingValue(before, after, field), "文具");
  assert.equal(siblingValue(before, after, page), "页面项");
});

test("a popup row is a choice, and the control that opened it is not", () => {
  assert.equal(choiceClick("generic \"文具类\""), true);
  assert.equal(choiceClick("cell \"0\" 已填数量"), true);
  assert.equal(choiceClick("option \"消耗品\""), true);
  assert.equal(choiceClick("combobox \"* 类别\""), false);
  assert.equal(choiceClick("button \"取 消\""), false);
  assert.equal(choiceClick("spinbutton \"数量\""), false);
  assert.equal(choiceClick("slider \"进度\""), false);
  assert.equal(choiceClick("columnheader \"名称\""), false);
});

test("one nearby name is the combobox value, and an open list is not", () => {
  const nodes = [
    { role: "combobox", name: "类别", value: "", depth: 2 },
    { role: "generic", name: "文具", depth: 2 },
    { role: "combobox", name: "状态", value: "", depth: 2 },
    { role: "generic", name: "甲", depth: 3 },
    { role: "generic", name: "乙", depth: 3 },
    { role: "combobox", name: "已有", value: "原值", depth: 2 },
    { role: "generic", name: "别的", depth: 2 },
    { role: "combobox", name: "内文", value: "", depth: 2 },
    { role: "generic", name: "选中", depth: 3 },
  ];
  applyDisplayedValues(nodes);
  assert.equal(nodes[0].value, "文具");
  assert.equal(nodes[2].value, "");
  assert.equal(nodes[5].value, "原值");
  assert.equal(nodes[7].value, "选中");
});

test("a choice keeps only the value still on the control after the snapshot settles", () => {
  const field = { role: "combobox", name: "类别", placeholder: "", popup: "", before: "", value: "甲" };
  assert.equal(keptFocusValue(field, [{ role: "combobox", name: "类别", placeholder: "", popup: "", value: "甲" }]), "甲");
  assert.equal(keptFocusValue(field, [{ role: "combobox", name: "类别", placeholder: "", popup: "", value: "" }]), "");
  assert.equal(keptFocusValue({ ...field, before: "甲" }, [{ role: "combobox", name: "类别", placeholder: "", popup: "", value: "甲" }]), "");
});

test("a nameless container around a named ref is not itself a target", () => {
  const nodes = [
    { role: "generic", name: "", ref: "f0:e1@1", depth: 1 },
    { role: "button", name: "确定", ref: "f0:e2@1", depth: 2 },
    { role: "generic", name: "", ref: "f0:e3@1", depth: 1 },
    { role: "generic", name: "拖拽文件", ref: "f0:e4@1", depth: 1 },
    { role: "button", name: "关闭", ref: "f0:e5@1", depth: 2 },
    { role: "generic", name: "", ref: "f0:e6@1", depth: 1 },
    { role: "radio", name: "日报", ref: "", depth: 2 },
    { role: "generic", name: "", ref: "f0:e7@1", depth: 1 },
  ];
  assert.equal(wrapperGeneric(nodes, 0), true);
  assert.equal(wrapperGeneric(nodes, 2), false);
  assert.equal(wrapperGeneric(nodes, 4), false);
  assert.equal(wrapperGeneric(nodes, 5), false);
  assert.equal(wrapperGeneric(nodes, 7), false);
});

test("a named control without a ref stays visible and is not clickable", () => {
  assert.equal(includeInSnapshot({ role: "button", name: "确定", ariaRef: "" }), true);
  assert.equal(includeInSnapshot({ role: "generic", name: "文具", ariaRef: "" }, "选择文具"), true);
  assert.equal(includeInSnapshot({ role: "generic", name: "页脚", ariaRef: "" }, "选择文具"), false);
  assert.equal(includeInSnapshot({ role: "generic", name: "", ariaRef: "" }), false);
  assert.equal(includeInSnapshot({ role: "button", name: "确定", ariaRef: "e4" }), true);
  assert.equal(refTail(""), "");
  assert.equal(refTail("f0:e4@2"), " ref=f0:e4@2");
});

test("clicking a choice that leaves the focused field unchanged is reported", () => {
  const before = [{ role: "textbox", name: "开始日期", placeholder: "请选择开始日期", value: "", states: ["active"], shown: "textbox \"开始日期\"" }];
  const same = [{ role: "textbox", name: "开始日期", placeholder: "请选择开始日期", value: "", states: ["active"], shown: "textbox \"开始日期\"", ref: "f0:e2@2" }];
  const applied = [{ role: "textbox", name: "开始日期", placeholder: "请选择开始日期", value: "2026-09-28", states: [], shown: "textbox \"开始日期\"", ref: "f0:e2@2" }];
  assert.equal(focusedUnchanged(before, same)[0].ref, "f0:e2@2");
  assert.equal(focusedUnchanged(before, applied).length, 0);
});

test("a number control uses the value it exposes, and a text control keeps the snapshot text", () => {
  assert.equal(settledControlValue("spinbutton", "(10.00)", "10"), "10");
  assert.equal(settledControlValue("slider", "50%", "50"), "50");
  assert.equal(settledControlValue("spinbutton", "10", ""), "10");
  assert.equal(settledControlValue("textbox", "已填", "已填"), "已填");
  assert.equal(settledControlValue("textbox", "", "已填"), "已填");
});

test("opening a list is not a value, and checking a box is", () => {
  const box = { frameId: "f0", role: "combobox", name: "类别", value: "", shown: "combobox \"类别\"", ref: "f0:e1@1" };
  assert.equal(changedControls([box], [{ ...box, states: ["expanded"], ref: "f0:e1@2" }]).length, 0);
  assert.equal(changedControls([{ ...box, states: ["expanded"] }], [{ ...box, ref: "f0:e1@3" }]).length, 0);
  const chosen = changedControls([{ ...box, states: ["expanded"] }], [{ ...box, value: "甲", ref: "f0:e1@4" }]);
  assert.equal(chosen.length, 1);
  assert.equal(chosen[0].before, "");
  assert.equal(chosen[0].after, "甲");
  const check = changedControls(
    [{ frameId: "f0", role: "checkbox", name: "启用", value: "", shown: "checkbox \"启用\"", ref: "f0:e2@1" }],
    [{ frameId: "f0", role: "checkbox", name: "启用", value: "", states: ["checked"], shown: "checkbox \"启用\"", ref: "f0:e2@2" }],
  );
  assert.equal(check[0].after, "checked");
});

test("a kept field value is not reported twice when the same name sits beside it", () => {
  const field = { role: "combobox", name: "类别", placeholder: "", popup: "dialog", label: "combobox \"类别\"", before: "", value: "" };
  const previous = [{ role: "combobox", name: "类别", placeholder: "", popup: "dialog", value: "" }];
  const after = [
    { role: "combobox", name: "类别", placeholder: "", popup: "dialog", value: "甲" },
    { role: "generic", name: "甲", placeholder: "", popup: "dialog" },
  ];
  const report = focusAfterChoice([field], previous, after);
  assert.deepEqual(report.focused_value.map((item) => item.value), ["甲"]);
  const empty = { ...field, popup: "" };
  const beside = focusAfterChoice(
    [empty],
    [{ role: "combobox", name: "类别", value: "" }],
    [{ role: "combobox", name: "类别", value: "" }, { role: "generic", name: "甲" }],
  );
  assert.deepEqual(beside.focused_value.map((item) => item.value), ["甲"]);
  const dropped = focusAfterChoice([ { ...empty, value: "临时" } ], [{ role: "combobox", name: "类别", value: "" }], [{ role: "combobox", name: "类别", value: "" }]);
  assert.equal(dropped.focused_value, undefined);
});

test("a new day cell is reported ahead of nameless containers, and focus alone is not a change", () => {
  const before = [
    { frameId: "f0", role: "generic", name: "", value: "车辆管理", shown: "generic", ref: "f0:e1@1" },
    { frameId: "f0", role: "textbox", name: "开始日期", value: "", shown: "textbox \"开始日期\"", ref: "f0:e2@1" },
  ];
  const after = [
    { frameId: "f0", role: "generic", name: "", value: "印章管理", shown: "generic", ref: "f0:e1@2" },
    { frameId: "f0", role: "textbox", name: "开始日期", value: "", states: ["active"], shown: "textbox \"开始日期\"", ref: "f0:e2@2" },
    { frameId: "f0", role: "cell", name: "6", column: "日", shown: "cell \"6\" 日", ref: "f0:e9@2" },
  ];
  const changed = changedControls(before, after);
  assert.equal(changed[0].ref, "f0:e9@2");
  assert.equal(changed.some((item) => item.after === "印章管理"), false);
  assert.equal(changed.some((item) => String(item.after).includes("active")), false);
});

test("a dialog button is new even when the page already has the same name", () => {
  const page = { frameId: "f0", role: "button", name: "确定", value: "", shown: "button \"确定\"", popup: "" };
  const before = [{ ...page, ref: "f0:e1@1" }];
  const after = [
    { ...page, ref: "f0:e8@2", popup: "提示" },
    { ...page, ref: "f0:e1@2" },
  ];
  const changed = changedControls(before, after);
  assert.equal(changed.length, 1);
  assert.equal(changed[0].ref, "f0:e8@2");
  assert.match(changed[0].label, /popup="提示"/);
});

test("a menu or list inside the page is not a popup layer", () => {
  const nodes = parseSnapshotYaml([
    "- navigation \"侧栏\" [ref=e1]:",
    "  - menu \"菜单\" [ref=e2]:",
    "    - button \"确定\" [ref=e3]",
    "- main \"正文\" [ref=e4]:",
    "  - listbox \"项目\" [ref=e5]:",
    "    - option \"确定\" [ref=e6]",
    "  - dialog \"提示\" [ref=e7]:",
    "    - button \"确定\" [ref=e8]",
    "- menu \"浮层\" [ref=e9]:",
    "  - button \"确定\" [ref=e10]",
  ].join("\n"));
  const popupOf = (ref) => nodes.find((node) => node.ariaRef === ref).popup;
  assert.equal(popupOf("e3"), "");
  assert.equal(popupOf("e6"), "");
  assert.equal(popupOf("e8"), "提示");
  assert.equal(popupOf("e10"), "浮层");
});

test("a button inside a dialog keeps that dialog, and a cell value does not become one", () => {
  const nodes = parseSnapshotYaml([
    "- dialog \"提示\" [ref=e1]:",
    "  - button \"确定\" [ref=e2]",
    "- row \"甲\" [ref=e3]:",
    "  - cell \"甲\" [ref=e4]",
    "  - button \"确定\" [ref=e5]",
  ].join("\n"));
  const dialogButton = nodes.find((node) => node.ariaRef === "e2");
  const rowButton = nodes.find((node) => node.ariaRef === "e5");
  assert.equal(dialogButton.popup, "提示");
  assert.match(controlText(dialogButton), /button "确定" popup="提示"/);
  assert.equal(rowButton.popup, "");
  assert.doesNotMatch(controlText(rowButton), /popup=/);
  assert.equal(annotatePopup([{ role: "button", name: "确定", depth: 0 }])[0].popup, "");
});

test("a row label change is not a new button", () => {
  const button = { frameId: "f0", role: "button", name: "编辑", value: "", shown: "button \"编辑\"" };
  const before = [{ ...button, row: "甲 | 1", ref: "f0:e1@1" }];
  const after = [
    { ...button, row: "甲 | 2", ref: "f0:e4@2" },
    { frameId: "f0", role: "option", name: "文具", value: "", shown: "option \"文具\"", ref: "f0:e9@2" },
  ];
  const changed = changedControls(before, after);
  assert.equal(changed.some((item) => String(item.label).includes("编辑")), false);
  assert.equal(changed[0].ref, "f0:e9@2");
});

test("a later snapshot reports the control whose value changed and a control that appeared", () => {
  const before = [{ frameId: "f0", role: "textbox", name: "开始日期", value: "", column: "", shown: "textbox \"开始日期\"", ref: "f0:e1@1" }];
  const after = [
    { frameId: "f0", role: "textbox", name: "开始日期", value: "2026-09-26", column: "", shown: "textbox \"开始日期\"", ref: "f0:e8@2" },
    { frameId: "f0", role: "button", name: "确 认", value: "", column: "", shown: "button \"确 认\"", ref: "f0:e9@2" },
  ];
  const changed = changedControls(before, after);
  assert.equal(changed[0].ref, "f0:e8@2");
  assert.equal(changed[0].before, "");
  assert.equal(changed[0].after, "2026-09-26");
  assert.equal(changed[1].label, "button \"确 认\"");
  assert.equal(changedControls([], after).length, 0);
});
