import assert from "node:assert/strict";
import { resolve } from "node:path";
import { chromium } from "playwright-core";
import {
  availablePort,
  findChromeExecutable,
  startService,
  stopService,
  waitForHttp,
} from "./browser-test-harness.mjs";

const repoRoot = resolve(import.meta.dirname, "..");
let vite;
let browser;
const serviceOutput = [];

function headerMetrics(page) {
  return page.evaluate(() => {
    const element = selector => document.querySelector(selector);
    const rect = selector => {
      const value = element(selector)?.getBoundingClientRect();
      return value
        ? {
            x: value.x,
            y: value.y,
            width: value.width,
            height: value.height,
            right: value.right,
            bottom: value.bottom,
          }
        : null;
    };
    const style = selector => {
      const node = element(selector);
      if (!node) return null;
      const value = getComputedStyle(node);
      return {
        position: value.position,
        display: value.display,
        alignItems: value.alignItems,
        justifyContent: value.justifyContent,
        gap: value.gap,
        paddingInline: [value.paddingInlineStart, value.paddingInlineEnd],
        borderRadius: value.borderRadius,
        background: value.backgroundColor,
        backdropFilter: value.backdropFilter,
        boxShadow: value.boxShadow,
        fontSize: value.fontSize,
        fontWeight: value.fontWeight,
        outline: value.outline,
        outlineOffset: value.outlineOffset,
        transform: value.transform,
      };
    };
    const newSession = rect(".new-session-button");
    const trailing = rect(".header-trailing");
    return {
      viewportWidth: innerWidth,
      header: rect(".app-header"),
      headerStyle: style(".app-header"),
      newSession,
      newSessionStyle: style(".new-session-button"),
      newSessionLabelDisplay: element(".new-session-button span")
        ? getComputedStyle(element(".new-session-button span")).display
        : null,
      newSessionIcon: rect(".new-session-button svg"),
      newSessionStroke: element(".new-session-button svg")?.getAttribute("stroke-width"),
      trailing,
      trailingStyle: style(".header-trailing"),
      connection: rect(".connection-status"),
      connectionStyle: style(".connection-status"),
      menuButton: rect(".menu-button"),
      menuButtonStyle: style(".menu-button"),
      overlap: Boolean(newSession && trailing && newSession.right > trailing.x),
    };
  });
}

function menuMetrics(page) {
  return page.evaluate(() => {
    const element = selector => document.querySelector(selector);
    const rect = selector => {
      const value = element(selector)?.getBoundingClientRect();
      return value
        ? { x: value.x, y: value.y, width: value.width, height: value.height, right: value.right }
        : null;
    };
    const menu = element(".header-menu");
    const style = menu && getComputedStyle(menu);
    const placeholder = element(".header-user-placeholder");
    return {
      menu: rect(".header-menu"),
      menuButton: rect(".menu-button"),
      themeRow: rect(".theme-menu-item"),
      separator: rect(".header-menu-separator"),
      userSummary: rect(".header-user-summary"),
      text: menu?.innerText ?? "",
      padding: style
        ? [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft]
        : null,
      borderRadius: style?.borderRadius,
      background: style?.backgroundColor,
      backdropFilter: style?.backdropFilter,
      boxShadow: style?.boxShadow,
      placeholderBackground: placeholder
        ? getComputedStyle(placeholder).backgroundColor
        : null,
    };
  });
}

function assertUtilityGeometry(metrics, viewportWidth) {
  assert.equal(metrics.viewportWidth, viewportWidth);
  assert.equal(metrics.header.x, 10);
  assert.equal(metrics.header.y, 10);
  assert.equal(metrics.header.right, viewportWidth - 10);
  assert.equal(metrics.headerStyle.position, "fixed");
  assert.equal(metrics.headerStyle.gap, "12px");
  assert.equal(metrics.trailingStyle.gap, "8px");
  assert.equal(metrics.connection.height, 26);
  assert.deepEqual(
    [metrics.menuButton.width, metrics.menuButton.height],
    [26, 26],
  );
  assert.equal(metrics.connection.y + metrics.connection.height / 2, metrics.menuButton.y + 13);
  assert.equal(metrics.connectionStyle.backdropFilter, "blur(2px)");
  assert.equal(metrics.menuButtonStyle.backdropFilter, "blur(2px)");
  assert.equal(metrics.overlap, false);
}

