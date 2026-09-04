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
import { playwrightStateFromTokens, saveStorageState } from "../src/session-store.mjs";

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

test("同站点下次打开时恢复登录态，预览页能读到已存 token", async (t) => {
  const previousAutoLogin = process.env.PI_CHECK_AUTO_LOGIN;
  process.env.PI_CHECK_AUTO_LOGIN = "0";
  t.after(() => {
    if (previousAutoLogin == null) delete process.env.PI_CHECK_AUTO_LOGIN;
    else process.env.PI_CHECK_AUTO_LOGIN = previousAutoLogin;
  });
  const html = `<!doctype html><html><body>
    <div id="out"></div>
    <script>
      document.getElementById("out").textContent = localStorage.getItem("ACCESS_TOKEN") ? "authed" : "anon";
    </script>
  </body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html);
  });
  const port = await listen(fixture);
  const origin = `http://127.0.0.1:${port}`;
  await saveStorageState(`${origin}/`, playwrightStateFromTokens(origin, {
    accessToken: "restored-token",
    refreshToken: "restored-refresh",
    tenantId: "1",
  }));
  const events = [];
  const appendEvidence = async (kind, payload) => {
    events.push({ kind, payload });
    return { seq: events.length };
  };
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_test", byteLength: bytes.byteLength });
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_session_restore", targetUrl: `${origin}/` },
    appendEvidence,
  });
  t.after(async () => {
    try {
      await handle.close();
    } catch {
      // ignore
    }
    await new Promise((resolve) => fixture.close(resolve));
  });
  const text = await handle.page.locator("#out").innerText();
  assert.equal(text, "authed");
  assert.ok(events.some((event) => event.kind === "network_request"));
});

test("同页跳转和新窗口都会成为当前录制页", async (t) => {
  process.env.PI_CHECK_AUTO_LOGIN = "0";
  const home = `<!doctype html><html><body>
    <a id="same" href="/other">same tab</a>
    <button id="popup">popup</button>
    <script>
      document.getElementById("popup").onclick = () => window.open("/other", "_blank");
    </script>
  </body></html>`;
  const other = `<!doctype html><html><body><h1 id="dest">other page</h1></body></html>`;
  const fixture = createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(req.url?.startsWith("/other") ? other : home);
  });
  const port = await listen(fixture);
  const origin = `http://127.0.0.1:${port}`;
  const events = [];
  const appendEvidence = async (kind, payload) => {
    events.push({ kind, payload });
    return { seq: events.length };
  };
  appendEvidence.saveBlob = async (bytes) => ({ blobId: "blob_nav", byteLength: bytes.byteLength });
  const handle = await createPlaywrightBrowser({
    recording: { id: "rec_nav", targetUrl: `${origin}/` },
    appendEvidence,
  });
  t.after(async () => {
    try {
      await handle.close();
    } catch {
      // ignore
    }
    await new Promise((resolve) => fixture.close(resolve));
  });

  await handle.page.click("#same");
  await handle.page.waitForURL(/\/other/);
  assert.match(handle.page.url(), /\/other/);
  assert.equal(await handle.page.locator("#dest").innerText(), "other page");

  await handle.applyInput({ kind: "goto", url: `${origin}/` });
  await handle.page.waitForURL((url) => url.pathname === "/");
  const first = handle.page;
  const popupWait = first.waitForEvent("popup");
  await first.click("#popup");
  const popup = await popupWait;
  await popup.waitForURL(/\/other/);
  assert.match(handle.page.url(), /\/other/);
  assert.equal(handle.page, popup);
  assert.ok(events.filter((event) => event.kind === "page_created").length >= 2);
  await popup.close();
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(handle.livePage(), first);
});
