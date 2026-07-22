import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer as createNetServer } from "node:net";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright-core";

const repoRoot = resolve(import.meta.dirname, "..");
const chromeCandidates = [
  process.env.DANO_CHROME_EXECUTABLE,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

let vite;
let browser;
const serviceOutput = [];

async function availablePort() {
  const server = createNetServer();
  await new Promise((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListen);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  await new Promise((resolveClose, reject) =>
    server.close(error => error ? reject(error) : resolveClose()),
  );
  return address.port;
}

async function waitForHttp(url) {
  const deadline = Date.now() + 30_000;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    if (vite?.exitCode !== null) {
      throw new Error(`Vite exited with ${vite.exitCode}`);
    }
    await delay(100);
  }
  throw new Error(`Vite did not become ready: ${lastError?.message ?? "timeout"}`);
}

async function stopVite() {
  if (!vite || vite.exitCode !== null || vite.signalCode !== null) return;
  const exited = new Promise(resolveExit => vite.once("exit", resolveExit));
  try {
    process.kill(-vite.pid, "SIGTERM");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  if (await Promise.race([exited.then(() => true), delay(3_000, false)])) return;
  try {
    process.kill(-vite.pid, "SIGKILL");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  await exited;
}

function actionMetrics(page) {
  return page.evaluate(() => {
    const sections = [...document.querySelectorAll("main > section")];
    const submitted = document.querySelector(".submitted-fields");
    return {
      viewportWidth: innerWidth,
      cards: sections.map(section => {
        const actions = section.querySelector(".question-actions");
        const buttons = [...section.querySelectorAll(
          ".question-actions .question-button",
        )];
        if (!actions || buttons.length === 0) {
          throw new Error("question action row is missing");
        }
        const actionHeight = actions.getBoundingClientRect().height;
        const buttonHeights = buttons.map(button =>
          button.getBoundingClientRect().height
        );
        const style = getComputedStyle(buttons[0]);
        return {
          actionHeight,
          buttonHeights,
          bottomBlank: actionHeight - Math.max(...buttonHeights),
          paddingBlock: [style.paddingBlockStart, style.paddingBlockEnd],
          paddingInline: [style.paddingInlineStart, style.paddingInlineEnd],
          display: style.display,
          alignItems: style.alignItems,
          justifyContent: style.justifyContent,
        };
      }),
      inputHeight: document.querySelector(".question-input:not(textarea)")
        ?.getBoundingClientRect().height,
      submittedGridColumns: submitted
        ? getComputedStyle(submitted).gridTemplateColumns
        : null,
    };
  });
}

function assertActionMetrics(metrics, height) {
  assert.equal(metrics.inputHeight, height);
  for (const card of metrics.cards) {
    assert.deepEqual(card.buttonHeights, card.buttonHeights.map(() => height));
    assert.equal(card.actionHeight, height);
    assert.equal(card.bottomBlank, 0);
    assert.deepEqual(card.paddingBlock, ["0px", "0px"]);
    assert.deepEqual(card.paddingInline, ["14px", "14px"]);
    assert.equal(card.display, "flex");
    assert.equal(card.alignItems, "center");
    assert.equal(card.justifyContent, "center");
  }
}

async function visibleForegrounds(page) {
  return page.evaluate(() => ({
    token: getComputedStyle(document.documentElement)
      .getPropertyValue("--on-accent").trim(),
    primaryButtons: [...document.querySelectorAll(
      ".question-actions .question-button:not(.secondary)",
    )].map(button => getComputedStyle(button).color),
    submittedIcon: getComputedStyle(
      document.querySelector(".submitted-status-icon"),
    ).color,
  }));
}

async function accentSurfaceForegrounds(page) {
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "日期", exact: true }).click();
  const enabledDays = page.locator(
    ".question-calendar-day:not([data-disabled]):not([data-unavailable])",
  );
  assert.ok(await enabledDays.count() > 0);
  await enabledDays.first().click();
  await page.getByRole("button", { name: "日期", exact: true }).click();
  return page.evaluate(() => {
    const checkbox = document.querySelector(
      '.question-option input[type="checkbox"]:checked',
    );
    const day = document.querySelector(".question-calendar-day[data-selected]");
    if (!checkbox || !day) throw new Error("accent surface is not visible");
    return {
      checkbox: getComputedStyle(checkbox, "::before").borderBottomColor,
      selectedDate: getComputedStyle(day).color,
    };
  });
}

async function run() {
  const executablePath = chromeCandidates.find(candidate => existsSync(candidate));
  assert.ok(executablePath, "No system Chrome/Chromium found");
  const port = await availablePort();
  const origin = `http://localhost:${port}`;
  vite = spawn(
    "pnpm",
    ["-C", "apps/dano", "exec", "vite", "--port", String(port), "--strictPort"],
    { cwd: repoRoot, detached: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  for (const stream of [vite.stdout, vite.stderr]) {
    stream.setEncoding("utf8");
    stream.on("data", chunk => serviceOutput.push(chunk));
  }
  await waitForHttp(`${origin}/question-tool-card-test.html`);

  browser = await chromium.launch({ executablePath, headless: true });
  const page = await browser.newPage({ viewport: { width: 641, height: 900 } });
  await page.goto(`${origin}/question-tool-card-test.html?accent=gray`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByRole("button", { name: "提交", exact: true }).waitFor();

  const desktop = await actionMetrics(page);
  assert.equal(desktop.viewportWidth, 641);
  assertActionMetrics(desktop, 36);
  assert.equal(desktop.submittedGridColumns.split(" ").length, 2);

  const gray = await visibleForegrounds(page);
  assert.equal(gray.token, "#0d1117");
  assert.deepEqual(gray.primaryButtons, ["rgb(13, 17, 23)", "rgb(13, 17, 23)"]);
  assert.equal(gray.submittedIcon, "rgb(13, 17, 23)");

  await page.setViewportSize({ width: 640, height: 900 });
  const narrow = await actionMetrics(page);
  assert.equal(narrow.viewportWidth, 640);
  assertActionMetrics(narrow, 44);

  await page.setViewportSize({ width: 641, height: 900 });
  for (const [preset, expectedToken, expectedRgb] of [
    ["default", "#ffffff", "rgb(255, 255, 255)"],
    ["blue", "#ffffff", "rgb(255, 255, 255)"],
    ["gray", "#0d1117", "rgb(13, 17, 23)"],
    ["yellow", "#ffffff", "rgb(255, 255, 255)"],
    ["pink", "#ffffff", "rgb(255, 255, 255)"],
    ["purple", "#ffffff", "rgb(255, 255, 255)"],
  ]) {
    await page.goto(`${origin}/question-tool-card-test.html?accent=${preset}`, {
      waitUntil: "domcontentloaded",
    });
    await page.getByRole("button", { name: "提交", exact: true }).waitFor();
    const foregrounds = await visibleForegrounds(page);
    assert.equal(foregrounds.token, expectedToken, preset);
    assert.deepEqual(foregrounds.primaryButtons, [expectedRgb, expectedRgb], preset);
    assert.equal(foregrounds.submittedIcon, expectedRgb, preset);
    assert.deepEqual(
      await accentSurfaceForegrounds(page),
      { checkbox: expectedRgb, selectedDate: expectedRgb },
      preset,
    );
  }
}

try {
  await run();
  console.log("[question-tool-card-browser] PASS");
} catch (error) {
  console.error(error?.stack ?? error);
  if (serviceOutput.length > 0) console.error(serviceOutput.join(""));
  process.exitCode = 1;
} finally {
  await browser?.close().catch(() => {});
  await stopVite().catch(() => {});
}
