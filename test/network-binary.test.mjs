import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import http from "node:http";
import test from "node:test";
import { attachNetwork, compactNetworkIndex, getNetwork, listNetwork } from "../src/browser/network.mjs";
import { withData } from "./helpers.mjs";

function fakeRequest({ method, url, headers = {}, postData = "", type = "fetch" }) {
  return {
    resourceType: () => type,
    method: () => method,
    url: () => url,
    headers: () => headers,
    postData: () => postData,
    frame: () => null,
  };
}

test("binary responses store type size and filename, json stays text", { timeout: 15000 }, async () => {
  await withData(async () => {
    const payload = Buffer.alloc(2048, 1);
    const server = http.createServer((req, res) => {
      if (req.url === "/api/export") {
        res.writeHead(200, {
          "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "content-disposition": "attachment; filename*=UTF-8''%E6%8A%A5%E8%A1%A8.xlsx",
          "content-length": String(payload.length),
        });
        res.end(payload);
        return;
      }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const id = "rec_binary";
    try {
      const fileRes = await fetch(`${origin}/api/export`);
      const jsonRes = await fetch(`${origin}/api/items`);
      const fileHeaders = {
        "content-type": fileRes.headers.get("content-type") || "",
        "content-disposition": fileRes.headers.get("content-disposition") || "",
        "content-length": fileRes.headers.get("content-length") || "",
      };
      const fileBody = Buffer.from(await fileRes.arrayBuffer());
      const jsonText = await jsonRes.text();
      const jsonHeaders = { "content-type": jsonRes.headers.get("content-type") || "" };

      const context = new EventEmitter();
      attachNetwork(id, context);
      const fileReq = fakeRequest({ method: "GET", url: `${origin}/api/export` });
      const jsonReq = fakeRequest({ method: "GET", url: `${origin}/api/items` });
      context.emit("request", fileReq);
      context.emit("request", jsonReq);
      context.emit("response", {
        request: () => fileReq,
        status: () => 200,
        headers: () => fileHeaders,
        text: async () => { throw new Error("binary should not use text()"); },
        body: async () => fileBody,
      });
      context.emit("response", {
        request: () => jsonReq,
        status: () => 200,
        headers: () => jsonHeaders,
        text: async () => jsonText,
        body: async () => Buffer.from(jsonText),
      });
      await new Promise((resolve) => setTimeout(resolve, 50));

      const rows = listNetwork(id, { all: true });
      const file = rows.find((row) => row.path === "/api/export");
      const json = rows.find((row) => row.path === "/api/items");
      assert.ok(file);
      assert.equal(file.binary?.size, 2048);
      assert.equal(file.binary?.filename, "报表.xlsx");
      const stored = getNetwork(id, file.id);
      assert.equal(stored.response_body, null);
      assert.equal(stored.response_binary?.size, 2048);
      assert.equal(stored.response_binary?.filename, "报表.xlsx");
      assert.ok(json);
      assert.equal(json.binary, undefined);
      assert.match(String(getNetwork(id, json.id).response_body), /ok/);
      const compact = compactNetworkIndex(rows);
      assert.equal(compact.find((row) => row.path === "/api/export")?.binary?.filename, "报表.xlsx");
      assert.equal(compact.find((row) => row.path === "/api/items")?.binary, undefined);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});
