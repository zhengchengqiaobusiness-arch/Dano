/**
 * 打开一个已登录业务页，只采集可见控件与按钮事实，不推断能力。
 */

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { loadStorageState } from "../src/session-store.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const targetUrl = process.argv[2];
if (!targetUrl) {
  console.error("usage: node scripts/probe-page-surface.mjs <url>");
  process.exit(1);
}

const storageState = await loadStorageState(targetUrl);
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  locale: "zh-CN",
  storageState: storageState || undefined,
  viewport: { width: 1440, height: 900 },
});
const page = await context.newPage();
const xhr = [];
page.on("request", (request) => {
  const type = request.resourceType();
  if (type !== "xhr" && type !== "fetch") return;
  xhr.push({
    method: request.method(),
    url: request.url().replace(/[?#].*$/, ""),
  });
});
await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
await page.waitForTimeout(3500);
const surface = await page.evaluate(() => {
  const textOf = (node) => String(node.textContent || "").replace(/\s+/g, " ").trim();
  const buttons = [...document.querySelectorAll("button, .el-button, [role='button']")]
    .map((node) => textOf(node))
    .filter((text) => text && text.length < 20);
  const labels = [...document.querySelectorAll(".el-form-item__label, label")]
    .map((node) => textOf(node).replace(/[:：]$/, ""))
    .filter((text) => text && text.length < 20);
  const placeholders = [...document.querySelectorAll("input, textarea")]
    .map((node) => String(node.getAttribute("placeholder") || "").trim())
    .filter(Boolean);
  return {
    title: document.title,
    url: location.href,
    buttons: [...new Set(buttons)],
    labels: [...new Set(labels)],
    placeholders: [...new Set(placeholders)],
  };
});
await browser.close();
const seenXhr = [];
const seen = new Set();
for (const item of xhr) {
  const key = `${item.method} ${item.url}`;
  if (seen.has(key)) continue;
  seen.add(key);
  seenXhr.push(item);
}
const result = {
  ...surface,
  loggedIn: !/login/i.test(surface.url),
  xhr: seenXhr.slice(0, 80),
};
const safeName = targetUrl.replace(/[^\w.-]+/g, "_").slice(0, 80);
const outFile = path.join(ROOT, "data", `surface-${safeName}.json`);
await mkdir(path.dirname(outFile), { recursive: true });
await writeFile(outFile, `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify(result, null, 2));
console.log(`wrote ${outFile}`);
