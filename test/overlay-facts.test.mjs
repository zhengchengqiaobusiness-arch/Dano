import assert from "node:assert/strict";
import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { closeBrowser, getPage, openBrowser } from "../src/browser/session.mjs";
import { overlayControlFacts, overlayFactLists, takeSnapshot } from "../src/browser/snapshot.mjs";
import { withData } from "./helpers.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixture = path.join(here, "fixtures", "placeholder-form.html");
const pickerFixture = path.join(here, "fixtures", "readonly-picker.html");

async function serve(file = fixture) {
  const server = http.createServer(async (_req, res) => {
    const body = await readFile(file);
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, url: `http://127.0.0.1:${server.address().port}/` };
}

test("overlay facts treat attribute and class placeholders as empty and skip removed nodes", { timeout: 30000 }, async () => {
  await withData(async () => {
    const site = await serve();
    const id = "rec_placeholder_facts";
    try {
      await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 900, height: 700 } });
      await takeSnapshot(id, { budgetMs: 4000 });
      await getPage(id).evaluate(() => document.querySelector("#gone")?.remove());
      const started = Date.now();
      const facts = await overlayControlFacts(id);
      assert.ok(Date.now() - started < 3000);
      const choice = facts.find((row) => /choice/i.test(String(row.name || "")));
      const picker = facts.find((row) => /picker/i.test(String(row.name || "")));
      const title = facts.find((row) => /title/i.test(String(row.name || "")));
      assert.equal(choice?.empty, true);
      assert.equal(picker?.empty, true);
      assert.equal(title?.empty, false);
      assert.equal(facts.some((row) => /gone/i.test(String(row.name || ""))), false);
    } finally {
      await closeBrowser(id).catch(() => {});
      await new Promise((resolve) => site.server.close(resolve));
    }
  });
});

test("readonly combobox stays in overlay_editable; plain readonly does not", { timeout: 30000 }, async () => {
  await withData(async () => {
    const site = await serve(pickerFixture);
    const id = "rec_readonly_picker";
    try {
      await openBrowser({ recordingId: id, url: site.url, storageState: null, viewport: { width: 900, height: 700 } });
      await takeSnapshot(id, { budgetMs: 4000 });
      const lists = overlayFactLists(await overlayControlFacts(id));
      const picker = lists.overlay_editable.find((row) => row.role === "combobox");
      assert.ok(picker);
      assert.ok(lists.required_empty_overlay.some((row) => row.ref === picker.ref));
      assert.equal(lists.overlay_editable.some((row) => row.value === "固定" || /固定/.test(String(row.name || ""))), false);
    } finally {
      await closeBrowser(id).catch(() => {});
      await new Promise((resolve) => site.server.close(resolve));
    }
  });
});
