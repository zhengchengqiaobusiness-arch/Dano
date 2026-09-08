/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 浏览器层只启动/关闭页面，并按发生顺序原样采集事实。
 * xhr/fetch/document 响应正文仍原样保存；静态资源与调试日志不落盘正文，避免拖死页面。
 * 登录态按站点保存，并在第一次打开页面前恢复。
 * 新打开的页面成为当前预览和操作目标；关掉后回到上一页。
 */

import { randomUUID } from "node:crypto";
import { logPiOnly } from "./policy.mjs";
import { collectPageFacts } from "./visible-controls.mjs";
import { assignSnapshotRefs } from "./browser-snapshot.mjs";
import { parseLocator, resolveInteractionActor } from "./browser-actions.mjs";
import {
  isLoginUrl,
  looksLoggedIn,
  resolveStorageState,
  saveStorageState,
  silentLoginStorage,
} from "./session-store.mjs";

const BLOB_THRESHOLD = 4096;
const POINTER_MOVE_MIN_INTERVAL_MS = 50;
export const FRAME_JPEG_QUALITY = 90;
export const DEFAULT_VIEWPORT = { width: 1440, height: 900 };

export function normalizeViewport(raw) {
  const width = Math.round(Number(raw?.width || raw?.w || 0));
  const height = Math.round(Number(raw?.height || raw?.h || 0));
  if (!Number.isFinite(width) || !Number.isFinite(height) || width < 640 || height < 400) {
    return null;
  }
  return {
    width: Math.min(2560, width),
    height: Math.min(1600, height),
  };
}

export function normalizeDeviceScale(raw) {
  const value = Number(raw?.devicePixelRatio ?? raw?.dpr ?? raw?.deviceScaleFactor ?? 1);
  if (!Number.isFinite(value) || value <= 0) return 1;
  return Math.min(1.5, Math.max(1, Math.round(value * 100) / 100));
}

export function frameScreenshotOptions(deviceScaleFactor = 1) {
  return {
    type: "jpeg",
    quality: FRAME_JPEG_QUALITY,
    scale: Number(deviceScaleFactor) > 1.05 ? "device" : "css",
  };
}
const STATIC_BODY_TYPES = new Set([
  "stylesheet",
  "image",
  "font",
  "media",
  "manifest",
  "texttrack",
  "script",
]);
const SEALED_HEADER_NAMES = new Set(["authorization", "cookie", "set-cookie", "proxy-authorization"]);

export function shouldStoreResponseBody(resourceType) {
  return !STATIC_BODY_TYPES.has(String(resourceType || "").toLowerCase());
}

export function shouldRecordConsole(type) {
  const kind = String(type || "").toLowerCase();
  return kind === "error" || kind === "warning";
}

export function shouldFlushFrame(event) {
  const kind = String(event?.kind || "");
  if (kind === "pointer_up") return true;
  if (kind === "key" && /^(Enter|Escape)$/i.test(String(event.key || ""))) return true;
  return false;
}

