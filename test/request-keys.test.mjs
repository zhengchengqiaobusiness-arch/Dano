import assert from "node:assert/strict";
import test from "node:test";
import { adoptedEvidencePaths, changedRequestKeys, citationErrors, collapseRequestIndex, keysAddedSince, keysFromPostData, previousMatchingRequest, previousPathRequest, readsIssuedCredential, requestIndexRow, sameAsUnlinked, writerRequestRows } from "../src/skillpack/request-keys.mjs";

const body = {
  creator: "1",
  title: "1",
  items: [{ content: "1", itemType: 1, progress: 30 }],
};

test("request keys include system fields and nested item keys", () => {
  const keys = keysFromPostData(JSON.stringify(body));
  assert.ok(keys.includes("creator"));
  assert.ok(keys.includes("items[].itemType"));
  assert.ok(keys.includes("items[].content"));
  assert.equal(keys.includes("1"), false);
  const ui = keysFromPostData(JSON.stringify({ items: [{ content: "1", _X_ROW_KEY: "r1", sort: 0, _id: "row-1" }] }));
  assert.equal(ui.includes("items[]._X_ROW_KEY"), false);
  assert.ok(ui.includes("items[].sort"));
  assert.ok(ui.includes("items[]._id"));
});

test("a short key is not written just because a longer word contains it", () => {
  const requests = [{ id: "req_1", method: "POST", path: "/api/save", keys: ["id", "name", "user.name"] }];
  const missed = citationErrors("def save():\n    path = \"/api/save\"\n    item_id = 1\n    username = \"甲\"\n    return valid\n", requests, "scripts/client.py");
  assert.ok(missed.some((item) => item.key === "id"));
  assert.ok(missed.some((item) => item.key === "name"));
  assert.ok(missed.some((item) => item.key === "user.name"));
  const written = citationErrors("def save():\n    path = \"/api/save\"\n    body[\"id\"] = item_id\n    body[\"name\"] = user[\"name\"]\n", requests, "scripts/client.py");
  assert.equal(written.some((item) => item.code === "key_not_written"), false);
});

test("a cited path must exist, and every key of that request must be written", () => {
  const requests = [{ id: "req_1", method: "POST", path: "/admin-api/oa/report/save", keys: keysFromPostData(body) }];
  assert.equal(citationErrors("#!/usr/bin/env python3", requests).some((item) => item.code === "path_not_in_evidence"), false);
  const invented = "POST /api/oa/workreport/daily/create";
  assert.ok(citationErrors(invented, requests).some((item) => item.code === "path_not_in_evidence"));
  const words = "help = \"(one/two/three)\"";
  assert.equal(citationErrors(words, requests).some((item) => item.code === "path_not_in_evidence"), false);
  const commented = "# POST /api/only/in/comment\n";
  assert.equal(citationErrors(commented, requests).some((item) => item.code === "path_not_in_evidence"), false);
  const partial = "/admin-api/oa/report/save title items[].content";
  assert.ok(citationErrors(partial, requests).some((item) => item.code === "key_not_written" && item.key === "creator"));
  const full = ["/admin-api/oa/report/save", ...requests[0].keys].join("\n");
  assert.equal(citationErrors(full, requests).length, 0);
  const queried = requestIndexRow({ id: "req_2", method: "GET", path: "/admin-api/oa/report/statistics", query: "deptId=1&reportType=1", post_data: "" });
  assert.ok(queried.keys.includes("deptId"));
  assert.deepEqual(queried.query, ["deptId", "reportType"]);
  assert.equal(queried.keys.includes("1"), false);
  assert.ok(citationErrors("/admin-api/oa/report/statistics", [queried]).some((item) => item.key === "deptId"));
  const client = "/admin-api/oa/report/save\nitems.append({'content': content, 'itemType': 1, '_X_ROW_KEY': key, 'sort': 0, 'progress': 0})";
  assert.equal(citationErrors(client, requests).some((item) => item.key === "items[].content"), false);
  assert.ok(citationErrors(client, requests).some((item) => item.key === "creator"));
});

test("empty values and a credential-issuing response stay facts on the request", () => {
  const row = requestIndexRow({
    id: "req_9",
    method: "POST",
    path: "/session/refresh",
    post_data: JSON.stringify({ companyId: null, title: "kept", items: [] }),
    query: "deptId=&reportType=1",
    response_body: JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref12345", expiresTime: 1 } }),
  });
  assert.ok(row.empty.includes("companyId"));
  assert.ok(row.empty.includes("items[]"));
  assert.ok(row.empty.includes("deptId"));
  assert.equal(row.empty.includes("title"), false);
  assert.equal(row.issues_credential, true);
  assert.equal(row.keys.includes("abc12345"), false);
  const plain = requestIndexRow({ id: "req_1", method: "GET", path: "/list", response_body: JSON.stringify({ data: { id: 1 } }) });
  assert.equal(plain.issues_credential, undefined);
  assert.equal(plain.empty, undefined);
});

