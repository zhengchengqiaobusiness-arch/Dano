/**
 * 打开目标站并列出业务叶子页。只收集已登录页上的菜单事实，不推断能力。
 * 必须用页面自己发出的带登录请求；裸 fetch 经常 HTTP 200、业务 code 401。
 */

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { loadStorageState } from "../src/session-store.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const SITES = [
  {
    name: "ruoyioffice",
    url: "https://ruoyioffice.com/web/",
    clickModules: false,
  },
  {
    name: "dianshi-boot",
    url: "http://boot.dianshixinxi.com:90/index",
    clickModules: true,
  },
  {
    name: "dianshi-admin",
    url: "http://admin.dianshixinxi.com:90/index",
    clickModules: false,
  },
];

function walkMenus(nodes, trail = [], origin = "") {
  const pages = [];
  for (const node of Array.isArray(nodes) ? nodes : []) {
    const name = String(
      node.name || node.title || node.meta?.title || node.menuName || "",
    ).trim();
    const pathValue = String(
      node.path || node.component || node.url || node.menuUrl || "",
    ).trim();
    const nextTrail = name ? [...trail, name] : trail;
    const children = node.children || node.child || node.childrenList || [];
    if (Array.isArray(children) && children.length) {
      pages.push(...walkMenus(children, nextTrail, origin));
      continue;
    }
    if (!nextTrail.length) continue;
    pages.push({
      text: nextTrail.join(" / "),
      path: pathValue,
      origin,
    });
  }
  return pages;
}

function looksMenuTree(value) {
  if (!Array.isArray(value) || !value.length) return false;
  const first = value[0];
  if (!first || typeof first !== "object") return false;
  return Boolean(
    first.name || first.title || first.meta?.title || first.menuName || first.children,
  );
}

function extractMenuPayload(payload, origin = "") {
  if (!payload || typeof payload !== "object") return [];
  const code = payload.code ?? payload.status;
  if (code !== undefined && Number(code) !== 0 && Number(code) !== 200) return [];
  const data = payload.data ?? payload;
  const buckets = [];
  if (Array.isArray(data)) buckets.push(data);
  if (Array.isArray(data?.menus)) buckets.push(data.menus);
  if (Array.isArray(data?.routers)) buckets.push(data.routers);
  if (Array.isArray(payload.menus)) buckets.push(payload.menus);
  const pages = [];
  for (const bucket of buckets) {
    if (looksMenuTree(bucket)) pages.push(...walkMenus(bucket, [], origin));
  }
  return pages;
}

async function collectDomPages(page) {
  for (let round = 0; round < 4; round += 1) {
    const titles = page.locator(
      ".el-sub-menu__title, .el-submenu__title, .ant-menu-submenu-title, .n-submenu-indent",
    );
    const count = await titles.count().catch(() => 0);
    for (let index = 0; index < Math.min(count, 80); index += 1) {
      await titles.nth(index).click({ timeout: 400 }).catch(() => {});
    }
    await page.waitForTimeout(250);
  }
  return page.evaluate(() => {
    const nodes = [
      ...document.querySelectorAll(
        "a, .el-menu-item, .ant-menu-item, .n-menu-item, [role='menuitem']",
      ),
    ];
    return nodes
      .map((node) => ({
        text: String(node.textContent || "").replace(/\s+/g, " ").trim(),
        href: String(node.getAttribute("href") || node.getAttribute("to") || ""),
      }))
      .filter(
        (item) =>
          item.text &&
          item.text.length < 60 &&
          !/退出|布局|个人中心|修改密码/.test(item.text),
      );
  });
}