function actionScopes(page) {
  if (!page) return [];
  const frames = typeof page.frames === "function" ? page.frames() : [];
  return frames.length ? frames : [page];
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function locatorBuilders(parsed) {
  const name = parsed.name || parsed.value || "";
  const exactText = new RegExp(`^\\s*${escapeRegExp(name)}\\s*$`);
  if (parsed.kind === "ref") {
    return [(scope) => scope.locator(`[data-pi-ref="${parsed.value}"]`)];
  }
  if (parsed.kind === "placeholder") {
    return [
      (scope) => scope.getByPlaceholder(parsed.value, { exact: true }),
      (scope) => scope.getByPlaceholder(parsed.value, { exact: false }),
      (scope) => scope.locator(`[placeholder="${parsed.value}"]`),
    ];
  }
  if (parsed.kind === "label") {
    return [
      (scope) => scope.getByLabel(parsed.value, { exact: true }),
      (scope) => scope.getByLabel(parsed.value, { exact: false }),
      (scope) => scope.locator("label").filter({ hasText: exactText }),
      (scope) => scope.getByText(parsed.value, { exact: true }),
    ];
  }
  if (parsed.kind === "role") {
    if (parsed.role === "checkbox" || parsed.role === "radio") {
      if (!parsed.name) {
        return [
          (scope) => scope.getByRole(parsed.role),
          (scope) => scope.locator(`input[type='${parsed.role}']`),
        ];
      }
      return [
        (scope) => scope.getByRole(parsed.role, { name: parsed.name, exact: true }),
        (scope) => scope.getByRole(parsed.role, { name: parsed.name, exact: false }),
        (scope) => scope.locator("label").filter({ hasText: exactText }),
        (scope) => scope.getByText(parsed.name, { exact: true }),
      ];
    }
    if (!parsed.name) return [(scope) => scope.getByRole(parsed.role)];
    return [
      (scope) => scope.getByRole(parsed.role, { name: parsed.name, exact: true }),
      (scope) => scope.getByRole(parsed.role, { name: parsed.name, exact: false }),
      (scope) => scope.locator("button, [role='button'], a, input[type='button'], input[type='submit']")
        .filter({ hasText: exactText }),
      (scope) => scope.getByText(parsed.name, { exact: true }),
    ];
  }
  return [
    (scope) => scope.getByText(parsed.value, { exact: true }),
    (scope) => scope.getByRole("button", { name: parsed.value, exact: false }),
  ];
}

async function firstVisibleInFrames(page, build, { exactFirst = false, limit = 12 } = {}) {
  const builders = exactFirst
    ? [
      (scope) => build(scope, true),
      (scope) => build(scope, false),
    ]
    : [(scope) => build(scope, false)];
  for (const make of builders) {
    for (const scope of actionScopes(page)) {
      let loc;
      try {
        loc = make(scope);
      } catch {
        continue;
      }
      const count = await loc.count().catch(() => 0);
      for (let index = 0; index < Math.min(count, limit); index += 1) {
        const item = loc.nth(index);
        if (await item.isVisible().catch(() => false)) return item;
        const box = await item.boundingBox().catch(() => null);
        if (box && box.width >= 2 && box.height >= 2) return item;
      }
    }
  }
  return null;
}

async function clickInFrames(page, build, timeout, options = {}) {
  const item = await firstVisibleInFrames(page, build, options);
  if (!item) throw new Error("页面动作没有找到目标");
  await item.click({ timeout });
}

async function fillInFrames(page, build, value, timeout, options = {}) {
  const item = await firstVisibleInFrames(page, build, options);
  if (!item) throw new Error("页面动作没有找到目标");
  await item.fill(value, { timeout });
}

async function clickFirstVisible(page, texts, timeout = 1200) {
  for (const text of texts) {
    const item = await firstVisibleInFrames(
      page,
      (scope, exact) => scope.getByText(text, { exact }),
      { exactFirst: true, limit: 6 },
    );
    if (!item) continue;
    await item.click({ timeout }).catch(() => {});
    return true;
  }
  return false;
}

async function exerciseListPage(page) {
  await clickFirstVisible(page, ["展开筛选", "高级搜索", "展开"]);
  await page.waitForTimeout(300);
  for (const scope of actionScopes(page)) {
    const selects = scope.locator(
      ".el-form .el-select, .el-form .ant-select, form .el-select, .search-form .el-select, .ant-pro-table-search .ant-select, .el-table-filter",
    );
    const selectCount = await selects.count().catch(() => 0);
    for (let index = 0; index < Math.min(selectCount, 12); index += 1) {
      await selects.nth(index).click({ timeout: 800 }).catch(() => {});
      await page.waitForTimeout(450);
      const option = scope.locator(
        ".el-select-dropdown:visible .el-select-dropdown__item, .ant-select-dropdown:visible .ant-select-item-option",
      ).first();
      if (await option.isVisible().catch(() => false)) {
        await option.click({ timeout: 800 }).catch(() => {});
      } else {
        await page.keyboard.press("Escape").catch(() => {});
      }
      await page.waitForTimeout(150);
    }
    const dates = scope.locator(".el-date-editor, .ant-picker");
    const dateCount = await dates.count().catch(() => 0);
    for (let index = 0; index < Math.min(dateCount, 4); index += 1) {
      await dates.nth(index).click({ timeout: 800 }).catch(() => {});
      await page.waitForTimeout(350);
      await page.keyboard.press("Escape").catch(() => {});
    }
    const inputs = scope.locator(
      ".el-form input.el-input__inner, .search-form input, .ant-pro-table-search input:not([readonly]), form input[type='text']",
    );
    const inputCount = await inputs.count().catch(() => 0);
    for (let index = 0; index < Math.min(inputCount, 8); index += 1) {
      const box = inputs.nth(index);
      if (!(await box.isVisible().catch(() => false))) continue;
      const readonly = await box.getAttribute("readonly").catch(() => null);
      if (readonly !== null) continue;
      await box.fill("1", { timeout: 800 }).catch(() => {});
    }
  }
  await clickFirstVisible(page, ["搜索", "查询"]);
  await page.waitForTimeout(800);
  await clickFirstVisible(page, ["查看", "详情"]);
  await page.waitForTimeout(800);
}

function headerEntries(headers) {
  if (!headers) return [];
  if (Array.isArray(headers)) return headers;
  return Object.entries(headers).map(([name, value]) => [name, value]);
}

function isolateHeaders(headers, sealed) {
  const visible = {};
  for (const [name, value] of headerEntries(headers)) {
    const key = String(name);
    if (SEALED_HEADER_NAMES.has(key.toLowerCase())) {
      const ref = `auth_ref_${randomUUID().replaceAll("-", "")}`;
      sealed[ref] = { name: key, value: String(value ?? "") };
      visible[key] = `[sealed:${ref}]`;
    } else {
      visible[key] = String(value ?? "");
    }
  }
  return visible;
}

async function bodyAsBuffer(source) {
  if (source == null) return null;
  if (Buffer.isBuffer(source)) return source;
  if (source instanceof Uint8Array) return Buffer.from(source);
  if (typeof source === "string") return Buffer.from(source);
  if (typeof source.buffer === "function") {
    try {
      const value = await source.buffer();
      return Buffer.isBuffer(value) ? value : Buffer.from(value);
    } catch {
      return null;
    }
  }
  return null;
}

async function writeStorageToPage(page, context, state) {
  if (!state || typeof state !== "object") return;
  if (Array.isArray(state.cookies) && state.cookies.length) {
    await context.addCookies(state.cookies);
  }
  const rows = [];
  for (const origin of state.origins || []) {
    for (const item of origin.localStorage || []) {
      rows.push(item);
    }
  }
  if (!rows.length) return;
  await page.evaluate((items) => {
    for (const item of items) {
      window.localStorage.setItem(String(item.name), String(item.value ?? ""));
    }
  }, rows);
}

async function waitForPageReady(page) {
  await page.waitForLoadState("load", { timeout: 8000 }).catch(() => {});
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
}

const routeSnapshotTimers = new WeakMap();

async function revealCollapsedFilters(page) {
  for (const scope of actionScopes(page)) {
    try {
      await scope.evaluate(() => {
        const expandRe = /^(展开|展开筛选|高级搜索|高级|更多筛选|Expand|Advanced)$/i;
        const roots = document.querySelectorAll(
          ".search-form, .ant-pro-table-search, .el-form--inline, .filter-container, .table-search, .vxe-grid--form-wrapper, [class*='search-form'], [class*='table-search'], [class*='filter-bar'], [class*='search-bar'], [class*='filter-form'], [class*='query-form']",
        );
        for (const root of roots) {
          for (const btn of root.querySelectorAll("button, a, .el-button, .ant-btn, span, [role='button']")) {
            const text = String(btn.innerText || btn.textContent || "").replace(/\s+/g, "");
            if (!expandRe.test(text) && !/展开筛选|高级搜索/.test(text)) continue;
            try {
              btn.click();
            } catch {
              // ignore
            }
          }
        }
      });
    } catch {
      // ignore
    }
  }
}

function scheduleVisibleSnapshot(page, handle, append, reason = "routed", delayMs = 700) {
  if (!page || page.isClosed?.() || handle.closed) return;
  const previous = routeSnapshotTimers.get(page);
  if (previous) clearTimeout(previous);
  routeSnapshotTimers.set(page, setTimeout(() => {
    snapshotVisibleControls(page, handle, append, reason).catch(() => {});
  }, delayMs));
}

function scheduleRoutedSnapshot(page, handle, append) {
  scheduleVisibleSnapshot(page, handle, append, "routed", 700);
}

function shouldResnapshotAfterAct(kind) {
  return /^(click|change|choose|fill|select)$/.test(String(kind || ""));
}

async function snapshotVisibleControls(page, handle, append, reason) {
  if (!page || page.isClosed?.() || handle.closed) return;
  await revealCollapsedFilters(page);
  await page.waitForTimeout(250).catch(() => {});
  const controls = [];
  for (const scope of actionScopes(page)) {
    try {
      const facts = await scope.evaluate(collectPageFacts);
      const rows = Array.isArray(facts?.visible) ? facts.visible : [];
      if (rows.length) controls.push(...rows);
    } catch {
      // iframe 未就绪时跳过，不得因此丢掉其它 frame 的控件
    }
  }
  if (!controls.length) return;
  await append("visible_control", {
    page_id: handle.pageIds.get(page) || "",
    url: page.url(),
    reason: String(reason || ""),
    controls,
  });
}

export class PlaywrightBrowser {
  #actionQueue = Promise.resolve();

  constructor({ browser, context, page, recordingId, targetUrl = "" }) {
    this.browser = browser;
    this.context = context;
    this.page = null;
    this.pages = [];
    this.pageIds = new WeakMap();
    this.recordingId = recordingId;
    this.targetUrl = targetUrl;
    this.started = true;
    this.closed = false;
    this.pendingWrites = 0;
    this.requestIds = new WeakMap();
    this.sealed = {};
    this.lastPointerMoveAt = 0;
    this.lastHumanActAt = 0;
    this.lastActAt = 0;
    this.piActDepth = 0;
    this.piActUntil = 0;
    this.userActions = [];
    this.lastSnapshot = null;
    this.appendEvidence = null;
    this.sawLoginPage = false;
    this.snapshotVisibleControls = async () => {};
    this.viewport = DEFAULT_VIEWPORT;
    this.deviceScaleFactor = 1;
    if (page) this.adoptPage(page);
  }

  async setViewport(next) {
    const size = normalizeViewport(next);
    if (!size || this.closed) return null;
    this.viewport = size;
    const pages = this.pages.filter((item) => !item.isClosed());
    await Promise.all(pages.map((item) => item.setViewportSize(size).catch(() => {})));
    return size;
  }

  livePage() {
    if (this.page && !this.page.isClosed()) return this.page;
    const next = this.pages.find((item) => !item.isClosed());
    this.page = next || null;
    return this.page;
  }

  adoptPage(page) {
    if (!page || page.isClosed()) return false;
    if (!this.pages.includes(page)) this.pages.push(page);
    const switched = this.page && this.page !== page && !this.page.isClosed();
    this.page = page;
    if (this.viewport) page.setViewportSize(this.viewport).catch(() => {});
    page.bringToFront().catch(() => {});
    if (switched) logPiOnly("已跟随到新页面");
    return true;
  }

  dropPage(page) {
    this.pages = this.pages.filter((item) => item !== page && !item.isClosed());
    if (this.page === page) {
      this.page = this.pages.at(-1) || null;
      this.page?.bringToFront?.().catch(() => {});
    }
  }

  async persistSession() {
    if (this.closed || !this.context) return false;
    const state = await this.context.storageState();
    if (!looksLoggedIn(state)) return false;
    const url = this.livePage()?.url() || this.targetUrl;
    return saveStorageState(url, state);
  }

  async applySession({ localStorage: store = null, cookies = null, url = "" } = {}) {
    const page = this.livePage();
    if (this.closed || !page) return { url: "" };
    if (Array.isArray(cookies) && cookies.length && this.context) {
      await this.context.addCookies(cookies);
    }
    if (store && typeof store === "object") {
      await page.evaluate((pairs) => {
        for (const [key, value] of Object.entries(pairs)) {
          window.localStorage.setItem(String(key), String(value ?? ""));
        }
      }, store);
    }
    const nextUrl = String(url || "").trim();
    if (nextUrl) {
      await page.goto(nextUrl, { waitUntil: "domcontentloaded" });
    } else {
      await page.reload({ waitUntil: "domcontentloaded" });
    }
    await waitForPageReady(page);
    await this.persistSession().catch(() => false);
    return { url: page.url() };
  }

  async act({ action = "", selector = "", text = "", timeout = 8000 } = {}) {
    const page = this.livePage();
    if (this.closed || !page) return { url: "" };
    const kind = String(action || "").trim();
    const loc = String(selector || "").trim();
    const value = String(text || "");
    if (kind === "click_text" && value) {
      await clickInFrames(
        page,
        (scope, exact) => scope.getByText(value, { exact }),
        timeout,
        { exactFirst: true },
      );
    } else if (kind === "click_role" && value) {
      await clickInFrames(
        page,
        (scope) => scope.getByRole(loc || "button", { name: value, exact: true }),
        timeout,
      );
    } else if (kind === "click" && loc) {
      await clickInFrames(page, (scope) => scope.locator(loc), timeout);
    } else if (kind === "fill" && loc) {
      await fillInFrames(page, (scope) => scope.locator(loc), value, timeout);
    } else if (kind === "fill_placeholder" && loc) {
      await fillInFrames(
        page,
        (scope, exact) => scope.getByPlaceholder(loc, { exact }),
        value,
        timeout,
        { exactFirst: true },
      );
    } else if (kind === "press" && value) {
      await page.keyboard.press(value);
    } else if (kind === "wait") {
      await page.waitForTimeout(Number(timeout) || 800);
    } else if (kind === "exercise_list") {
      await exerciseListPage(page);
    } else if (kind === "evaluate" && value) {
      const results = [];
      for (const scope of actionScopes(page)) {
        try {
          results.push({ url: scope.url(), value: await scope.evaluate(value) });
        } catch (error) {
          results.push({ url: scope.url(), error: error.message || String(error) });
        }
      }
      await page.waitForTimeout(200);
      return { url: this.livePage()?.url() || "", results };
    } else {
      throw new Error(`不支持的页面动作: ${kind || "(empty)"}`);
    }
    await page.waitForTimeout(400);
    return { url: this.livePage()?.url() || "" };
  }

  enqueue(task) {
    const run = this.#actionQueue.then(task, task);
    this.#actionQueue = run.then(() => {}, () => {});
    return run;
  }

  recentlyHuman(ms = 800) {
    return Date.now() - Number(this.lastHumanActAt || 0) < ms;
  }

  beginPiAct() {
    this.piActDepth = Number(this.piActDepth || 0) + 1;
  }

  endPiAct() {
    this.piActDepth = Math.max(0, Number(this.piActDepth || 0) - 1);
    this.piActUntil = Date.now() + 400;
  }

  isPiActing() {
    return Number(this.piActDepth || 0) > 0 || Date.now() < Number(this.piActUntil || 0);
  }

  rememberUserAction(payload = {}) {
    this.userActions = [...(this.userActions || []), {
      kind: payload.kind || "click",
      label: payload.label || payload.text || payload.placeholder || "",
      selector: payload.selector || "",
      at: Date.now(),
    }].slice(-12);
  }

  recentUserActions() {
    return this.userActions || [];
  }

  listPages() {
    const current = this.livePage();
    return {
      pages: this.pages.filter((item) => !item.isClosed()).map((item) => ({
        url: item.url(),
        current: item === current,
      })),
    };
  }

  async openPage(url) {
    return this.enqueue(async () => {
      const page = this.livePage();
      if (this.closed || !page) return { available: false, error: "浏览器未打开" };
      const next = String(url || "").trim();
      if (next) {
        await page.goto(next, { waitUntil: "domcontentloaded" });
        await waitForPageReady(page);
      }
      this.lastActAt = Date.now();
      return { available: true, url: page.url() };
    });
  }

  async inspect({ includeScreenshot = false } = {}) {
    return this.enqueue(async () => {
      const page = this.livePage();
      if (this.closed || !page) return { available: false, error: "浏览器未打开" };
      const frames = [];
      const visible = [];
      for (const scope of actionScopes(page)) {
        try {
          const facts = await scope.evaluate(collectPageFacts);
          frames.push({
            url: facts?.url || "",
            title: facts?.title || "",
            controls: Array.isArray(facts?.controls) ? facts.controls : [],
            actions: Array.isArray(facts?.actions) ? facts.actions : [],
            options: Array.isArray(facts?.options) ? facts.options : [],
          });
          if (Array.isArray(facts?.visible) && facts.visible.length) visible.push(...facts.visible);
        } catch {
          // frame 未就绪时跳过
        }
      }
      const snapshot = {
        ...assignSnapshotRefs({
          url: page.url(),
          title: await page.title().catch(() => ""),
          frames,
        }),
        options: [...new Set(frames.flatMap((item) => item.options || []))],
        recentUserActions: this.recentUserActions(),
      };
      this.lastSnapshot = snapshot;
      if (visible.length && typeof this.appendEvidence === "function") {
        await this.appendEvidence("visible_control", {
          page_id: this.pageIds.get(page) || "",
          url: page.url(),
          reason: "snapshot",
          controls: visible,
        });
      }
      if (includeScreenshot) {
        try {
          const bytes = await page.screenshot(frameScreenshotOptions(this.deviceScaleFactor));
          const size = page.viewportSize() || this.viewport || DEFAULT_VIEWPORT;
          snapshot.screenshot = {
            data: Buffer.from(bytes).toString("base64"),
            width: size.width,
            height: size.height,
          };
        } catch (error) {
          snapshot.screenshot = { error: error.message || String(error) };
        }
      }
      return snapshot;
    });
  }

  async actByRef({ ref = "", action = "click", text = "", selector = "" } = {}) {
    return this.actBySelector({ selector: selector || ref, ref: ref || selector, action, text });
  }

  async actBySelector({ selector = "", ref = "", action = "click", text = "" } = {}) {
    return this.enqueue(async () => {
      const page = this.livePage();
      if (this.closed || !page) return { ok: false, error: "浏览器未打开" };
      const token = String(selector || ref || "").trim();
      if (!token) return { ok: false, error: "缺少 selector。先 snapshot，用 placeholder= / label= / role=。" };
      const kind = String(action || "click");
      this.beginPiAct();
      try {
        if (kind === "choose") {
          return await this.#chooseNow(page, token, text);
        }
        const handle = await this.#locateNow(page, token);
        if (!handle) {
          return {
            ok: false,
            code: "not_found",
            error: `找不到 ${token}。重新 snapshot，用 placeholder= / label= / role=，不要点第一个重名控件。`,
          };
        }
        if (kind === "fill") {
          const input = handle.locator("input, textarea, [contenteditable='true']").first();
          const target = (await input.count()) ? input : handle;
          const written = await this.#fillValue(target, String(text ?? ""));
          if (!written.ok) {
            return {
              ok: false,
              code: "not_writable",
              error: "这一格写不进。",
              selector: token,
              ref: token,
              action: kind,
            };
          }
        } else if (kind === "select") {
          await handle.selectOption(String(text ?? "")).catch(async () => this.#mouseClick(handle));
        } else if (kind === "press") {
          await handle.press(String(text || "Enter"));
        } else {
          await this.#mouseClick(handle);
        }
        this.lastActAt = Date.now();
        if (typeof this.appendEvidence === "function") {
          await this.appendEvidence("interaction", {
            actor: "pi",
            kind,
            ref: token,
            selector: token,
            text: String(text || "").slice(0, 80),
          });
        }
        this.#scheduleControlResnapshot(page, kind);
        if (kind === "click") await page.waitForTimeout(50);
        const panel = kind === "click" ? await this.#observePanel(page) : null;
        return {
          ok: true,
          url: page.url(),
          selector: token,
          ref: token,
          action: kind,
          ...(panel && panel.panel !== "none" ? { panel: panel.panel, options: panel.options } : {}),
        };
      } catch (error) {
        return {
          ok: false,
          code: kind === "fill" ? "not_writable" : "not_found",
          error: kind === "fill" ? "这一格写不进。" : (error.message || String(error)),
        };
      } finally {
        this.endPiAct();
      }
    });
  }

  #advertisedRef(token) {
    const snap = this.lastSnapshot;
    if (!snap || !token) return "";
    const items = [...(snap.controls || []), ...(snap.actions || [])];
    const exact = items.filter((item) => (
      item.selector === token
      || item.ref === token
      || `ref=${item.ref}` === token
    ));
    if (!exact.length) return "";
    const preferred = exact.find((item) => item.region === "dialog")
      || exact.find((item) => item.region === "form")
      || exact[exact.length - 1];
    return String(preferred?.ref || "");
  }

  async #locateNow(page, token) {
    const advertised = this.#advertisedRef(token);
    if (advertised) {
      const marked = await firstVisibleInFrames(
        page,
        (scope) => scope.locator(`[data-pi-ref="${advertised}"]`),
        { limit: 4 },
      );
      if (marked) return marked;
    }
    const parsed = parseLocator(token);
    for (const build of locatorBuilders(parsed)) {
      const found = await firstVisibleInFrames(page, (scope) => build(scope), { limit: 16 });
      if (found) return found;
    }
    return null;
  }

  async #fillValue(locator, value) {
    try {
      await locator.fill(value);
      return { ok: true };
    } catch {
      const written = await locator.evaluate((el, next) => {
        const writable = (node) => {
          if (!node || node.disabled) return false;
          if (node.isContentEditable) return true;
          const tag = String(node.tagName || "").toLowerCase();
          if (tag === "textarea") return true;
          if (tag !== "input") return false;
          const type = String(node.type || "text").toLowerCase();
          return !/^(button|submit|reset|checkbox|radio|file|image)$/.test(type);
        };
        const root = el;
        const nested = [...(root.querySelectorAll?.("input, textarea, [contenteditable='true']") || [])];
        const target = writable(root) ? root : nested.find(writable);
        if (!target) return false;
        if (target.isContentEditable) target.textContent = next;
        else target.value = next;
        target.dispatchEvent(new Event("input", { bubbles: true }));
        target.dispatchEvent(new Event("change", { bubbles: true }));
        if (target.isContentEditable) return String(target.textContent || "").includes(String(next));
        return String(target.value) === String(next);
      }, value).catch(() => false);
      return { ok: Boolean(written) };
    }
  }

  async #visibleBox(locator, timeout = 400) {
    await locator.scrollIntoViewIfNeeded({ timeout }).catch(() => {});
    return locator.boundingBox({ timeout }).catch(() => null);
  }

  async #actionBox(locator) {
    const hostBox = await this.#visibleBox(locator);
    const kind = await locator.evaluate((el) => {
      const tag = String(el.tagName || "").toLowerCase();
      const role = String(el.getAttribute("role") || "").toLowerCase();
      if (/^(button|a|option|summary)$/.test(tag)) return "self";
      if (/^(button|link|option|tab|menuitem|radio|checkbox)$/.test(role)) return "self";
      if (tag === "input" || tag === "textarea" || tag === "select") return "self";
      return "host";
    }).catch(() => "self");
    if (kind === "self" || !hostBox) return hostBox;
    const inner = locator.locator("input, textarea, select, button, [role='button']");
    const count = await inner.count().catch(() => 0);
    let best = null;
    for (let index = 0; index < Math.min(count, 8); index += 1) {
      const box = await this.#visibleBox(inner.nth(index), 200);
      if (!box || box.width < 4 || box.height < 4) continue;
      if (!best || (box.width * box.height) < (best.width * best.height)) best = box;
    }
    if (best && (hostBox.width > best.width * 2 || hostBox.height > best.height * 2)) return best;
    return hostBox;
  }

  async #mouseClick(locator) {
    const page = this.livePage();
    const box = await this.#actionBox(locator);
    if (!page || !box || box.width < 1 || box.height < 1) {
      await locator.click({ timeout: 800, force: true });
      return { via: "locator", x: 0, y: 0 };
    }
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.click(x, y);
    return { via: "mouse", x, y };
  }

  async #listRoleTexts(page, role) {
    const labels = [];
    const seen = new Set();
    for (const scope of actionScopes(page)) {
      const nodes = scope.getByRole(role);
      const count = await nodes.count().catch(() => 0);
      for (let index = 0; index < Math.min(count, 40); index += 1) {
        const item = nodes.nth(index);
        if (!(await item.isVisible({ timeout: 0 }).catch(() => false))) continue;
        const text = String(await item.innerText({ timeout: 200 }).catch(() => "")).replace(/\s+/g, " ").trim();
        if (!text || seen.has(text)) continue;
        seen.add(text);
        labels.push(text);
      }
    }
    return labels;
  }

  async #looksLikeCalendar(page) {
    for (const scope of actionScopes(page)) {
      const days = await scope.evaluate(() => {
        const nodes = [...document.querySelectorAll("td, [role='gridcell'], button, span, div")];
        let count = 0;
        for (const node of nodes) {
          const text = String(node.textContent || "").replace(/\s+/g, "");
          if (!/^(3[01]|[12]?\d)$/.test(text)) continue;
          const box = node.getBoundingClientRect?.();
          if (!box || box.width < 2 || box.height < 2 || box.width > 80 || box.height > 80) continue;
          count += 1;
          if (count >= 20) return true;
        }
        return false;
      }).catch(() => false);
      if (days) return true;
    }
    return false;
  }

  async #observePanel(page) {
    const options = await this.#listRoleTexts(page, "option");
    if (options.length) return { panel: "list", options };
    const radios = await this.#listRoleTexts(page, "radio");
    const tabs = await this.#listRoleTexts(page, "tab");
    if (radios.length >= 2 || tabs.length >= 2) {
      return { panel: "inline", options: [...radios, ...tabs] };
    }
    if (await this.#looksLikeCalendar(page)) return { panel: "calendar", options: [] };
    return { panel: "none", options: [] };
  }

  async #dismissOpenChooser(page) {
    await page.waitForTimeout(80);
    await page.keyboard.press("Escape").catch(() => {});
  }

  async #waitChoice(page, label, timeoutMs = 1600) {
    const deadline = Date.now() + Math.max(80, Number(timeoutMs) || 1600);
    while (Date.now() < deadline) {
      for (const scope of actionScopes(page)) {
        const builders = [
          () => scope.getByRole("option", { name: label, exact: true }),
          () => scope.getByRole("radio", { name: label, exact: true }),
          () => scope.getByRole("tab", { name: label, exact: true }),
          () => scope.getByText(label, { exact: true }),
        ];
        for (const build of builders) {
          let loc;
          try {
            loc = build();
          } catch {
            continue;
          }
          const count = await loc.count().catch(() => 0);
          for (let index = 0; index < Math.min(count, 8); index += 1) {
            const item = loc.nth(index);
            if (!(await item.isVisible({ timeout: 0 }).catch(() => false))) continue;
            const box = await this.#visibleBox(item, 200);
            if (box && box.width > 1 && box.height > 1 && box.height < 120) return item;
          }
        }
      }
      await page.waitForTimeout(40);
    }
    return null;
  }

  async #chooseNow(page, token, text) {
    const label = String(text || "").trim();
    if (!label) return { ok: false, code: "option_not_seen", error: "choose 需要可见选项原文" };
    const field = await this.#locateNow(page, token);
    if (!field) {
      return { ok: false, code: "not_found", error: `找不到 ${token}` };
    }
    const tag = await field.evaluate((el) => String(el.tagName || ""), { timeout: 800 }).catch(() => "");
    const nestedSelects = await field.locator("select").count().catch(() => 0);
    if (tag === "SELECT" || nestedSelects) {
      const target = tag === "SELECT" ? field : field.locator("select").first();
      const picked = await target.selectOption({ label }, { timeout: 800 })
        .then(() => true)
        .catch(() => target.selectOption(label, { timeout: 400 }).then(() => true).catch(() => false));
      if (!picked) {
        return {
          ok: false,
          code: "option_not_seen",
          error: `没有可见项：${label}。`,
          panel: "list",
          options: [],
        };
      }
    } else {
      let option = await this.#waitChoice(page, label, 400);
      if (!option) {
        await this.#mouseClick(field);
        option = await this.#waitChoice(page, label, 800);
      }
      if (!option) {
        await page.keyboard.press("ArrowDown").catch(() => {});
        option = await this.#waitChoice(page, label, 800);
      }
      if (!option) {
        const seen = await this.#observePanel(page);
        const visible = seen.options.slice(0, 12).join("、");
        const where = seen.panel === "calendar"
          ? "打开后是日历。"
          : seen.panel === "list"
            ? `可见：${visible}`
            : seen.panel === "inline"
              ? `可见：${visible}`
              : "打开后没有可见项。";
        return {
          ok: false,
          code: "option_not_seen",
          error: `没有可见项：${label}。${where}`,
          panel: seen.panel,
          options: seen.options,
        };
      }
      await this.#mouseClick(option);
      await this.#dismissOpenChooser(page);
    }
    this.lastActAt = Date.now();
    if (typeof this.appendEvidence === "function") {
      await this.appendEvidence("interaction", {
        actor: "pi",
        kind: "choose",
        selector: token,
        text: label,
      });
    }
    this.#scheduleControlResnapshot(page, "choose");
    return { ok: true, url: page.url(), selector: token, action: "choose", text: label };
  }

  #scheduleControlResnapshot(page, kind) {
    if (!shouldResnapshotAfterAct(kind) || typeof this.appendEvidence !== "function") return;
    scheduleVisibleSnapshot(page, this, this.appendEvidence, "interaction", 400);
  }

  async fillFields(fields = []) {
    const rows = Array.isArray(fields) ? fields : [];
    const results = [];
    for (const field of rows) {
      results.push(await this.actByRef({
        ref: field?.ref,
        action: "fill",
        text: field?.value ?? field?.text ?? "",
      }));
    }
    return { ok: results.every((item) => item.ok), filled: results.filter((item) => item.ok).length, results };
  }

  async applyInput(event) {
    return this.#applyInputNow(event);
  }

  async #applyInputNow(event) {
    const page = this.livePage();
    if (this.closed || !page) return;
    const viewport = page.viewportSize() || this.viewport || DEFAULT_VIEWPORT;
    const x = Number.isFinite(Number(event.x))
      ? Number(event.x)
      : Math.round(Number(event.nx || 0) * viewport.width);
    const y = Number.isFinite(Number(event.y))
      ? Number(event.y)
      : Math.round(Number(event.ny || 0) * viewport.height);
    const kind = String(event.kind || "");
    if (kind === "goto" && event.url) {
      await page.goto(String(event.url), { waitUntil: "domcontentloaded" });
      await waitForPageReady(page);
      return;
    }
    if (kind === "pointer_move") {
      const now = Date.now();
      if (now - this.lastPointerMoveAt < POINTER_MOVE_MIN_INTERVAL_MS) return;
      this.lastPointerMoveAt = now;
      await page.mouse.move(x, y);
      return;
    }
    if (kind === "pointer_down") {
      this.lastHumanActAt = Date.now();
      this.lastActAt = Date.now();
      this.rememberUserAction({ kind: "click" });
      await page.mouse.move(x, y);
      await page.mouse.down({ button: event.button || "left" });
      return;
    }
    if (kind === "pointer_up") {
      this.lastHumanActAt = Date.now();
      this.lastActAt = Date.now();
      await page.mouse.move(x, y);
      await page.mouse.up({ button: event.button || "left" });
      return;
    }
    if (kind === "scroll") {
      this.lastHumanActAt = Date.now();
      this.lastActAt = Date.now();
      await page.mouse.wheel(Number(event.dx || 0), Number(event.dy || 0));
      return;
    }
    if (kind === "text" && event.text) {
      this.lastHumanActAt = Date.now();
      this.lastActAt = Date.now();
      await page.keyboard.type(String(event.text));
      return;
    }
    if (kind === "key" && event.key) {
      this.lastHumanActAt = Date.now();
      this.lastActAt = Date.now();
      await page.keyboard.press(String(event.key));
    }
  }

  async captureFrame() {
    const page = this.livePage();
    if (this.closed || !page) return null;
    try {
      const bytes = await page.screenshot(frameScreenshotOptions(this.deviceScaleFactor));
      const size = page.viewportSize() || this.viewport || DEFAULT_VIEWPORT;
      return {
        data: Buffer.from(bytes).toString("base64"),
        width: size.width,
        height: size.height,
      };
    } catch {
      return null;
    }
  }

  async close() {
    try {
      await this.snapshotVisibleControls("closing");
    } catch {
      // 控件快照失败不得改写结果
    }
    try {
      await this.persistSession();
    } catch {
      // 保存登录态失败不得改写结果
    }
    const startedAt = Date.now();
    while (this.pendingWrites > 0 && Date.now() - startedAt < 10000) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    this.closed = true;
    this.started = false;
    try {
      await this.browser?.close();
    } catch {
      // ignore
    }
  }
}

