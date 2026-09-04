/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHarness, sampleResult } from "./helpers/harness.mjs";
import { createPlaywrightBrowser } from "../src/browser-capture.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

test("13. 完成一次真实浏览器录制", async (t) => {
  const html = await readFile(path.join(ROOT, "tests", "fixtures", "demo.html"));
  const fixture = createServer((req, res) => {
    if (req.url === "/api/leave") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const targetUrl = `http://127.0.0.1:${port}/`;
  let browserRef = null;
  const harness = await createHarness({
    emitOnStart: false,
    result: sampleResult({ e2e: "browser" }),
    createBrowser: async ({ recording, appendEvidence }) => {
      browserRef = await createPlaywrightBrowser({ recording, appendEvidence });
      return browserRef;
    },
  });
  t.after(async () => {
    try {
      await browserRef?.close();
    } catch {
      // ignore
    }
    await harness.cleanup();
    await new Promise((resolve) => fixture.close(resolve));
  });

  const started = await harness.controller.start({
    targetUrl,
    goal: "在演示页提交一次表单",
  });
  assert.ok(browserRef?.page);
  await browserRef.page.fill("#days", "2");
  await browserRef.page.click("#submit");
  await browserRef.page.waitForTimeout(300);
  const stopped = await harness.controller.stop(started.id);
  assert.equal(stopped.session.status, "succeeded");
  assert.ok(stopped.session.evidenceCount > 0);
  const events = await harness.files.readEvidence(started.id);
  assert.ok(events.some((event) => event.kind === "network_request"));
  assert.ok(events.some((event) => event.kind === "interaction" || event.kind === "screenshot"));
  assert.deepEqual(stopped.result.e2e, "browser");
});
