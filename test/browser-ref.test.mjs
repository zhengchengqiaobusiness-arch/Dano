import assert from "node:assert/strict";
import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { openBrowser, closeBrowser, getPage } from "../src/browser/session.mjs";
import { takeSnapshot } from "../src/browser/snapshot.mjs";
import { runAction } from "../src/browser/actions.mjs";
import { listNetwork } from "../src/browser/network.mjs";

const fixtureDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

async function serve() {
  let saves = 0;
  const server = http.createServer(async (req, res) => {
    if (req.method === "POST" && req.url === "/api/save") {
      saves += 1;
      res.writeHead(200, { "content-type": "application/json", "access-control-allow-origin": "*" });
      res.end("{}");
      return;
    }
    const file = req.url === "/inner.html" ? "inner.html" : "iframe-form.html";
    const body = await readFile(path.join(fixtureDir, file));
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, url: `http://127.0.0.1:${server.address().port}/`, saves: () => saves };
}

function publishedRef(line, frameId) {
  const match = String(line || "").match(new RegExp(`\\[ref=(${frameId}:[^\\]]+)\\]`))
    || String(line || "").match(new RegExp(`ref=(${frameId}:[^\\s\\]]+)`));
  return match ? match[1] : "";
}

function refsIn(text, frameId) {
  return text.split(/\n/).filter((line) => publishedRef(line, frameId) && /button/i.test(line));
}

test("iframe refs do not click the wrong submit", { timeout: 60000 }, async () => {
  const site = await serve();
  const id = "rec_browser";
  try {
    await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 1440, height: 900 } });
    const first = await takeSnapshot(id);
    const outerRef = publishedRef(refsIn(first.text, "f0")[0], "f0");
    const page = getPage(id);
    await page.reload({ waitUntil: "domcontentloaded" });
    const stale = await runAction(id, { action: "click", ref: outerRef });
    assert.equal(stale.error, "stale_ref");
    assert.equal(await page.evaluate(() => window.clicks.outer), 0);
    const fresh = await takeSnapshot(id);
    const outer = publishedRef(refsIn(fresh.text, "f0")[0], "f0");
    const outerClick = await runAction(id, { action: "click", ref: outer });
    await page.waitForTimeout(300);
    assert.equal(site.saves(), 0);
    const inner = refsIn(outerClick.snapshot.text, "f1");
    const second = publishedRef(inner[1], "f1");
    const innerClick = await runAction(id, { action: "click", ref: second });
    await page.waitForTimeout(500);
    const posts = listNetwork(id, {}).filter((row) => row.method === "POST" && row.path === "/api/save");
    assert.equal(posts.length, 1);
    const box = innerClick.snapshot.text.split(/\n/).find((line) => line.includes("textbox") && /\[ref=/.test(line));
    const inputRef = publishedRef(box, "f0");
    const filled = await runAction(id, { action: "fill", ref: inputRef, text: "changed" });
    assert.ok(filled.error === "value_not_applied" || filled.error === "needs_click");
    const nodeLine = filled.snapshot.text.split(/\n/).find((line) => line.includes("节点甲") && /\[ref=/.test(line));
    const nodeRef = publishedRef(nodeLine, "f0");
    const nodeClick = await runAction(id, { action: "click", ref: nodeRef });
    assert.equal(nodeClick.ok, true);
    assert.equal(await page.evaluate(() => window.clicks.node), 1);
    const radioLine = nodeClick.snapshot.text.split(/\n/).find((line) => line.includes("日报") && /\[ref=/.test(line));
    const radioRef = publishedRef(radioLine, "f0");
    const radioClick = await runAction(id, { action: "click", ref: radioRef });
    assert.equal(radioClick.ok, true);
    assert.equal(await page.locator("#daily input").isChecked(), true);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => site.server.close(resolve));
  }
});