async function clickPortalModules(page) {
  const cards = page.locator(
    ".el-card, .module-item, .app-item, .menu-card, [class*='module'], [class*='app-box']",
  );
  const count = await cards.count().catch(() => 0);
  const texts = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll("a, button, .el-card, li, div")];
    const wanted = /工作空间|流程|办公|人事|客户|档案|仓储|资产|供应|生产|财务|商城|系统管理|MES|WMS|ERP|OA|HRM|CRM/;
    const seen = new Set();
    const hits = [];
    for (const node of nodes) {
      const text = String(node.textContent || "").replace(/\s+/g, " ").trim();
      if (!text || text.length > 24 || !wanted.test(text) || seen.has(text)) continue;
      if (node.children.length > 8) continue;
      seen.add(text);
      hits.push(text);
    }
    return hits.slice(0, 24);
  });
  const clicked = [];
  for (const text of texts) {
    const popupPromise = page.context().waitForEvent("page", { timeout: 2500 }).catch(() => null);
    await page.getByText(text, { exact: false }).first().click({ timeout: 1500 }).catch(() => {});
    const popup = await popupPromise;
    clicked.push(text);
    if (popup) {
      await popup.waitForLoadState("domcontentloaded").catch(() => {});
      await popup.waitForTimeout(1500);
    } else {
      await page.waitForTimeout(800);
    }
  }
  return { cardCount: count, clicked };
}

async function inventorySite(browser, site) {
  const storageState = await loadStorageState(site.url);
  const context = await browser.newContext({
    locale: "zh-CN",
    storageState: storageState || undefined,
    viewport: { width: 1440, height: 900 },
  });
  const apiPages = [];
  const probes = [];
  const onResponse = async (response) => {
    const url = response.url();
    if (!/menu|router|permission|getRouters|auth/i.test(url)) return;
    let json = null;
    try {
      json = await response.json();
    } catch {
      return;
    }
    const origin = (() => {
      try {
        return new URL(url).origin;
      } catch {
        return "";
      }
    })();
    const extracted = extractMenuPayload(json, origin);
    probes.push({
      url,
      status: response.status(),
      code: json?.code,
      extracted: extracted.length,
    });
    apiPages.push(...extracted);
  };
  const page = await context.newPage();
  page.on("response", onResponse);
  await page.goto(site.url, { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForTimeout(3500);
  const landedUrl = page.url();
  const title = await page.title();
  const loggedIn = !/login/i.test(landedUrl);
  let moduleClicks = null;
  if (site.clickModules && loggedIn) {
    moduleClicks = await clickPortalModules(page);
    await page.waitForTimeout(2000);
  }
  const pagesFromAllOpen = [];
  for (const openPage of context.pages()) {
    pagesFromAllOpen.push(...(await collectDomPages(openPage).catch(() => [])));
  }
  const openUrls = context.pages().map((item) => item.url());
  await context.close();
  const pages = [];
  const seen = new Set();
  for (const item of [...apiPages, ...pagesFromAllOpen]) {
    const key = `${item.text}|${item.path || item.href || ""}`;
    if (!item.text || seen.has(key)) continue;
    seen.add(key);
    pages.push(item);
  }
  return {
    name: site.name,
    startUrl: site.url,
    landedUrl,
    title,
    loggedIn,
    openUrls,
    probes,
    moduleClicks,
    pageCount: pages.length,
    pages,
  };
}

const browser = await chromium.launch({ headless: true });
const results = [];
for (const site of SITES) {
  try {
    results.push(await inventorySite(browser, site));
  } catch (error) {
    results.push({
      name: site.name,
      startUrl: site.url,
      error: String(error?.message || error),
      loggedIn: false,
      pageCount: 0,
      pages: [],
    });
  }
}
await browser.close();

const outFile = path.join(ROOT, "data", "page-inventory.json");
await mkdir(path.dirname(outFile), { recursive: true });
await writeFile(
  outFile,
  `${JSON.stringify({ capturedAt: new Date().toISOString(), sites: results }, null, 2)}\n`,
);
for (const site of results) {
  console.log(
    `${site.name} loggedIn=${site.loggedIn} pages=${site.pageCount} url=${site.landedUrl || site.startUrl}`,
  );
  console.log(`  probes=${JSON.stringify((site.probes || []).slice(0, 8))}`);
  if (site.moduleClicks) console.log(`  modules=${JSON.stringify(site.moduleClicks.clicked)}`);
  for (const page of (site.pages || []).slice(0, 120)) {
    console.log(`  - ${page.text}${page.path || page.href ? ` -> ${page.path || page.href}` : ""}`);
  }
}
console.log(`wrote ${outFile}`);
