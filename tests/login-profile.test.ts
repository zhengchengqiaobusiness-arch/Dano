import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import { mkdtemp, mkdir, readFile, writeFile, stat, utimes } from "node:fs/promises";
import { seedPageProfile, syncLoginState, hasLoginState } from "../src/browser/login-profile.js";
import { WorkbenchPage } from "../src/web/workbench-page.js";

async function exists(target: string) {
  try {
    await stat(target);
    return true;
  } catch {
    return false;
  }
}

async function writeLogin(profileDir: string, mark: string) {
  const cookies = path.join(profileDir, "Default", "Cookies");
  const local = path.join(profileDir, "Default", "Local Storage", "000003.log");
  await mkdir(path.dirname(local), { recursive: true });
  await writeFile(cookies, `${mark}-cookie`);
  await writeFile(local, `${mark}-local`);
}

async function markLoginTime(profileDir: string, epochMs: number) {
  const at = new Date(epochMs);
  await utimes(path.join(profileDir, "Default", "Cookies"), at, at);
  await utimes(path.join(profileDir, "Default", "Local Storage"), at, at);
}

test("empty page profile inherits shared login cookies", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bss-login-"));
  const shared = path.join(root, "browser-profile");
  const page = path.join(shared, "page_newtab");
  await writeLogin(shared, "shared");

  await seedPageProfile(shared, page);

  assert.equal(await hasLoginState(page), true);
  assert.equal(await readFile(path.join(page, "Default", "Cookies"), "utf8"), "shared-cookie");
  assert.equal(await readFile(path.join(page, "Default", "Local Storage", "000003.log"), "utf8"), "shared-local");
});

test("page profile with its own login is not overwritten", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bss-login-"));
  const shared = path.join(root, "browser-profile");
  const page = path.join(shared, "page_keep");
  await writeLogin(shared, "shared");
  await writeLogin(page, "page");

  await seedPageProfile(shared, page);

  assert.equal(await readFile(path.join(page, "Default", "Cookies"), "utf8"), "page-cookie");
});

test("reopening a page refreshes stale login state from the newest shared profile", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bss-login-"));
  const shared = path.join(root, "browser-profile");
  const page = path.join(shared, "page_reopen");
  await writeLogin(page, "expired-page");
  await markLoginTime(page, 1_700_000_000_000);
  await writeLogin(shared, "fresh-shared");
  await markLoginTime(shared, 1_800_000_000_000);

  await seedPageProfile(shared, page);

  assert.equal(await readFile(path.join(page, "Default", "Cookies"), "utf8"), "fresh-shared-cookie");
  assert.equal(await readFile(path.join(page, "Default", "Local Storage", "000003.log"), "utf8"), "fresh-shared-local");
});

test("new page can inherit login from a previous page profile", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bss-login-"));
  const shared = path.join(root, "browser-profile");
  const previous = path.join(shared, "page_old");
  const next = path.join(shared, "page_next");
  await mkdir(shared, { recursive: true });
  await writeLogin(previous, "oldpage");

  await seedPageProfile(shared, next);

  assert.equal(await readFile(path.join(next, "Default", "Cookies"), "utf8"), "oldpage-cookie");
});

test("sync writes page login back to the shared profile", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bss-login-"));
  const shared = path.join(root, "browser-profile");
  const page = path.join(shared, "page_sync");
  await writeLogin(page, "fresh");

  await syncLoginState(page, shared);

  assert.equal(await hasLoginState(shared), true);
  assert.equal(await readFile(path.join(shared, "Default", "Cookies"), "utf8"), "fresh-cookie");
  assert.equal(await exists(path.join(shared, "Default", "Local Storage", "000003.log")), true);
});

test("a second workbench page reuses the login completed in the first recording", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bss-login-reuse-"));
  const server = createServer((request, response) => {
    if (request.url === "/login" && request.method === "POST") {
      response.setHeader("set-cookie", "session=logged-in; Path=/; HttpOnly");
      response.end("ok");
      return;
    }
    if (request.headers.cookie?.includes("session=logged-in")) {
      response.setHeader("content-type", "text/html; charset=utf-8");
      response.end("<title>印章申请</title><h1>印章申请</h1>");
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<title>登录</title><form action="/login"><input name="username"><input type="password"><button id="login" type="button" onclick="fetch('/login',{method:'POST'}).then(()=>location.reload())">登录</button></form>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("server did not bind");
  const config = {
    rootDir: root,
    dataDir: path.join(root, "data"),
    recordingsDir: path.join(root, "data", "recordings"),
    catalogDir: path.join(root, "data", "catalog"),
    profileDir: path.join(root, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  };
  const url = `http://127.0.0.1:${address.port}/oa/seal/sealApply?billType=seal_apply`;
  const first = new WorkbenchPage("page_loginfirst", config, "http://127.0.0.1:4310", value => value, () => {});
  const second = new WorkbenchPage("page_loginsecond", config, "http://127.0.0.1:4310", value => value, () => {});
  let secondStarting: Promise<unknown> | undefined;

  try {
    const firstStarting = first.startRecording(url, "first");
    for (let attempt = 0; attempt < 100 && !first.manualTakeoverState(); attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.ok(first.manualTakeoverState(), "first recording should pause for login");
    const firstBrowserPage = (first.recorder as any).currentPage();
    await firstBrowserPage.locator("#login").click();
    await firstBrowserPage.waitForFunction(() => document.title === "印章申请");
    assert.equal((await first.recorder.loginPageState()).detected, false);
    first.completeManualTakeover(first.manualTakeoverState()!.id);
    await firstStarting;
    await first.stopRecording();

    let secondStarted = false;
    secondStarting = second.startRecording(url, "second").then(session => {
      secondStarted = true;
      return session;
    });
    for (let attempt = 0; attempt < 100 && !secondStarted && !second.manualTakeoverState(); attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(second.manualTakeoverState(), undefined, second.manualTakeoverState()?.reason || "second recording unexpectedly paused for login");
    await secondStarting;
    assert.equal((await second.recorder.loginPageState()).detected, false);
    assert.equal(await (second.recorder as any).currentPage().title(), "印章申请");
  } finally {
    if (second.manualTakeoverState()) second.completeManualTakeover(second.manualTakeoverState()!.id);
    await secondStarting?.catch(() => {});
    if (first.recorder.isActive()) await first.stopRecording().catch(() => {});
    if (second.recorder.isActive()) await second.stopRecording().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