async function run() {
  const executablePath = findChromeExecutable();
  assert.ok(executablePath, "No system Chrome/Chromium found");
  const port = await availablePort();
  const origin = `http://localhost:${port}`;
  vite = startService(
    "pnpm",
    ["-C", "apps/dano", "exec", "vite", "--port", String(port), "--strictPort"],
    { cwd: repoRoot, output: serviceOutput },
  );
  await waitForHttp(`${origin}/app-header-test.html`, { services: [vite] });

  browser = await chromium.launch({ executablePath, headless: true });
  const page = await browser.newPage({ viewport: { width: 879, height: 863 } });
  await page.goto(`${origin}/app-header-test.html?conversation=empty`, {
    waitUntil: "domcontentloaded",
  });
  const menuButton = page.getByRole("button", { name: "菜单", exact: true });
  await menuButton.waitFor();

  const empty = await headerMetrics(page);
  assertUtilityGeometry(empty, 879);
  assert.equal(empty.newSession, null);
  assert.equal(empty.connectionStyle.background, empty.menuButtonStyle.background);
  assert.equal(empty.connectionStyle.boxShadow, empty.menuButtonStyle.boxShadow);

  await menuButton.click();
  const menu = page.locator(".header-menu");
  await menu.waitFor();
  const openMenu = await menuMetrics(page);
  assert.equal(openMenu.menu.width, 248);
  assert.equal(openMenu.menu.right, 869);
  assert.equal(openMenu.menu.y - openMenu.menuButton.y - openMenu.menuButton.height, 8);
  assert.deepEqual(openMenu.padding, ["6px", "6px", "6px", "6px"]);
  assert.equal(openMenu.borderRadius, "16px");
  assert.equal(openMenu.backdropFilter, "blur(14px)");
  assert.match(openMenu.background, /0\.65|65%/);
  assert.match(openMenu.boxShadow, /12px 32px/);
  assert.equal(openMenu.themeRow.height, 40);
  assert.equal(openMenu.separator.height, 1);
  assert.equal(openMenu.userSummary.height, 40);
  assert.match(openMenu.text, /^主题色\n浏览器验收用户$/);
  assert.doesNotMatch(openMenu.text, /Keyboard shortcuts|Dano preview/i);
  assert.equal(openMenu.placeholderBackground, "rgba(0, 0, 0, 0)");

  await page.evaluate(() => new Promise(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(resolve)),
  ));
  await page.mouse.click(400, 400);
  await menu.waitFor({ state: "hidden" });

  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
  for (let index = 0; index < 4; index++) {
    await page.keyboard.press("Tab");
    if (await menuButton.evaluate(node => node === document.activeElement)) break;
  }
  assert.equal(await menuButton.evaluate(node => node === document.activeElement), true);
  assert.equal(await menuButton.evaluate(node => node.matches(":focus-visible")), true);
  const focus = await menuButton.evaluate(node => {
    const style = getComputedStyle(node);
    return { outline: style.outline, offset: style.outlineOffset };
  });
  assert.match(focus.outline, /2px/);
  assert.equal(focus.offset, "2px");
  await page.keyboard.press("Enter");
  await menu.waitFor();
  await page.keyboard.press("Escape");
  await menu.waitFor({ state: "hidden" });

  await menuButton.hover();
  const restingBackground = empty.menuButtonStyle.background;
  await page.waitForFunction(
    restingShadow => getComputedStyle(document.querySelector(".menu-button")).boxShadow !== restingShadow,
    empty.menuButtonStyle.boxShadow,
  );
  const hoverStyle = await menuButton.evaluate(node => {
    const style = getComputedStyle(node);
    return { background: style.backgroundColor, boxShadow: style.boxShadow };
  });
  assert.notEqual(hoverStyle.background, restingBackground);
  assert.notEqual(hoverStyle.boxShadow, empty.menuButtonStyle.boxShadow);
  await page.mouse.down();
  await page.waitForFunction(() =>
    getComputedStyle(document.querySelector(".menu-button")).transform !== "none",
  );
  const activeTransform = await menuButton.evaluate(node => getComputedStyle(node).transform);
  await page.mouse.up();
  assert.notEqual(activeTransform, "none");

  await page.goto(`${origin}/app-header-test.html?conversation=chat`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator(".new-session-button").waitFor();
  const desktop = await headerMetrics(page);
  assertUtilityGeometry(desktop, 879);
  assert.deepEqual([desktop.newSession.height, desktop.newSessionStyle.paddingInline], [40, ["14px", "14px"]]);
  assert.equal(desktop.newSessionStyle.gap, "8px");
  assert.equal(desktop.newSessionStyle.fontSize, "13.12px");
  assert.equal(desktop.newSessionStyle.fontWeight, "700");
  assert.deepEqual([desktop.newSessionIcon.width, desktop.newSessionIcon.height], [18, 18]);
  assert.equal(desktop.newSessionStroke, "2.5");

  await page.setViewportSize({ width: 641, height: 800 });
  const aboveBreakpoint = await headerMetrics(page);
  assertUtilityGeometry(aboveBreakpoint, 641);
  assert.equal(aboveBreakpoint.newSession.height, 40);
  assert.ok(aboveBreakpoint.newSession.width > 40);
  assert.notEqual(aboveBreakpoint.newSessionLabelDisplay, "none");

  await page.setViewportSize({ width: 640, height: 800 });
  const narrow = await headerMetrics(page);
  assertUtilityGeometry(narrow, 640);
  assert.deepEqual([narrow.newSession.width, narrow.newSession.height], [40, 40]);
  assert.equal(narrow.newSessionLabelDisplay, "none");
}

try {
  await run();
  console.log("[app-header-browser] PASS");
} catch (error) {
  console.error(error?.stack ?? error);
  if (serviceOutput.length > 0) console.error(serviceOutput.join(""));
  process.exitCode = 1;
} finally {
  await browser?.close().catch(() => {});
  await stopService(vite).catch(() => {});
}