export async function createPlaywrightBrowser({ recording, appendEvidence }) {
  let chromium;
  try {
    ({ chromium } = await import("playwright"));
  } catch (error) {
    throw new Error(`浏览器无法启动：${error.message}`);
  }

  const headed = process.env.PI_CHECK_HEADED === "1";
  const launchOptions = { headless: !headed };
  const browser = await chromium.launch({ ...launchOptions, channel: "chrome" }).catch(() => (
    chromium.launch(launchOptions)
  ));
  const viewport = normalizeViewport(recording.viewport) || DEFAULT_VIEWPORT;
  const deviceScaleFactor = normalizeDeviceScale(recording.viewport);
  const contextOptions = {
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    viewport,
    deviceScaleFactor,
  };
  const storage = await resolveStorageState(recording.targetUrl, recording.storageState);
  if (storage) contextOptions.storageState = storage;
  let context;
  try {
    context = await browser.newContext(contextOptions);
  } catch {
    delete contextOptions.storageState;
    context = await browser.newContext(contextOptions);
  }
  context.setDefaultTimeout(4000);
  context.setDefaultNavigationTimeout(12000);
  const page = await context.newPage();
  const handle = new PlaywrightBrowser({
    browser,
    context,
    page,
    recordingId: recording.id,
    targetUrl: recording.targetUrl,
  });
  handle.viewport = viewport;
  handle.deviceScaleFactor = deviceScaleFactor;
  handle.appendEvidence = appendEvidence;
  if (storage && looksLoggedIn(storage)) {
    logPiOnly("已恢复上次登录态");
  }

  const append = async (kind, payload) => {
    if (handle.closed) return;
    handle.pendingWrites += 1;
    try {
      await appendEvidence(kind, payload);
    } finally {
      handle.pendingWrites -= 1;
    }
  };
  await installContextHooks(context, handle, append);

  const rememberBody = async (bytes) => {
    if (!bytes || bytes.byteLength === 0) {
      return { stored: "inline", text: "", byteLength: 0 };
    }
    if (bytes.byteLength <= BLOB_THRESHOLD) {
      return {
        stored: "inline",
        text: bytes.toString("utf8"),
        byteLength: bytes.byteLength,
      };
    }
    const blob = await recordingWriteBlob(appendEvidence, bytes);
    return {
      stored: "blob",
      blob_id: blob.blobId,
      byteLength: blob.byteLength,
    };
  };

  context.on("page", (nextPage) => {
    attachPage(nextPage, handle, append, rememberBody).catch(() => {});
    handle.adoptPage(nextPage);
  });
  await attachPage(page, handle, append, rememberBody);
  handle.adoptPage(page);
  await page.goto(recording.targetUrl, { waitUntil: "domcontentloaded" });
  await waitForPageReady(page);
  if (isLoginUrl(page.url())) {
    handle.sawLoginPage = true;
    const fresh = await silentLoginStorage(recording.targetUrl).catch(() => null);
    if (fresh) {
      await writeStorageToPage(page, context, fresh);
      await page.goto(recording.targetUrl, { waitUntil: "domcontentloaded" });
      await waitForPageReady(page);
      logPiOnly("已写入登录态");
    }
  }
  await page.waitForTimeout(400);
  const shot = await page.screenshot(frameScreenshotOptions(deviceScaleFactor));
  const blob = await rememberBody(shot);
  await append("screenshot", {
    page_id: "main",
    url: page.url(),
    reason: "page_ready",
    image: blob,
  });
  await snapshotVisibleControls(page, handle, append, "page_ready");
  handle.snapshotVisibleControls = (reason = "manual") => (
    snapshotVisibleControls(handle.livePage(), handle, append, reason)
  );
  await handle.persistSession().catch(() => false);
  return handle;
}

