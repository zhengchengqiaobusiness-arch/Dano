/**
 * 打开页面，列出本页实际发出的 xhr/fetch。只记事实。
 */

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { loadStorageState } from "../src/session-store.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const targetUrl = process.argv[2];
if (!targetUrl) {
  console.error("usage: node scripts/dump-page-xhr.mjs <url>");
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
const rows = [];
const menuBodies = [];
page.on("response", async (response) => {
  const request = response.request();
  const type = request.resourceType();
  if (type !== "xhr" && type !== "fetch") return;
  const url = request.url();
  rows.push({
    status: response.status(),
    method: request.method(),
    type,
    url,
  });
  if (!/menu|router|permission|getRouters|auth/i.test(url)) return;
  try {
    const json = await response.json();
    menuBodies.push({ url, status: response.status(), code: json?.code, json });
  } catch {
    /* 不是 JSON */
  }
});
await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
await page.waitForTimeout(5000);
const info = await page.evaluate(() => ({
  url: location.href,
  title: document.title,
  storageKeys: Object.keys(localStorage),
}));
const host = new URL(targetUrl).host.replace(/[^\w.-]+/g, "_");
const outDir = path.join(ROOT, "data", "xhr-dumps");
await mkdir(outDir, { recursive: true });
await writeFile(
  path.join(outDir, `${host}.json`),
  `${JSON.stringify({ info, xhrCount: rows.length, xhr: rows, menuBodies: menuBodies.map((item) => ({ url: item.url, status: item.status, code: item.code })) }, null, 2)}\n`,
);
for (const [index, item] of menuBodies.entries()) {
  const looksMenu = Boolean(
    item.json?.data?.menus ||
      (Array.isArray(item.json?.data) && item.json.data[0]?.path) ||
      Array.isArray(item.json?.data?.routers),
  );
  if (!looksMenu) continue;
  await writeFile(path.join(outDir, `${host}-menu-${index}.json`), `${JSON.stringify(item.json)}\n`);
}
console.log(JSON.stringify({ info, xhrCount: rows.length, xhr: rows.slice(0, 40), menuCount: menuBodies.length }, null, 2));
await browser.close();
