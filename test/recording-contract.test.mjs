import assert from "node:assert/strict";
import test from "node:test";
import { annotateColumns, goalExactLines, lineRef, parseNodes, publishedRef, publishSnapshotYaml, sameColumnRefs } from "../src/browser/snapshot.mjs";
import { citationErrors, keysFromPostData, requestIndexRow } from "../src/skillpack/request-keys.mjs";

test("iframe aria refs from the page snapshot publish as fN:eN", () => {
  assert.equal(publishedRef("f0", "e12", 3), "f0:e12@3");
  assert.equal(publishedRef("f0", "f1e4", 3), "f1:e4@3");
  assert.equal(publishedRef("f1", "e4", 3), "f1:e4@3");
  assert.equal(publishedRef("f1", "f1e4", 3), "f1:e4@3");
});

test("cells inherit the header name as a column fact", () => {
  const nodes = annotateColumns([
    { role: "columnheader", name: "应填数量" },
    { role: "columnheader", name: "已填数量" },
    { role: "row", name: "" },
    { role: "cell", name: "2" },
    { role: "cell", name: "0" },
  ]);
  const cells = nodes.filter((node) => node.role === "cell");
  assert.equal(cells[0].column, "应填数量");
  assert.equal(cells[1].column, "已填数量");
});

test("goal_exact lists cells whose column appears in the goal, not the header", () => {
  const nodes = annotateColumns([
    { role: "columnheader", name: "应填数量" },
    { role: "columnheader", name: "已填数量" },
    { role: "row", name: "" },
    { role: "cell", name: "2" },
    { role: "cell", name: "0" },
  ]).map((node, index) => ({
    ...node,
    shown: node.column ? `${node.role} "${node.name}" ${node.column}` : `${node.role} "${node.name}"`,
    ref: `f0:e${index}@1`,
  }));
  const exact = goalExactLines(nodes, "点击应填数量 已填数量的数字部分");
  assert.equal(exact.some((node) => node.role === "columnheader"), false);
  const cells = exact.filter((node) => node.role === "cell");
  assert.equal(cells.length, 2);
  assert.equal(cells[0].column, "应填数量");
  assert.equal(cells[1].column, "已填数量");
});

test("named controls inside a cell inherit the column fact", () => {
  const nodes = parseNodes([
    '- columnheader "应填数量" [ref=e1]',
    '- columnheader "已填数量" [ref=e2]',
    "- row [ref=e3]:",
    "  - cell [ref=e4]:",
    '    - button "12" [ref=e5]',
    "  - cell [ref=e6]:",
    '    - button "3" [ref=e7]',
  ].join("\n"));
  const twelve = nodes.find((node) => node.name === "12");
  const three = nodes.find((node) => node.name === "3");
  assert.equal(twelve.column, "应填数量");
  assert.equal(three.column, "已填数量");
  const exact = goalExactLines(
    nodes.map((node) => ({ ...node, shown: `${node.role} "${node.name}" ${node.column || ""}`.trim(), ref: node.ariaRef })),
    "点击应填数量的数字部分",
  );
  assert.equal(exact.some((node) => node.role === "columnheader"), false);
  assert.equal(exact.some((node) => node.role === "cell" && !node.name), false);
  assert.equal(exact.some((node) => node.name === "12" && node.column === "应填数量"), true);
});

test("a second table's headers replace the first table's columns", () => {
  const nodes = parseNodes([
    '- columnheader "周一" [ref=e1]',
    '- columnheader "周二" [ref=e2]',
    "- row [ref=e3]:",
    "  - cell [ref=e4]:",
    '    - button "1" [ref=e5]',
    "  - cell [ref=e6]:",
    '    - button "2" [ref=e7]',
    '- columnheader "应填数量" [ref=e8]',
    '- columnheader "已填数量" [ref=e9]',
    "- row [ref=e10]:",
    "  - cell [ref=e11]:",
    '    - button "12" [ref=e12]',
    "  - cell [ref=e13]:",
    '    - button "3" [ref=e14]',
  ].join("\n"));
  assert.equal(nodes.find((node) => node.name === "1").column, "周一");
  assert.equal(nodes.find((node) => node.name === "12").column, "应填数量");
  const exact = goalExactLines(
    nodes.map((node) => ({
      ...node,
      shown: `${node.role} "${node.name}" ${node.column || ""}`.trim(),
      ref: node.ariaRef,
    })),
    "点击应填数量的数字部分",
  );
  assert.equal(exact.some((node) => node.name === "1" || node.column === "周一"), false);
  assert.equal(exact.some((node) => node.name === "12" && node.column === "应填数量"), true);
});