async function recordingWriteBlob(appendEvidence, bytes) {
  if (typeof appendEvidence.saveBlob === "function") {
    return appendEvidence.saveBlob(bytes);
  }
  throw new Error("证据层未提供二进制保存能力");
}

async function installContextHooks(context, handle, append) {
  await context.exposeBinding("__piCheckRecord", async (source, payload) => {
    if (String(payload?.kind || "") === "route_changed") {
      scheduleRoutedSnapshot(source.page, handle, append);
      return;
    }
    const pageId = handle.pageIds.get(source.page) || "";
    const actor = resolveInteractionActor(handle, payload);
    await append("interaction", {
      page_id: pageId,
      ...payload,
      actor,
    });
    if (actor === "human") {
      handle.rememberUserAction?.(payload);
    }
    if (shouldResnapshotAfterAct(payload?.kind)) {
      scheduleVisibleSnapshot(source.page, handle, append, "interaction", 500);
    }
  });
  await context.addInitScript(() => {
    const send = (kind, detail) => {
      try {
        window.__piCheckRecord({ kind, ...detail, href: location.href });
      } catch {
        // ignore
      }
    };
    const fieldHint = (target) => {
      const node = target && target.closest ? target : null;
      const control = node?.closest?.("input, textarea, select, .el-select, .el-input, .ant-select, .ant-picker") || node;
      const item = control?.closest?.(".el-form-item, .ant-form-item, .el-form-item__content, label") || control;
      const labelNode = item?.querySelector?.(".el-form-item__label, .ant-form-item-label, label");
      let label = String(labelNode?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80);
      const cell = node?.closest?.("td, th");
      if (!label && cell) {
        const row = cell.parentElement;
        const index = row ? [...row.children].indexOf(cell) : -1;
        const table = cell.closest?.("table, .el-table, .ant-table, .vxe-table");
        const head = index >= 0
          ? table?.querySelector?.(`thead th:nth-child(${index + 1}), thead td:nth-child(${index + 1})`)
          : null;
        label = String(head?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80);
      }
      if (!label) {
        const dialog = node?.closest?.('dialog, [role="dialog"], [role="alertdialog"], .el-dialog, .ant-modal, .van-dialog');
        const header = dialog?.querySelector?.(".el-dialog__header, .ant-modal-title, .el-dialog__title, [class*='dialog__title'], header");
        label = String(header?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80);
      }
      return {
        placeholder: String(control?.getAttribute?.("placeholder") || target?.placeholder || ""),
        aria_label: String(control?.getAttribute?.("aria-label") || ""),
        label: label.replace(/^[＊*\s]+/, "").replace(/[＊*]\s*$/g, "").trim(),
      };
    };
    document.addEventListener("click", (event) => {
      const target = event.target;
      send("click", {
        tag: target?.tagName || "",
        id: target?.id || "",
        name: target?.getAttribute?.("name") || "",
        text: String(target?.innerText || "").slice(0, 200),
        ...fieldHint(target),
      });
    }, true);
    document.addEventListener("input", (event) => {
      const target = event.target;
      send("input", {
        tag: target?.tagName || "",
        id: target?.id || "",
        name: target?.name || "",
        value: String(target?.value ?? ""),
        ...fieldHint(target),
      });
    }, true);
    document.addEventListener("change", (event) => {
      const target = event.target;
      send("change", {
        tag: target?.tagName || "",
        id: target?.id || "",
        name: target?.name || "",
        value: String(target?.value ?? ""),
        ...fieldHint(target),
      });
    }, true);
    document.addEventListener("submit", (event) => {
      const target = event.target;
      send("submit", {
        tag: target?.tagName || "",
        id: target?.id || "",
        action: target?.action || "",
        method: target?.method || "",
      });
    }, true);
    const notifyRoute = () => send("route_changed", { url: location.href });
    window.addEventListener("hashchange", notifyRoute);
    window.addEventListener("popstate", notifyRoute);
    const wrapHistory = (method) => function wrappedHistory(...args) {
      const result = method.apply(this, args);
      notifyRoute();
      return result;
    };
    history.pushState = wrapHistory(history.pushState);
    history.replaceState = wrapHistory(history.replaceState);
  });
}

