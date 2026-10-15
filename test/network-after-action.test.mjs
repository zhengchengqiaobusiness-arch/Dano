import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { attachNetwork, beginAction, compactNetworkIndex, endAction, listNetwork } from "../src/browser/network.mjs";
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

test("requests after the action window keep after_action time facts", async () => {
  await withData(async () => {
    const id = "rec_after_action";
    const origin = "http://app.example.test";
    const context = new EventEmitter();
    attachNetwork(id, context);
    const actId = beginAction(id, null, { anyFrame: true });
    const inside = fakeRequest({
      method: "POST",
      url: `${origin}/api/items`,
      headers: { "content-type": "application/json" },
      postData: "{}",
    });
    context.emit("request", inside);
    await emitResponse(context, inside);
    const during = listNetwork(id).find((row) => row.path === "/api/items");
    assert.equal(during.action_id, actId);
    assert.equal(during.after_action, undefined);
    endAction(id);
    await new Promise((resolve) => setTimeout(resolve, 800));
    const delayed = fakeRequest({ method: "GET", url: `${origin}/api/items?q=a` });
    context.emit("request", delayed);
    await emitResponse(context, delayed);
    const idle = listNetwork(id).find((row) => row.path === "/api/items" && !row.action_id);
    assert.ok(idle);
    assert.equal(idle.action_id, "");
    assert.equal(idle.after_action, actId);
    assert.ok(idle.after_ms >= 800);
    const compact = compactNetworkIndex(listNetwork(id));
    const compactIdle = compact.find((row) => row.id === idle.id);
    assert.equal(compactIdle.after_action, actId);
    assert.ok(compactIdle.after_ms >= 800);
    const compactDuring = compact.find((row) => row.id === during.id);
    assert.equal(compactDuring.after_action, undefined);
  });
});