test("only keys whose values differ are reported", () => {
  const before = { post_data: JSON.stringify({ title: "旧", reportType: 1, items: [{ content: "a" }] }), query: "deptId=1" };
  const after = { post_data: JSON.stringify({ title: "新", reportType: 1, items: [{ content: "a" }, { content: "b" }] }), query: "deptId=2" };
  const changed = changedRequestKeys(before, after);
  assert.ok(changed.includes("title"));
  assert.ok(changed.includes("deptId"));
  assert.ok(changed.includes("items[].content"));
  assert.equal(changed.includes("reportType"), false);
  assert.equal(changed.includes("items[0].content"), false);
  assert.equal(changed.includes("items[1].content"), false);
});

test("a different key set is not the baseline for what changed", () => {
  const items = [
    { id: "req_1", method: "POST", path: "/save", post_data: JSON.stringify({ name: "a" }) },
    { id: "req_2", method: "POST", path: "/save", post_data: JSON.stringify({ id: "1", name: "a" }) },
    { id: "req_3", method: "POST", path: "/save", post_data: JSON.stringify({ id: "2", name: "b" }) },
  ];
  assert.equal(previousMatchingRequest(items, 1), null);
  assert.equal(previousMatchingRequest(items, 2).id, "req_2");
  assert.deepEqual(changedRequestKeys(items[2], items[2]).length, 0);
  assert.ok(changedRequestKeys(previousMatchingRequest(items, 2), items[2]).includes("name"));
});

test("a changed key keeps the action that was open when it changed", () => {
  const rows = collapseRequestIndex([
    { method: "POST", path: "/save", keys: ["name", "qty"], action: "fill:textbox \"名称\"", changed_keys: ["name"] },
    { method: "POST", path: "/save", keys: ["name", "qty"], action: "fill:spinbutton \"数量\"", changed_keys: ["qty"] },
  ]);
  assert.deepEqual(rows[0].changed_keys, ["name", "qty"]);
  assert.deepEqual(rows[0].changed_by, [
    { key: "name", actions: ["fill:textbox \"名称\""] },
    { key: "qty", actions: ["fill:spinbutton \"数量\""] },
  ]);
});

test("the same path keeps every action name that was open when it fired", () => {
  const rows = collapseRequestIndex([
    { method: "GET", path: "/items", keys: ["id"], action: "click:button \"甲\"" },
    { method: "GET", path: "/items", keys: ["id"], action: "click:button \"乙\"" },
    { method: "GET", path: "/items", keys: ["id"], action: "click:button \"甲\"" },
  ]);
  assert.deepEqual(rows[0].actions, ["click:button \"甲\"", "click:button \"乙\""]);
  assert.equal(rows[0].action, undefined);
});

test("the same path with different keys or different empty keys stays separate", () => {
  const rows = collapseRequestIndex([
    { id: "req_1", method: "GET", path: "/poll", keys: ["a"], empty: ["a"], changed_keys: ["z"] },
    { id: "req_2", method: "GET", path: "/poll", keys: ["b"], issues_credential: true, changed_keys: ["a"] },
    { id: "req_3", method: "POST", path: "/poll", keys: ["c"] },
    { id: "req_4", method: "POST", path: "/save", keys: ["id", "name"], empty: ["id"] },
    { id: "req_5", method: "POST", path: "/save", keys: ["id", "name"] },
    { id: "req_6", method: "POST", path: "/save", keys: ["id", "name"], empty: ["id"] },
  ]);
  assert.equal(rows.length, 5);
  const named = rows.find((row) => row.method === "GET" && row.keys.includes("a"));
  assert.deepEqual(named.keys, ["a"]);
  assert.deepEqual(named.empty, ["a"]);
  assert.deepEqual(named.changed_keys, ["z"]);
  assert.equal(named.issues_credential, undefined);
  const issued = rows.find((row) => row.keys.includes("b"));
  assert.equal(issued.issues_credential, true);
  const saves = rows.filter((row) => row.path === "/save");
  assert.equal(saves.length, 2);
  assert.equal(saves.filter((row) => row.empty?.includes("id")).length, 1);
});

test("a credential replay must read method and url outside comments", () => {
  assert.equal(readsIssuedCredential("\"\"\"auth['credential']\"\"\"\n# auth['credential']['url']"), false);
  assert.equal(readsIssuedCredential("credential = auth['credential']\nRequest(credential['url'], method=credential['method'])\n"), true);
});