async function attachPage(page, handle, append, rememberBody) {
  const pageId = `page_${randomUUID().replaceAll("-", "")}`;
  handle.pageIds.set(page, pageId);
  await append("page_created", {
    page_id: pageId,
    url: page.url(),
  });

  page.on("framenavigated", async (frame) => {
    await append("page_navigated", {
      page_id: pageId,
      frame_id: frame.url(),
      url: frame.url(),
      name: frame.name(),
      is_main: frame === page.mainFrame(),
    });
    if (frame !== page.mainFrame()) return;
    await page.waitForTimeout(500).catch(() => {});
    await snapshotVisibleControls(page, handle, append, "navigated");
    if (isLoginUrl(frame.url())) {
      handle.sawLoginPage = true;
      return;
    }
    if (handle.sawLoginPage) {
      handle.sawLoginPage = false;
      handle.persistSession().catch(() => false);
    }
  });

  page.on("close", async () => {
    handle.dropPage(page);
    await append("page_closed", { page_id: pageId, url: page.url() });
  });

  page.on("dialog", async (dialog) => {
    await append("page_dialog", {
      page_id: pageId,
      type: dialog.type(),
      message: dialog.message(),
    });
    try {
      await dialog.accept();
    } catch {
      // 对话框已关闭
    }
  });

  page.on("popup", (popup) => {
    handle.adoptPage(popup);
  });

  page.on("console", async (message) => {
    if (!shouldRecordConsole(message.type())) return;
    await append("console", {
      page_id: pageId,
      type: message.type(),
      text: message.text(),
      location: message.location(),
    });
  });

  page.on("pageerror", async (error) => {
    await append("page_exception", {
      page_id: pageId,
      message: error.message,
      stack: error.stack || "",
    });
  });

  page.on("request", async (request) => {
    const requestId = `req_${randomUUID().replaceAll("-", "")}`;
    handle.requestIds.set(request, requestId);
    const headers = isolateHeaders(request.headers(), handle.sealed);
    const resourceType = request.resourceType();
    let body = { stored: "omitted", byteLength: 0 };
    if (shouldStoreResponseBody(resourceType)) {
      let rawPost = null;
      try {
        rawPost = await request.postDataBuffer();
      } catch {
        rawPost = request.postData();
      }
      body = await rememberBody(await bodyAsBuffer(rawPost));
    }
    await append("network_request", {
      request_id: requestId,
      page_id: pageId,
      frame_url: request.frame()?.url?.() || "",
      method: request.method(),
      url: request.url(),
      resource_type: resourceType,
      headers,
      body,
    });
  });

  page.on("response", async (response) => {
    const requestId = handle.requestIds.get(response.request()) || "";
    const headers = isolateHeaders(response.headers(), handle.sealed);
    const resourceType = response.request().resourceType();
    let body = { stored: "omitted", byteLength: 0 };
    if (shouldStoreResponseBody(resourceType)) {
      try {
        body = await rememberBody(await bodyAsBuffer(await response.body()));
      } catch (error) {
        body = { stored: "unavailable", error: error.message, byteLength: 0 };
      }
    }
    await append("network_response", {
      request_id: requestId,
      page_id: pageId,
      url: response.url(),
      status: response.status(),
      status_text: response.statusText(),
      headers,
      body,
    });
  });

  page.on("requestfailed", async (request) => {
    await append("network_failure", {
      request_id: handle.requestIds.get(request) || "",
      page_id: pageId,
      url: request.url(),
      method: request.method(),
      error_text: request.failure()?.errorText || "request failed",
    });
  });

}

export function attachBlobSaver(appendEvidence, evidence, recordingId) {
  appendEvidence.saveBlob = (bytes) => evidence.writeBlob(recordingId, bytes);
  return appendEvidence;
}
