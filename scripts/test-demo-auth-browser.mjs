#!/usr/bin/env node
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { chromium } from "playwright-core";

const baseUrl = process.env.DANO_BROWSER_BASE_URL || "http://localhost:18082";
const screenshotPath =
  process.env.DANO_BROWSER_SCREENSHOT || join(tmpdir(), "dano-demo-auth-browser.png");
const chromeCandidates = [
  process.env.DANO_CHROME_EXECUTABLE,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].filter(Boolean);
const presetLabels = {
  default: "默认",
  blue: "蓝色",
  gray: "灰色",
  yellow: "黄色",
  pink: "粉色",
  purple: "紫色",
};

let browser;
let context;
let page;
let initialPreset;

async function waitForPreset(expected) {
  await page.waitForFunction(
    preset => document.querySelector(".app-shell")?.dataset.accentColorPreset === preset,
    expected,
  );
}

async function selectPreset(preset) {
  await page.getByRole("button", { name: "菜单", exact: true }).click();
  await page.getByRole("button", { name: "主题色", exact: true }).click();
  const responsePromise = page.waitForResponse(response =>
    response.url().endsWith("/preferences/theme") &&
    response.request().method() === "PUT"
  );
  await page.getByRole("button", { name: presetLabels[preset], exact: true }).click();
  assert.equal((await responsePromise).status(), 200);
  await page.keyboard.press("Escape");
  await waitForPreset(preset);
}

async function assertDemoUser() {
  await page.getByRole("button", { name: "菜单", exact: true }).click();
  await page.getByText("演示用户", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
}

async function run() {
  const executablePath = chromeCandidates.find(candidate => existsSync(candidate));
  assert.ok(executablePath, "No system Chrome/Chromium found");

  browser = await chromium.launch({ executablePath, headless: true });
  context = await browser.newContext({ viewport: { width: 879, height: 863 } });
  assert.equal((await context.cookies(baseUrl)).length, 0, "browser context was not new");

  page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "已连接", exact: true }).waitFor();
  await assertDemoUser();

  const cookie = (await context.cookies(baseUrl)).find(item => item.name === "dano_auth");
  assert.ok(cookie, "nginx did not set dano_auth on the first HTML response");
  assert.equal(cookie.httpOnly, true);
  assert.equal(cookie.sameSite, "Lax");
  assert.equal(cookie.secure, new URL(baseUrl).protocol === "https:");
  assert.ok(cookie.expires > Date.now() / 1000, "Demo cookie was not persistent");

  initialPreset = await page.locator(".app-shell").getAttribute("data-accent-color-preset");
  assert.ok(initialPreset && presetLabels[initialPreset], "current theme preset is unknown");
  const changedPreset = initialPreset === "purple" ? "blue" : "purple";
  await selectPreset(changedPreset);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "已连接", exact: true }).waitFor();
  await waitForPreset(changedPreset);
  await assertDemoUser();

  await page.screenshot({ path: screenshotPath, fullPage: true });
  await selectPreset(initialPreset);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "已连接", exact: true }).waitFor();
  await waitForPreset(initialPreset);
  console.log(`[demo-auth-browser] PASS; screenshot: ${screenshotPath}`);
}

try {
  await run();
} catch (error) {
  console.error(error?.stack ?? error);
  process.exitCode = 1;
} finally {
  if (page && initialPreset) {
    const currentPreset = await page
      .locator(".app-shell")
      .getAttribute("data-accent-color-preset")
      .catch(() => null);
    if (currentPreset && currentPreset !== initialPreset) {
      await selectPreset(initialPreset).catch(() => {});
    }
  }
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
}