test("a static file is not a business request, and a short path adopts the evidence path", () => {
  const rows = writerRequestRows([
    { id: "req_1", method: "GET", path: "/ep.json", keys: ["icons"], action_id: "act_1" },
    { id: "req_2", method: "GET", path: "/admin-api/oa/supply/get", keys: ["id"], action_id: "act_2" },
  ]);
  assert.equal(rows.some((row) => row.path === "/ep.json"), false);
  assert.ok(rows.some((row) => row.path === "/admin-api/oa/supply/get"));
  const adopted = adoptedEvidencePaths(["/oa/supply/get", "/admin-api/oa/supply/get"], rows);
  assert.deepEqual(adopted, ["/admin-api/oa/supply/get"]);
});

test("a multipart body keeps the field names and does not mark them empty", () => {
  const post = [
    "------bound",
    "Content-Disposition: form-data; name=\"file\"; filename=\"a.png\"",
    "",
    "xx",
    "------bound",
    "Content-Disposition: form-data; name=\"title\"",
    "",
    "hi",
    "------bound--",
    "",
  ].join("\r\n");
  const keys = keysFromPostData(post);
  assert.ok(keys.includes("file"));
  assert.ok(keys.includes("title"));
  const row = requestIndexRow({ id: "req_1", method: "POST", path: "/upload", post_data: post });
  assert.equal(row.empty, undefined);
});

test("a form body contributes its keys, empty fields, and changed keys", () => {
  const keys = keysFromPostData("name=ab&qty=");
  assert.ok(keys.includes("name"));
  assert.ok(keys.includes("qty"));
  const row = requestIndexRow({ id: "req_1", method: "POST", path: "/save", post_data: "name=ab&qty=" });
  assert.ok(row.empty.includes("qty"));
  assert.deepEqual(changedRequestKeys(
    { post_data: "name=ab&qty=1" },
    { post_data: "name=cd&qty=1" },
  ), ["name"]);
});

test("a request that matches the background copies is marked, and a different key is not", () => {
  const background = [1, 2, 3].map((id) => ({ id: `req_${id}`, method: "GET", path: "/items", keys: ["page"], query: ["page"] }));
  const same = { id: "req_9", method: "GET", path: "/items", action_id: "act_1", keys: ["page"], query: ["page"] };
  const named = { id: "req_8", method: "GET", path: "/items", action_id: "act_1", keys: ["page", "name"], query: ["page", "name"] };
  const emptied = { id: "req_7", method: "GET", path: "/items", action_id: "act_1", keys: ["page"], query: ["page"], empty: ["page"] };
  assert.equal(sameAsUnlinked(same, background), true);
  assert.equal(sameAsUnlinked(named, background), false);
  assert.equal(sameAsUnlinked(emptied, background), false);
  const prior = { id: "req_1", method: "GET", path: "/notes", query: "n=1" };
  assert.equal(sameAsUnlinked({ id: "req_2", method: "GET", path: "/notes", query: "n=1", action_id: "act_2" }, [prior]), true);
  assert.equal(sameAsUnlinked({ id: "req_3", method: "GET", path: "/notes", query: "n=2", action_id: "act_3" }, [prior]), false);
  const polls = [1, 2, 3].map((id) => ({ id: `req_${id}`, method: "HEAD", path: "/app/", action_id: `act_${id}` }));
  assert.equal(sameAsUnlinked(polls[0], polls), false);
  assert.equal(sameAsUnlinked(polls[1], polls), true);
  assert.equal(sameAsUnlinked(polls[2], polls), true);
});

test("keys that the previous request on this path did not have are listed once", () => {
  const items = [
    { id: "req_1", method: "GET", path: "/items", query: "page=1" },
    { id: "req_2", method: "GET", path: "/items", query: "page=1&name=a" },
    { id: "req_3", method: "GET", path: "/items", query: "page=2&name=b" },
  ];
  assert.equal(previousPathRequest(items, 1).id, "req_1");
  assert.deepEqual(keysAddedSince(items[0], items[1]), ["name"]);
  assert.deepEqual(keysAddedSince(items[1], items[2]), []);
  assert.equal(previousMatchingRequest(items, 1), null);
  const rows = collapseRequestIndex([
    { id: "req_2", method: "GET", path: "/items", keys: ["page", "name"], added_keys: ["name"] },
    { id: "req_4", method: "GET", path: "/items", keys: ["page", "name"], added_keys: ["name"] },
  ]);
  assert.deepEqual(rows[0].added_keys, ["name"]);
});

test("a click that only repeats an earlier unmatched request is not a new row", () => {
  const rows = writerRequestRows([
    { id: "req_1", method: "GET", path: "/notes", keys: ["n"] },
    { id: "req_2", method: "GET", path: "/notes", keys: ["n"], action_id: "act_1", same_as_unlinked: true },
    { id: "req_3", method: "POST", path: "/save", keys: ["title"], action_id: "act_1", changed_keys: ["title"] },
  ]);
  assert.equal(rows.some((row) => row.action_id === "act_1" && row.path === "/notes"), false);
  assert.ok(rows.some((row) => row.path === "/notes" && !row.action_id));
  assert.ok(rows.some((row) => row.path === "/save" && row.changed_keys.includes("title")));
});

