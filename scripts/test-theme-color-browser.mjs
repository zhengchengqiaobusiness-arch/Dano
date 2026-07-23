import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { mkdtempSync, rmSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { chromium } from "playwright-core";
import {
  availablePort,
  findChromeExecutable,
  startService,
  stopService,
  waitForHttp,
} from "./browser-test-harness.mjs";

const repoRoot = resolve(import.meta.dirname, "..");
const authSecret = "dano-theme-color-browser-test-secret";
const userId = "theme-color-browser-user";
const runtimeRoot = mkdtempSync(join(tmpdir(), "dano-theme-color-browser-"));
const preferenceDirectory = join(runtimeRoot, "users", userId, "preferences");
let backend;
let web;
let browser;
const serviceOutput = [];

function signTestUserToken() {
  const encode = value => Buffer.from(JSON.stringify(value)).toString("base64url");
  const unsigned = `${encode({ alg: "HS256", typ: "JWT" })}.${encode({
    sub: userId,
    name: "主题色浏览器验收用户",
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}`;
  const signature = createHmac("sha256", authSecret)
    .update(unsigned)
    .digest("base64url");
  return `${unsigned}.${signature}`;
}

async function waitForConnected(page) {
  await page.getByRole("button", { name: "已连接", exact: true }).waitFor();
}

async function waitForPreset(page, preset) {
  await page.waitForFunction(
    value => document.querySelector(".app-shell")?.dataset.accentColorPreset === value,
    preset,
  );
}

async function openDialog(page) {
  await page.getByRole("button", { name: "菜单", exact: true }).click();
  await page.getByRole("button", { name: "主题色", exact: true }).click();
  await page.getByRole("dialog").waitFor();
}

async function selectPreset(page, label, expectedStatus = 200) {
  const responsePromise = page.waitForResponse(response =>
    response.url().endsWith("/preferences/theme") &&
    response.request().method() === "PUT"
  );
  await page.getByRole("button", { name: label, exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), expectedStatus);
  assert.equal(await page.getByRole("dialog").isVisible(), true);
}

async function dialogMetrics(page) {
  return page.evaluate(() => {
    const query = selector => document.querySelector(selector);
    const rect = selector => {
      const value = query(selector)?.getBoundingClientRect();
      return value
        ? { x: value.x, y: value.y, width: value.width, height: value.height }
        : null;
    };
    const panel = query(".theme-dialog");
    const panelStyle = getComputedStyle(panel);
    const overlayStyle = getComputedStyle(query(".theme-dialog-overlay"));
    const rowStyle = getComputedStyle(query(".theme-color-row"));
    const selected = query(".theme-color-row[aria-pressed=true]");
    return {
      panel: rect(".theme-dialog"),
      close: rect(".theme-dialog-close"),
      titleIcon: rect(".theme-dialog-title svg"),
      rows: Array.from(document.querySelectorAll(".theme-color-row"), row => ({
        key: row.getAttribute("data-theme-color-preset"),
        height: row.getBoundingClientRect().height,
      })),
      panelPadding: [panelStyle.paddingTop, panelStyle.paddingRight, panelStyle.paddingBottom, panelStyle.paddingLeft],
      panelRadius: panelStyle.borderRadius,
      panelBackground: panelStyle.backgroundColor,
      panelBackdropFilter: panelStyle.backdropFilter,
      panelShadow: panelStyle.boxShadow,
      overlayBackground: overlayStyle.backgroundColor,
      rowGap: rowStyle.gap,
      rowPadding: [rowStyle.paddingLeft, rowStyle.paddingRight],
      selected: selected?.getAttribute("data-theme-color-preset"),
      selectedBackground: selected ? getComputedStyle(selected).backgroundColor : null,
      title: query("#theme-color-dialog-title")?.textContent?.trim(),
      bodyText: panel?.textContent ?? "",
    };
  });
}

function assertDialogContract(metrics, options) {
  assert.deepEqual([metrics.panel.x, metrics.panel.width], options.panel);
  assert.deepEqual(metrics.panelPadding, options.padding);
  assert.equal(metrics.panelRadius, options.radius);
  assert.equal(metrics.panelBackdropFilter, "none");
  assert.match(metrics.panelBackground, /^rgba?\(/);
  assert.doesNotMatch(metrics.panelBackground, /0\)$/);
  assert.notEqual(metrics.panelShadow, "none");
  assert.notEqual(metrics.overlayBackground, "rgba(0, 0, 0, 0)");
  assert.deepEqual([metrics.close.width, metrics.close.height], [40, 40]);
  assert.deepEqual([metrics.titleIcon.width, metrics.titleIcon.height], [18, 18]);
  assert.equal(metrics.title, "主题色");
  assert.doesNotMatch(metrics.bodyText, /明暗模式|Dark theme|Light theme/i);
  assert.deepEqual(metrics.rows.map(row => row.key), ["default", "blue", "gray", "yellow", "pink", "purple"]);
  assert.ok(metrics.rows.every(row => row.height === 48));
  assert.equal(metrics.rowGap, "12px");
  assert.deepEqual(metrics.rowPadding, ["14px", "14px"]);
  assert.notEqual(metrics.selectedBackground, "rgba(0, 0, 0, 0)");
}

async function run() {
  const executablePath = findChromeExecutable();
  assert.ok(executablePath, "No system Chrome/Chromium found");
  const backendPort = await availablePort();
  const webPort = await availablePort();
  const serverOrigin = `http://localhost:${backendPort}`;
  const origin = `http://localhost:${webPort}`;

  backend = startService(
    "pnpm",
    ["run", "dev:server"],
    {
      cwd: repoRoot,
      output: serviceOutput,
      env: {
        ...process.env,
        DANO_PORT: String(backendPort),
        DANO_RUNTIME_DIR: runtimeRoot,
        DANO_AUTH_JWT_SECRET: authSecret,
      },
    },
  );
  web = startService("pnpm", [
    "-C",
    "apps/dano",
    "exec",
    "vite",
    "--port",
    String(webPort),
    "--strictPort",
  ], {
    cwd: repoRoot,
    output: serviceOutput,
    env: {
      ...process.env,
      DANO_DEV_BACKEND_ORIGIN: serverOrigin,
    },
  });
  await Promise.all([
    waitForHttp(serverOrigin, {
      services: [backend],
      isReady: response => response.status < 500,
    }),
    waitForHttp(origin, { services: [web] }),
  ]);

  browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({
    colorScheme: "light",
    viewport: { width: 879, height: 863 },
  });
  await context.addCookies([{
    name: "dano_auth",
    value: signTestUserToken(),
    url: origin,
    httpOnly: true,
    sameSite: "Lax",
  }]);
  await context.addInitScript(() => {
    localStorage.setItem("pi-web-theme", "dark");
  });

  const page = await context.newPage();
  await page.goto(origin, { waitUntil: "domcontentloaded" });
  await waitForConnected(page);
  await waitForPreset(page, "default");
  assert.equal(await page.locator(".app-shell").getAttribute("data-theme-mode"), "light");

  await openDialog(page);
  const initial = await dialogMetrics(page);
  assertDialogContract(initial, {
    panel: [249.5, 380],
    padding: ["14px", "14px", "14px", "14px"],
    radius: "22px",
  });
  assert.equal(initial.selected, "default");
  await page.screenshot({ path: join(tmpdir(), "dano-theme-color-browser.png") });

  for (const [label, preset] of [
    ["蓝色", "blue"],
    ["灰色", "gray"],
    ["黄色", "yellow"],
    ["粉色", "pink"],
    ["紫色", "purple"],
    ["默认", "default"],
    ["紫色", "purple"],
  ]) {
    await selectPreset(page, label);
    await waitForPreset(page, preset);
  }

  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForConnected(page);
  await waitForPreset(page, "purple");

  const secondPage = await context.newPage();
  await secondPage.goto(origin, { waitUntil: "domcontentloaded" });
  await waitForConnected(secondPage);
  await waitForPreset(secondPage, "purple");
  await secondPage.close();

  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(() =>
    document.querySelector(".app-shell")?.dataset.themeMode === "dark"
  );
  await openDialog(page);
  assert.equal((await dialogMetrics(page)).selected, "purple");
  await page.keyboard.press("Escape");

  chmodSync(preferenceDirectory, 0o500);
  try {
    await openDialog(page);
    await selectPreset(page, "蓝色", 500);
    await waitForPreset(page, "blue");
    await page.getByText("主题色保存失败，当前页面将保留本次选择。", { exact: true }).waitFor();
    assert.equal(await page.getByRole("dialog").isVisible(), true);
  } finally {
    chmodSync(preferenceDirectory, 0o700);
  }

  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForConnected(page);
  await waitForPreset(page, "purple");

  await page.setViewportSize({ width: 640, height: 760 });
  await openDialog(page);
  assertDialogContract(await dialogMetrics(page), {
    panel: [130, 380],
    padding: ["16px", "16px", "16px", "16px"],
    radius: "20px",
  });
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 360, height: 760 });
  await openDialog(page);
  assertDialogContract(await dialogMetrics(page), {
    panel: [20, 320],
    padding: ["16px", "16px", "16px", "16px"],
    radius: "20px",
  });

  await selectPreset(page, "默认");
  await waitForPreset(page, "default");
  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForConnected(page);
  await waitForPreset(page, "default");
  await context.close();
}

try {
  await run();
  console.log("[theme-color-browser] PASS");
} catch (error) {
  console.error(error?.stack ?? error);
  if (serviceOutput.length > 0) console.error(serviceOutput.join(""));
  process.exitCode = 1;
} finally {
  await browser?.close().catch(() => {});
  await Promise.all([
    stopService(web).catch(() => {}),
    stopService(backend).catch(() => {}),
  ]);
  rmSync(runtimeRoot, { recursive: true, force: true });
}
