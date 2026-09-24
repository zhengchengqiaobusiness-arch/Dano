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

function refsIn(text, frameId) {
  return text.split(/\n/).filter((line) => line.includes(`ref=${frameId}:`) && line.includes("button"));
}

test("iframe refs do not click the wrong submit", async () => {
  const site = await serve();
  const id = "rec_browser";
  try {
    await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 1440, height: 900 } });
    const first = await takeSnapshot(id);
    const outerRef = refsIn(first.text, "f0")[0].match(/ref=(f0:\S+)/)[1];
    const page = getPage(id);
    await page.reload({ waitUntil: "domcontentloaded" });
    const stale = await runAction(id, { action: "click", ref: outerRef });
    assert.equal(stale.error, "stale_ref");
    assert.equal(await page.evaluate(() => window.clicks.outer), 0);
    const fresh = await takeSnapshot(id);
    const outer = refsIn(fresh.text, "f0")[0].match(/ref=(f0:\S+)/)[1];
    await runAction(id, { action: "click", ref: outer });
    await page.waitForTimeout(300);
    assert.equal(site.saves(), 0);
    const inner = refsIn(fresh.text, "f1");
    const second = inner[1].match(/ref=(f1:\S+)/)[1];
    await runAction(id, { action: "click", ref: second });
    await page.waitForTimeout(500);
    const posts = listNetwork(id, {}).filter((row) => row.method === "POST" && row.path === "/api/save");
    assert.equal(posts.length, 1);
    const box = fresh.text.split(/\n/).find((line) => line.includes("textbox"));
    const inputRef = box.match(/ref=(f0:\S+)/)[1];
    const filled = await runAction(id, { action: "fill", ref: inputRef, text: "changed" });
    assert.equal(filled.error, "value_not_applied");
    const nodeLine = filled.snapshot.text.split(/\n/).find((line) => line.includes("节点甲"));
    const nodeRef = nodeLine.match(/ref=(f0:\S+)/)[1];
    const nodeClick = await runAction(id, { action: "click", ref: nodeRef });
    assert.equal(nodeClick.ok, true);
    assert.equal(await page.evaluate(() => window.clicks.node), 1);
    const radioLine = nodeClick.snapshot.text.split(/\n/).find((line) => line.includes("日报"));
    const radioRef = radioLine.match(/ref=(f0:\S+)/)[1];
    const radioClick = await runAction(id, { action: "click", ref: radioRef });
    assert.equal(radioClick.ok, true);
    assert.equal(await page.locator("#daily input").isChecked(), true);
  } finally {
    await closeBrowser(id);
    await new Promise((resolve) => site.server.close(resolve));
  }
});