test("the same keys as a poll stay when the empty set differs", () => {
  const polls = [1, 2, 3].map((id) => ({ id: `req_${id}`, method: "GET", path: "/items", keys: ["page", "name"], query: ["page", "name"] }));
  const clicked = { id: "req_9", method: "GET", path: "/items", action_id: "act_1", keys: ["page", "name"], query: ["page", "name"], empty: ["name"] };
  const rows = writerRequestRows([...polls, clicked]);
  assert.ok(rows.some((row) => row.path === "/items" && row.empty?.includes("name")));
  assert.equal(rows.some((row) => row.path === "/items" && !row.empty), false);
});

test("a click that changes a polled path stays, and the same poll shape does not", () => {
  const rows = writerRequestRows([
    { id: "req_1", method: "GET", path: "/items", action_id: "act_1", keys: ["page", "name"], query: ["page", "name"] },
    { id: "req_2", method: "GET", path: "/items", keys: ["page"], query: ["page"] },
    { id: "req_3", method: "GET", path: "/items", keys: ["page"], query: ["page"] },
    { id: "req_4", method: "GET", path: "/items", keys: ["page"], query: ["page"] },
  ]);
  assert.deepEqual(rows.map((row) => row.path), ["/items"]);
  assert.ok(rows[0].keys.includes("name"));
  const same = writerRequestRows([
    { id: "req_1", method: "GET", path: "/items", action_id: "act_1", keys: ["page"], query: ["page"], changed_keys: ["page"] },
    { id: "req_2", method: "GET", path: "/items", keys: ["page"], query: ["page"] },
    { id: "req_3", method: "GET", path: "/items", keys: ["page"], query: ["page"] },
    { id: "req_4", method: "GET", path: "/items", keys: ["page"], query: ["page"] },
  ]);
  assert.ok(same.some((row) => row.path === "/items" && row.changed_keys.includes("page")));
});

test("a one-off read stays in the writer index, and a repeated unlinked poll does not", () => {
  const rows = writerRequestRows([
    { id: "req_1", method: "GET", path: "/session/profile", keys: ["id"] },
    { id: "req_2", method: "GET", path: "/poll", keys: [] },
    { id: "req_3", method: "GET", path: "/poll", keys: [] },
    { id: "req_4", method: "GET", path: "/poll", keys: [] },
    { id: "req_5", method: "POST", path: "/save", keys: ["title"], action_id: "act_1" },
  ]);
  assert.ok(rows.some((row) => row.path === "/session/profile"));
  assert.equal(rows.some((row) => row.path === "/poll"), false);
  assert.ok(rows.some((row) => row.path === "/save"));
  const overlapped = writerRequestRows([
    { id: "req_a", method: "GET", path: "/unread", action_id: "act_1" },
    { id: "req_b", method: "GET", path: "/unread" },
    { id: "req_c", method: "GET", path: "/unread" },
    { id: "req_d", method: "GET", path: "/unread" },
    { id: "req_e", method: "POST", path: "/save", keys: ["title"], action_id: "act_1" },
  ]);
  assert.equal(overlapped.some((row) => row.path === "/unread"), false);
  assert.ok(overlapped.some((row) => row.path === "/save"));
});

test("a key in another function does not satisfy the function that sends the path", () => {
  const requests = [{ id: "req_1", method: "POST", path: "/api/save", keys: ["owner", "state"] }];
  const client = [
    "def save():",
    "    url = \"/api/save\"",
    "    if args.owner:",
    "        data[\"owner\"] = args.owner",
    "",
    "def show():",
    "    return row.get(\"state\")",
  ].join("\n");
  const cited = citationErrors(client, requests, "scripts/client.py");
  const missing = cited.find((item) => item.code === "key_not_written" && item.key === "state");
  assert.ok(missing);
  assert.deepEqual(missing.keys, ["state"]);
  assert.match(missing.hint, /data=data/);
  assert.equal(cited.some((item) => item.key === "owner"), false);
  const replay = citationErrors(
    "def refresh():\n    url = \"/session/refresh\"\n    credential = auth[\"credential\"]\n    Request(credential[\"url\"], method=credential[\"method\"])\n",
    [{ id: "req_9", method: "POST", path: "/session/refresh", keys: ["refreshToken"], query: ["refreshToken"], issues_credential: true }],
    "scripts/client.py",
  );
  assert.equal(replay.some((item) => item.code === "key_not_written"), false);
});
