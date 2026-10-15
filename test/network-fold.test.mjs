import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { attachNetwork, foldNetwork, foldedSummary, getNetwork, listNetwork } from "../src/browser/network.mjs";
import { hostTools } from "../src/agent/tools.mjs";
import { withData } from "./helpers.mjs";

function fakeRequest({ method, url, headers = {}, postData = "", type = "xhr" }) {
  return {
    resourceType: () => type,
    method: () => method,
    url: () => url,
    headers: () => headers,
    postData: () => postData,
    frame: () => null,
  };
}

async function emitResponse(context, request, { status = 200, contentType = "application/json", body = "{}" } = {}) {
  context.emit("response", {
    request: () => request,
    status: () => status,
    headers: () => ({ "content-type": contentType }),
    text: async () => body,
  });
  await new Promise((resolve) => setTimeout(resolve, 30));
}

test("HEAD, static GET, and idle repeats fold; all=true keeps them", () => {
  const poll = (id) => ({ id, method: "GET", path: "/poll", query: "q=1", action_id: "" });
  const items = [
    { id: "h1", method: "HEAD", path: "/ping", query: "" },
    { id: "o1", method: "OPTIONS", path: "/ping", query: "" },
    { id: "s1", method: "GET", path: "/app/bundle.js", query: "", content_type: "application/javascript" },
    { id: "s2", method: "GET", path: "/img/a.png", query: "", content_type: "image/png" },
    { id: "j1", method: "GET", path: "/data/list.json", query: "", content_type: "application/json" },
    { id: "j2", method: "GET", path: "/x.json", query: "", content_type: "" },
    poll("p1"),
    poll("p2"),
    poll("p3"),
    { id: "w1", method: "POST", path: "/save", query: "", action_id: "act_1" },
  ];
  const { kept, folded } = foldNetwork(items);
  assert.equal(folded.head, 2);
  assert.equal(folded.static, 2);
  assert.equal(folded.repeated.length, 1);
  assert.equal(folded.repeated[0].count, 3);
  assert.equal(kept.some((row) => row.method === "HEAD" || row.method === "OPTIONS"), false);
  assert.equal(kept.some((row) => row.path === "/app/bundle.js" || row.path === "/img/a.png"), false);
  assert.ok(kept.some((row) => row.path === "/data/list.json"));
  assert.ok(kept.some((row) => row.path === "/x.json"));
  assert.equal(kept.filter((row) => row.path === "/poll").length, 1);
  assert.equal(kept.find((row) => row.path === "/poll")._repeat, 3);
  assert.ok(kept.some((row) => row.id === "w1"));
});

test("listNetwork folds by default and read_page_asset still sees javascript", async () => {
  await withData(async () => {
    const id = "rec_fold_list";
    const origin = "http://app.example.test";
    const context = new EventEmitter();
    attachNetwork(id, context);
    const head = fakeRequest({ method: "HEAD", url: `${origin}/ping` });
    const asset = fakeRequest({ method: "GET", url: `${origin}/app.js` });
    const write = fakeRequest({
      method: "POST",
      url: `${origin}/save`,
      headers: { "content-type": "application/json" },
      postData: "{}",
    });
    context.emit("request", head);
    context.emit("request", asset);
    context.emit("request", write);
    await emitResponse(context, asset, { contentType: "application/javascript", body: "window.x=1" });
    await emitResponse(context, write);
    const folded = listNetwork(id);
    const all = listNetwork(id, { all: true });
    const summary = foldedSummary(id);
    assert.equal(folded.some((row) => row.method === "HEAD"), false);
    assert.equal(folded.some((row) => row.path === "/app.js"), false);
    assert.ok(folded.some((row) => row.path === "/save"));
    assert.ok(all.some((row) => row.method === "HEAD"));
    assert.ok(all.some((row) => row.path === "/app.js"));
    assert.ok(summary.head >= 1);
    assert.ok(summary.static >= 1);
    const tools = hostTools({ id, startUrl: `${origin}/`, subsystem: "app", tenant: "" });
    const assetRead = await tools.read_page_asset({ url: `${origin}/app.js` });
    assert.equal(assetRead.ok, true);
    assert.equal(assetRead.body, "window.x=1");
    assert.equal(getNetwork(id, all.find((row) => row.path === "/app.js").id).request_header_names !== undefined, true);
  });
});
