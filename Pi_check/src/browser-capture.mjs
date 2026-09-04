/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 浏览器层只启动/关闭页面，并按发生顺序原样采集事实。
 * 不判断业务请求，不丢弃“看起来不重要”的响应。
 */

import { randomUUID } from "node:crypto";

const BLOB_THRESHOLD = 4096;
const SEALED_HEADER_NAMES = new Set(["authorization", "cookie", "set-cookie", "proxy-authorization"]);

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

export class PlaywrightBrowser {
  constructor({ browser, context, page, recordingId }) {
    this.browser = browser;
    this.context = context;
    this.page = page;
    this.recordingId = recordingId;
    this.started = true;
    this.closed = false;
    this.pendingWrites = 0;
    this.requestIds = new WeakMap();
    this.sealed = {};
  }

  async applySession({ localStorage: store = null, cookies = null, url = "" } = {}) {
    if (this.closed || !this.page) return { url: "" };
    if (Array.isArray(cookies) && cookies.length && this.context) {
      await this.context.addCookies(cookies);
    }
    if (store && typeof store === "object") {
      await this.page.evaluate((pairs) => {
        for (const [key, value] of Object.entries(pairs)) {
          window.localStorage.setItem(String(key), String(value ?? ""));
        }
      }, store);
    }
    const nextUrl = String(url || "").trim();
    if (nextUrl) {
      await this.page.goto(nextUrl, { waitUntil: "domcontentloaded" });
    } else {
      await this.page.reload({ waitUntil: "domcontentloaded" });
    }
    await this.page.waitForTimeout(800);
    return { url: this.page.url() };
  }

  async act({ action = "", selector = "", text = "", timeout = 8000 } = {}) {
    if (this.closed || !this.page) return { url: "" };
    const kind = String(action || "").trim();
    const loc = String(selector || "").trim();
    const value = String(text || "");
    if (kind === "click_text" && value) {
      await this.page.getByText(value, { exact: false }).first().click({ timeout });
    } else if (kind === "click" && loc) {
      await this.page.locator(loc).first().click({ timeout });
    } else if (kind === "fill" && loc) {
      await this.page.locator(loc).first().fill(value, { timeout });
    } else if (kind === "fill_placeholder" && loc) {
      await this.page.getByPlaceholder(loc, { exact: false }).first().fill(value, { timeout });
    } else if (kind === "press" && value) {
      await this.page.keyboard.press(value);
    } else if (kind === "wait") {
      await this.page.waitForTimeout(Number(timeout) || 800);
    } else {
      throw new Error(`不支持的页面动作: ${kind || "(empty)"}`);
    }
    await this.page.waitForTimeout(400);
    return { url: this.page.url() };
  }

  async applyInput(event) {
    if (this.closed || !this.page) return;
    const viewport = this.page.viewportSize() || { width: 1280, height: 800 };
    const x = Number.isFinite(Number(event.x))
      ? Number(event.x)
      : Math.round(Number(event.nx || 0) * viewport.width);
    const y = Number.isFinite(Number(event.y))
      ? Number(event.y)
      : Math.round(Number(event.ny || 0) * viewport.height);
    const kind = String(event.kind || "");
    if (kind === "pointer_move") {
      await this.page.mouse.move(x, y);
      return;
    }
    if (kind === "pointer_down") {
      await this.page.mouse.move(x, y);
      await this.page.mouse.down({ button: event.button || "left" });
      return;
    }
    if (kind === "pointer_up") {
      await this.page.mouse.move(x, y);
      await this.page.mouse.up({ button: event.button || "left" });
      return;
    }
    if (kind === "scroll") {
      await this.page.mouse.wheel(Number(event.dx || 0), Number(event.dy || 0));
      return;
    }
    if (kind === "text" && event.text) {
      await this.page.keyboard.type(String(event.text));
      return;
    }
    if (kind === "key" && event.key) {
      await this.page.keyboard.press(String(event.key));
    }
  }