test("clicking a column header with no request exposes the cells in that column", () => {
  const snapshot = [
    "epoch 1 snap 2",
    '- columnheader "应填数量" [ref=f0:e1@2]',
    '- cell "2" [ref=f0:e3@2] [column=应填数量]',
    '- cell "0" [ref=f0:e4@2] [column=已填数量]',
  ].join("\n");
  const refs = sameColumnRefs(snapshot, 'columnheader "应填数量"');
  assert.deepEqual(refs, ["f0:e3@2"]);
});

test("yaml tree keeps indent and rewrites refs without flattening", () => {
  const yaml = [
    "- table [ref=e1]:",
    "  - row [ref=e2]:",
    '    - columnheader "应填数量" [ref=e3]',
    "    - cell [ref=e4]:",
    '      - button "12" [ref=e5]',
  ].join("\n");
  const published = publishSnapshotYaml(yaml, "f0", 0, 1);
  assert.match(published.text, /^ {2}- row \[ref=f0:e2@1\]:/m);
  assert.match(published.text, /button "12" \[ref=f0:e5@1\] \[column=应填数量\]/);
  assert.equal(published.nodes.some((node) => node.ref === "f0:e5@1" && node.column === "应填数量"), true);
  assert.equal(lineRef('      - button "12" [ref=f0:e5@1] [column=应填数量]'), "f0:e5@1");
});

test("trailing quoted cell text does not keep the quote marks in the name", () => {
  const yaml = [
    '- columnheader "已填数量" [ref=e1]',
    "- row [ref=e2]:",
    '  - cell "2" [ref=e3]:',
    '    - generic [ref=e4]: "2"',
  ].join("\n");
  const nodes = parseNodes(yaml);
  assert.equal(nodes.find((node) => node.ariaRef === "e4").name, "2");
  assert.equal(nodes.find((node) => node.ariaRef === "e4").parentRole, "cell");
  const exact = goalExactLines(
    nodes.map((node) => ({
      ...node,
      shown: node.column ? `${node.role} "${node.name}" ${node.column}` : `${node.role} "${node.name}"`,
      ref: node.ariaRef,
    })),
    "点击已填数量的数字部分",
  );
  assert.equal(exact.some((node) => node.role === "generic"), false);
  assert.equal(exact.some((node) => node.role === "cell" && node.name === "2"), true);
});

test("goal_exact lists the cell when the number lives on an inner generic", () => {
  const yaml = [
    '- columnheader "应填数量" [ref=e1]',
    "- row [ref=e2]:",
    "  - cell [ref=e3]:",
    '    - generic [ref=e4]: "38"',
  ].join("\n");
  const published = publishSnapshotYaml(yaml, "f0", 0, 1);
  const exact = goalExactLines(
    published.nodes.map((item) => ({
      role: item.role,
      name: item.name,
      column: item.column,
      parentRole: item.parentRole,
      inCell: item.inCell,
      shown: item.shown,
      ref: item.ref,
    })),
    "点击应填数量的数字部分",
  );
  assert.equal(exact.some((node) => node.role === "generic"), false);
  assert.equal(exact.some((node) => node.role === "cell" && node.ref === "f0:e3@1"), true);
});

test("goal_exact does not list nested generics inside a cell", () => {
  const yaml = [
    '- columnheader "已填数量" [ref=e1]',
    "- row [ref=e2]:",
    '  - cell "2 下载" [ref=e3]:',
    "    - generic [ref=e4]:",
    '      - generic [ref=e5]: "2"',
    "      - generic [ref=e6]: 下载",
  ].join("\n");
  const published = publishSnapshotYaml(yaml, "f0", 0, 1);
  const exact = goalExactLines(published.nodes, "点击已填数量的数字部分");
  assert.equal(exact.some((node) => node.role === "generic"), false);
  assert.equal(exact.some((node) => node.role === "cell" && node.ref === "f0:e3@1"), true);
});

