import assert from "node:assert/strict";
import test from "node:test";
import { citationErrors, citedPaths, keysFromPostData, requestIndexRow } from "../src/skillpack/request-keys.mjs";

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
  const ui = keysFromPostData(JSON.stringify({ items: [{ content: "1", _X_ROW_KEY: "r1", sort: 0 }] }));
  assert.equal(ui.includes("items[]._X_ROW_KEY"), false);
  assert.ok(ui.includes("items[].sort"));
});

test("a cited path must exist, and every key of that request must be written", () => {
  const requests = [{ id: "req_1", method: "POST", path: "/admin-api/oa/report/save", keys: keysFromPostData(body) }];
  assert.equal(citationErrors("#!/usr/bin/env python3", requests).some((item) => item.code === "path_not_in_evidence"), false);
  const invented = "POST /api/oa/workreport/daily/create";
  assert.ok(citationErrors(invented, requests).some((item) => item.code === "path_not_in_evidence"));
  const prefix = citationErrors("GET `/admin-api/oa/report`", requests);
  assert.equal(prefix.some((item) => item.code === "path_not_in_evidence"), false);
  const partial = "/admin-api/oa/report/save title items[].content";
  assert.ok(citationErrors(partial, requests).some((item) => item.code === "key_not_written" && item.key === "creator"));
  const full = ["/admin-api/oa/report/save", ...requests[0].keys].join("\n");
  assert.equal(citationErrors(full, requests).length, 0);
  const queried = requestIndexRow({ id: "req_2", method: "GET", path: "/admin-api/oa/report/statistics", query: "deptId=1&reportType=1", post_data: "" });
  assert.ok(queried.keys.includes("deptId"));
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

test("numeric type codes are not treated as API paths, and a path constant counts inside the function that uses it", () => {
  assert.equal(citedPaths("python scripts/client.py statistics --report-type <1|2|3>\nREPORT_TYPE = {\"1\": 1, \"2\": 2, \"3\": 3}\n1/2/3").some((path) => path === "/2/3"), false);
  assert.equal(citedPaths("roles = row.get(\"dept\")\n# leftover /dept/roles in a comment\n").some((path) => path === "/dept/roles"), false);
  const client = [
    "STATS = \"/api/stats\"",
    "def other():",
    "    return row.get(\"zone\")",
    "",
    "def run():",
    "    request(STATS)",
    "    params[\"deptId\"] = args.dept_id",
  ].join("\n");
  const cited = citationErrors(client, [{ id: "req_1", method: "GET", path: "/api/stats", keys: ["deptId"] }], "scripts/client.py");
  assert.equal(cited.some((item) => item.code === "key_not_written"), false);
  const preamble = [
    '"""python scripts/client.py departments',
    "python scripts/client.py create",
    '"""',
    'SAVE = "/api/save"',
    "def cmd_departments():",
    '    call("GET", "/api/list")',
    "def cmd_create():",
    "    request(SAVE)",
    '    body["title"] = args.title',
  ].join("\n");
  const fromPreamble = citationErrors(preamble, [{ id: "req_1", method: "POST", path: "/api/save", keys: ["title"] }], "scripts/client.py");
  assert.equal(fromPreamble.some((item) => item.code === "key_not_written"), false);
});

test("a helper called from the path function counts as writing its keys", () => {
  const requests = [{ id: "req_1", method: "POST", path: "/api/save", keys: ["attachments[].fileName", "title"] }];
  const client = [
    "def attach_of(name):",
    "    return {\"fileName\": name, \"filePath\": name}",
    "",
    "def save():",
    "    url = \"/api/save\"",
    "    body[\"title\"] = args.title",
    "    body[\"attachments\"] = [attach_of(item)]",
  ].join("\n");
  const cited = citationErrors(client, requests, "scripts/client.py");
  assert.equal(cited.some((item) => item.code === "key_not_written"), false);
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
  assert.ok(cited.some((item) => item.code === "key_not_written" && item.key === "state"));
  assert.equal(cited.some((item) => item.key === "owner"), false);
});
