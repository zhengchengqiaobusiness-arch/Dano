import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { mkdtemp, mkdir, readFile, writeFile, stat } from "node:fs/promises";
import { seedPageProfile, syncLoginState, hasLoginState } from "../src/browser/login-profile.js";

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
