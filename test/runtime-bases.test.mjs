import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { attachNetwork, beginAction, endAction } from "../src/browser/network.mjs";
import { skillDir } from "../src/paths.mjs";
import { writeRuntimeConfig } from "../src/skillpack/files.mjs";
import { withData } from "./helpers.mjs";

function fakeRequest({ method, url, headers = {}, postData = "" }) {
  return {
    resourceType: () => "xhr",
    method: () => method,
    url: () => url,
    headers: () => headers,
    postData: () => postData,
    frame: () => null,
  };
}

test("runtime.json bases lists non-entry hosts and omits static origins", async () => {
  await withData(async () => {
    const entryId = "rec_bases_entry";
    const splitId = "rec_bases_split";
    const entry = "https://app.example.test";
    const api = "https://api.example.test";
    const cdn = "https://cdn.example.test";
    const files = "https://files.example.test";
    const entryContext = new EventEmitter();
    attachNetwork(entryId, entryContext);
    entryContext.emit("request", fakeRequest({
      method: "POST",
      url: `${entry}/save`,
      headers: { "content-type": "application/json" },
      postData: "{}",
    }));
    const onlyEntry = await writeRuntimeConfig({
      id: entryId,
      tenant: "",
      subsystem: "app",
      startUrl: `${entry}/`,
    }, "app.rec_bases_entry");
    assert.equal(onlyEntry.runtime.base_url, entry);
    assert.equal(onlyEntry.runtime.bases, undefined);

    const splitContext = new EventEmitter();
    attachNetwork(splitId, splitContext);
    splitContext.emit("request", fakeRequest({
      method: "POST",
      url: `${api}/save`,
      headers: { "content-type": "application/json" },
      postData: "{}",
    }));
    const cdnAsset = fakeRequest({ method: "GET", url: `${cdn}/app.js` });
    splitContext.emit("request", cdnAsset);
    splitContext.emit("response", {
      request: () => cdnAsset,
      status: () => 200,
      headers: () => ({ "content-type": "application/javascript" }),
      text: async () => "window.x=1",
    });
    await new Promise((resolve) => setTimeout(resolve, 30));
    beginAction(splitId, null, { anyFrame: true });
    splitContext.emit("request", fakeRequest({
      method: "GET",
      url: `${files}/data.json`,
      headers: { "content-type": "application/json" },
    }));
    endAction(splitId);
    splitContext.emit("request", fakeRequest({
      method: "GET",
      url: `${entry}/me`,
      headers: { "content-type": "application/json" },
    }));
    const split = await writeRuntimeConfig({
      id: splitId,
      tenant: "",
      subsystem: "app",
      startUrl: `${entry}/`,
    }, "app.rec_bases_split");
    assert.equal(split.runtime.bases["api.example.test"], api);
    assert.equal(split.runtime.bases["files.example.test"], files);
    assert.equal(split.runtime.bases["cdn.example.test"], undefined);
    assert.equal(split.runtime.bases["app.example.test"], undefined);
    const written = JSON.parse(await readFile(path.join(skillDir("app.rec_bases_split"), "config", "runtime.json"), "utf8"));
    assert.equal(written.bases["api.example.test"], api);
    assert.equal(written.bases?.["cdn.example.test"], undefined);
  });
});