test("yaml names come from quotes, trailing text, placeholder, and unnamed text children", () => {
  const yaml = [
    '- generic [ref=e1]:',
    "  - text: 组织机构",
    '- textbox [ref=e2]:',
    '  - /placeholder: "请输入部门名称"',
    '- radio "日报" [ref=e3]',
    "- generic [ref=e4]: 周报",
    "    - 'button \"新增\" [ref=e5]'",
  ].join("\n");
  const nodes = parseNodes(yaml);
  assert.equal(nodes.find((node) => node.ariaRef === "e1").name, "组织机构");
  assert.equal(nodes.find((node) => node.ariaRef === "e2").name, "请输入部门名称");
  assert.equal(nodes.find((node) => node.ariaRef === "e3").name, "日报");
  assert.equal(nodes.find((node) => node.ariaRef === "e4").name, "周报");
  assert.equal(nodes.find((node) => node.ariaRef === "e5").name, "新增");
});

test("goal_exact names the control in the goal, not a nearby search box", () => {
  const nodes = parseNodes([
    "- generic [ref=e1]:",
    "  - text: 组织机构",
    "- textbox [ref=e2]:",
    '  - /placeholder: "请输入部门名称"',
    '- radio "日报" [ref=e3]',
  ].join("\n")).map((node) => ({
    ...node,
    shown: `${node.role} "${node.name}"`,
    ref: `f0:${node.ariaRef}@1`,
  }));
  const exact = goalExactLines(nodes, "点击组织机构 日报");
  assert.deepEqual(exact.map((node) => node.name), ["组织机构", "日报"]);
});

test("a path the script never saw is not in evidence", () => {
  const errors = citationErrors("get('/api/missing')\n", [{ path: "/api/items", keys: ["q"] }], "scripts/client.py");
  assert.equal(errors.some((item) => item.code === "path_not_in_evidence" && item.path === "/api/missing"), true);
});

test("a cited path must write each captured key in the client", () => {
  const script = "def list_items():\n    return get('/api/items')\n";
  const errors = citationErrors(script, [{ id: "req_a", path: "/api/items", keys: ["deptId"] }], "scripts/client.py");
  assert.equal(errors.some((item) => item.code === "key_not_written" && item.key === "deptId"), true);
});

test("keys written in the same client pass citation", () => {
  const script = "def list_items():\n    return get('/api/items', deptId=deptId)\n";
  const errors = citationErrors(script, [{ id: "req_a", path: "/api/items", keys: ["deptId"] }], "scripts/client.py");
  assert.equal(errors.length, 0);
});

test("keys next to a path table still count as written", () => {
  const script = [
    "PATHS = {\"list\": \"/api/items\"}",
    "def list_items():",
    "    return get(PATHS[\"list\"], params={\"deptId\": dept_id})",
    "",
  ].join("\n");
  const errors = citationErrors(script, [{ id: "req_a", path: "/api/items", keys: ["deptId"] }], "scripts/client.py");
  assert.equal(errors.length, 0);
});

test("a pathname in a recorded response is in evidence", () => {
  const api = "file /admin-api/infra/file/29/get/20261002/x/attachment.png\n";
  const errors = citationErrors(api, [{
    id: "req_u",
    path: "/admin-api/infra/file/upload",
    keys: [],
    response_body: "{\"data\":\"http://127.0.0.1/admin-api/infra/file/29/get/20261002/x/attachment.png\"}",
  }], "references/api.md", { requireKeys: false });
  assert.equal(errors.length, 0);
});

test("handbook path citation does not demand every captured key", () => {
  const api = "GET /api/items\nevidence req_a\n";
  const errors = citationErrors(api, [{ id: "req_a", path: "/api/items", keys: ["deptId", "pageNo"] }], "references/api.md", { requireKeys: false });
  assert.equal(errors.length, 0);
});

test("form-urlencoded bodies expose keys as request facts", () => {
  assert.deepEqual(keysFromPostData("deptId=103&reportType=1").sort(), ["deptId", "reportType"]);
  const row = requestIndexRow({
    id: "req_1",
    method: "POST",
    path: "/api/save",
    post_data: "title=hello&remark=",
    query: "",
  });
  assert.ok(row.keys.includes("title"));
  assert.ok(row.keys.includes("remark"));
  assert.deepEqual(row.empty, ["remark"]);
});

test("a credential-issuing response exposes issued field names", () => {
  const row = requestIndexRow({
    id: "req_2",
    method: "POST",
    path: "/auth/refresh",
    query: "refreshToken=r1",
    post_data: "",
    response_body: JSON.stringify({ data: { accessToken: "a", refreshToken: "b" } }),
  });
  assert.equal(row.issues_credential, true);
  assert.deepEqual(row.issued, ["accessToken", "refreshToken"]);
});
