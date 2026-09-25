import assert from "node:assert/strict";
import test from "node:test";
import { citationErrors, keysFromPostData } from "../src/skillpack/request-keys.mjs";

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
});

test("a cited path must exist, and every key of that request must be written", () => {
  const requests = [{ id: "req_1", method: "POST", path: "/admin-api/oa/report/save", keys: keysFromPostData(body) }];
  assert.equal(citationErrors("#!/usr/bin/env python3", requests).some((item) => item.code === "path_not_in_evidence"), false);
  const invented = "POST /api/oa/workreport/daily/create";
  assert.ok(citationErrors(invented, requests).some((item) => item.code === "path_not_in_evidence"));
  const partial = "/admin-api/oa/report/save title items[].content";
  assert.ok(citationErrors(partial, requests).some((item) => item.code === "key_not_written" && item.key === "creator"));
  const full = ["/admin-api/oa/report/save", ...requests[0].keys].join("\n");
  assert.equal(citationErrors(full, requests).length, 0);
});