  async captureFrame() {
    if (this.closed || !this.page) return null;
    const bytes = await this.page.screenshot({ type: "jpeg", quality: 45 });
    const size = this.page.viewportSize() || { width: 1280, height: 800 };
    return {
      data: Buffer.from(bytes).toString("base64"),
      width: size.width,
      height: size.height,
    };
  }

  async close() {
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
  const context = await browser.newContext(
    recording.storageState ? { storageState: recording.storageState } : {},
  );
  const page = await context.newPage();
  const handle = new PlaywrightBrowser({
    browser,
    context,
    page,
    recordingId: recording.id,
  });

  const append = async (kind, payload) => {
    if (handle.closed) return;
    handle.pendingWrites += 1;
    try {
      await appendEvidence(kind, payload);
    } finally {
      handle.pendingWrites -= 1;
    }
  };

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
    attachPage(nextPage, handle, append, rememberBody);
  });
  await attachPage(page, handle, append, rememberBody);
  await page.goto(recording.targetUrl, { waitUntil: "domcontentloaded" });
  const shot = await page.screenshot({ type: "png", fullPage: true });
  const blob = await rememberBody(shot);
  await append("screenshot", {
    page_id: "main",
    url: page.url(),
    reason: "page_ready",
    image: blob,
  });
  return handle;
}

async function recordingWriteBlob(appendEvidence, bytes) {
  if (typeof appendEvidence.saveBlob === "function") {
    return appendEvidence.saveBlob(bytes);
  }
  throw new Error("证据层未提供二进制保存能力");
}

async function attachPage(page, handle, append, rememberBody) {
  const pageId = `page_${randomUUID().replaceAll("-", "")}`;
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
  });

  page.on("close", async () => {
    await append("page_closed", { page_id: pageId, url: page.url() });
  });

  page.on("console", async (message) => {
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
    let rawPost = null;
    try {
      rawPost = await request.postDataBuffer();
    } catch {
      rawPost = request.postData();
    }
    const post = await bodyAsBuffer(rawPost);
    const body = await rememberBody(post);
    await append("network_request", {
      request_id: requestId,
      page_id: pageId,
      frame_url: request.frame()?.url?.() || "",
      method: request.method(),
      url: request.url(),
      resource_type: request.resourceType(),
      headers,
      body,
    });
  });

  page.on("response", async (response) => {
    const requestId = handle.requestIds.get(response.request()) || "";
    const headers = isolateHeaders(response.headers(), handle.sealed);
    let body = { stored: "inline", text: "", byteLength: 0 };
    try {
      body = await rememberBody(await bodyAsBuffer(await response.body()));
    } catch (error) {
      body = { stored: "unavailable", error: error.message, byteLength: 0 };
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

  await page.exposeBinding("__piCheckRecord", async (_source, payload) => {
    await append("interaction", {
      page_id: pageId,
      ...payload,
    });
  });

  await page.addInitScript(() => {
    const send = (kind, detail) => {
      try {
        window.__piCheckRecord({ kind, ...detail, href: location.href });
      } catch {
        // ignore
      }
    };
    document.addEventListener("click", (event) => {
      const target = event.target;
      send("click", {
        tag: target?.tagName || "",
        id: target?.id || "",
        name: target?.getAttribute?.("name") || "",
        text: String(target?.innerText || "").slice(0, 200),
      });
    }, true);
    document.addEventListener("input", (event) => {
      const target = event.target;
      send("input", {
        tag: target?.tagName || "",
        id: target?.id || "",
        name: target?.name || "",
        value: String(target?.value ?? ""),
      });
    }, true);
    document.addEventListener("change", (event) => {
      const target = event.target;
      send("change", {
        tag: target?.tagName || "",
        id: target?.id || "",
        name: target?.name || "",
        value: String(target?.value ?? ""),
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

export function attachBlobSaver(appendEvidence, evidence, recordingId) {
  appendEvidence.saveBlob = (bytes) => evidence.writeBlob(recordingId, bytes);
  return appendEvidence;
}
