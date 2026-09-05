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

async function clickFirstVisible(page, texts, timeout = 1200) {
  for (const text of texts) {
    const loc = page.getByText(text, { exact: false });
    const count = await loc.count().catch(() => 0);
    for (let index = 0; index < Math.min(count, 6); index += 1) {
      const item = loc.nth(index);
      if (!(await item.isVisible().catch(() => false))) continue;
      await item.click({ timeout }).catch(() => {});
      return true;
    }
  }
  return false;
}

async function exerciseListPage(page) {
  await clickFirstVisible(page, ["展开筛选", "高级搜索", "展开"]);
  await page.waitForTimeout(300);
  const selects = page.locator(
    ".el-form .el-select, .el-form .ant-select, form .el-select, .search-form .el-select, .ant-pro-table-search .ant-select, .el-table-filter",
  );
  const selectCount = await selects.count().catch(() => 0);
  for (let index = 0; index < Math.min(selectCount, 12); index += 1) {
    await selects.nth(index).click({ timeout: 800 }).catch(() => {});
    await page.waitForTimeout(450);
    const option = page.locator(
      ".el-select-dropdown:visible .el-select-dropdown__item, .ant-select-dropdown:visible .ant-select-item-option",
    ).first();
    if (await option.isVisible().catch(() => false)) {
      await option.click({ timeout: 800 }).catch(() => {});
    } else {
      await page.keyboard.press("Escape").catch(() => {});
    }
    await page.waitForTimeout(150);
  }
  const dates = page.locator(".el-date-editor, .ant-picker");
  const dateCount = await dates.count().catch(() => 0);
  for (let index = 0; index < Math.min(dateCount, 4); index += 1) {
    await dates.nth(index).click({ timeout: 800 }).catch(() => {});
    await page.waitForTimeout(350);
    await page.keyboard.press("Escape").catch(() => {});
  }
  const inputs = page.locator(
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

export class PlaywrightBrowser {
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
    this.sawLoginPage = false;
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
      await page.getByText(value, { exact: false }).first().click({ timeout });
    } else if (kind === "click" && loc) {
      await page.locator(loc).first().click({ timeout });
    } else if (kind === "fill" && loc) {
      await page.locator(loc).first().fill(value, { timeout });
    } else if (kind === "fill_placeholder" && loc) {
      await page.getByPlaceholder(loc, { exact: false }).first().fill(value, { timeout });
    } else if (kind === "press" && value) {
      await page.keyboard.press(value);
    } else if (kind === "wait") {
      await page.waitForTimeout(Number(timeout) || 800);
    } else if (kind === "exercise_list") {
      await exerciseListPage(page);
    } else {
      throw new Error(`不支持的页面动作: ${kind || "(empty)"}`);
    }
    await page.waitForTimeout(400);
    return { url: this.livePage()?.url() || "" };
  }

  async applyInput(event) {
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
      await page.mouse.move(x, y);
      await page.mouse.down({ button: event.button || "left" });
      return;
    }
    if (kind === "pointer_up") {
      await page.mouse.move(x, y);
      await page.mouse.up({ button: event.button || "left" });
      return;
    }
    if (kind === "scroll") {
      await page.mouse.wheel(Number(event.dx || 0), Number(event.dy || 0));
      return;
    }
    if (kind === "text" && event.text) {
      await page.keyboard.type(String(event.text));
      return;
    }
    if (kind === "key" && event.key) {
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
    const pageId = handle.pageIds.get(source.page) || "";
    await append("interaction", {
      page_id: pageId,
      ...payload,
    });
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
      return {
        placeholder: String(control?.getAttribute?.("placeholder") || target?.placeholder || ""),
        aria_label: String(control?.getAttribute?.("aria-label") || ""),
        label: String(labelNode?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80),
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
