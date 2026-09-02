import path from "node:path";
import { chromium, type BrowserContext, type Browser, type Frame, type Request, type Response, type Page } from "playwright";
import type { EvidenceEvent, NetworkEvidence, RecordingSession, UiEvidence } from "../domain.js";
import type { StudioConfig } from "../config.js";
import { appendJsonl, ensureDir, id, writeJson } from "../utils.js";
import { parsePossiblyJson, redactHeaders, redactValue } from "../security/redact.js";
import { UI_RECORDER_SCRIPT } from "./ui-script.js";

// Construct browser-context functions from static source. When this file is
// run through tsx/esbuild, serializing a TypeScript callback can otherwise
// leak bundler helpers (for example `__name`) into the page context.
const INSPECT_TARGET_IN_PAGE = new Function("el", String.raw`
  const text = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 800);
  const form = el.closest("form");
  return {
    text: text(el.textContent || el.value || ""),
    label: el.getAttribute("aria-label") || undefined,
    name: el.getAttribute("name") || undefined,
    type: el.getAttribute("type") || undefined,
    role: el.getAttribute("role") || undefined,
    formText: text((form && form.textContent) || ""),
    formMethod: form ? form.getAttribute("method") || undefined : undefined,
    formAction: form ? form.getAttribute("action") || undefined : undefined
  };
`) as (element: Element) => unknown;

const SNAPSHOT_IN_PAGE = new Function(String.raw`
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 12000);
  const generatedName = (value) => /^(el-id-\d+-\d+|el-[a-z]+-\d+)$/i.test(String(value || ""));
  const labelOf = (el) => {
    if (el.labels?.length) return clean([...el.labels].map(item => item.textContent).join(" "));
    const aria = el.getAttribute("aria-label");
    if (aria) return clean(aria);
    const formItem = el.closest('.el-form-item,.ant-form-item,.arco-form-item,[class*="form-item"]');
    const itemLabel = formItem?.querySelector('label,.el-form-item__label,.ant-form-item-label,[class*="label"]');
    return clean(itemLabel?.textContent || el.getAttribute("placeholder") || "");
  };
  const nameOf = (el) => {
    const named = el.getAttribute("name") || el.getAttribute("data-field") || el.getAttribute("data-name");
    if (named) return named;
    const formItem = el.closest('.el-form-item,.ant-form-item,.arco-form-item,[class*="form-item"]');
    return formItem?.getAttribute("prop") || formItem?.getAttribute("data-prop") || (el.id && !generatedName(el.id) ? el.id : undefined);
  };
  const selectorOf = (el) => {
    const placeholder = el.getAttribute("placeholder");
    if (placeholder) return "placeholder=" + placeholder;
    if (el.id && !generatedName(el.id)) return "#" + CSS.escape(el.id);
    const label = labelOf(el);
    if (label && label.length <= 40) return "label=" + label;
    const role = el.getAttribute("role") || (el.matches("button,.el-button") ? "button" : "");
    const roleName = clean(el.getAttribute("aria-label") || el.textContent || "");
    if (role && roleName && roleName.length <= 40) return "role=" + role + '[name="' + roleName + '"]';
    const testid = el.getAttribute("data-testid");
    if (testid) return '[data-testid="' + CSS.escape(testid) + '"]';
    const name = nameOf(el);
    if (name) return el.tagName.toLowerCase() + '[name="' + CSS.escape(name) + '"]';
    const parts = [];
    let node = el;
    for (let i = 0; node && i < 4; i++, node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      const classes = Array.from(node.classList).slice(0, 2);
      if (classes.length) part += "." + classes.map((item) => CSS.escape(item)).join(".");
      parts.unshift(part);
    }
    return parts.join(" > ");
  };
  const controls = Array.from(document.querySelectorAll(
    'a,button,input,select,textarea,[contenteditable="true"],[role="button"],[role="combobox"],[role="option"],[role="link"],[role="checkbox"],[role="switch"],[role="radio"],[role="tab"],[role="menuitem"]'
  )).filter((el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }).slice(0, 250).map((el) => ({
    selector: selectorOf(el),
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role") || undefined,
    label: labelOf(el) || el.getAttribute("aria-label") || undefined,
    name: nameOf(el),
    type: el.getAttribute("type") || undefined,
    placeholder: el.getAttribute("placeholder") || undefined,
    required: el.hasAttribute("required") || el.getAttribute("aria-required") === "true",
    disabled: el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true",
    value: el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement ? String(el.value || "") : undefined,
    filled: Boolean((el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) && el.value),
    text: clean(el.textContent || el.value || "").slice(0, 300)
  }));
  return {
    title: document.title,
    url: location.href,
    text: clean(document.body.innerText),
    controls
  };
`) as () => unknown;

interface ActiveRecording {
  session: RecordingSession;
  browser?: Browser;
  context: BrowserContext;
  eventsFile: string;
  requestIds: WeakMap<Request, string>;
  lastUiByPage: WeakMap<Page, UiEvidence>;
  recentUi: UiEvidence[];
  externalBrowser: boolean;
}

export class BrowserRecorder {
  private active?: ActiveRecording;

  constructor(private readonly config: StudioConfig) {}

  isActive() {
    return Boolean(this.active);
  }

  activeSession() {
    return this.active?.session;
  }

  async state() {
    if (!this.active) return { active: false as const };
    const page = this.currentPage();
    return {
      active: true as const,
      session: this.active.session,
      url: page.url(),
      title: await page.title().catch(() => ""),
      viewport: page.viewportSize()
    };
  }

  async preview(): Promise<Buffer> {
    const page = this.currentPage();
    return page.screenshot({ type: "png", fullPage: false });
  }

  async reload() {
    const page = this.currentPage();
    await page.reload({ waitUntil: "domcontentloaded" });
    return { url: page.url(), title: await page.title() };
  }

  async start(startUrl: string, name = "recording"): Promise<RecordingSession> {
    if (this.active) throw new Error(`Recording already active: ${this.active.session.id}`);

    const sessionId = id("rec");
    const dir = path.join(this.config.recordingsDir, sessionId);
    await ensureDir(dir);
    await ensureDir(this.config.profileDir);

    const context = await chromium.launchPersistentContext(this.config.profileDir, {
      headless: this.config.headless,
      viewport: { width: 1440, height: 960 },
      args: ["--disable-features=TranslateUI"]
    });

    const eventsFile = path.join(dir, "events.jsonl");
    const session: RecordingSession = {
      id: sessionId,
      name,
      startedAt: new Date().toISOString(),
      startUrl,
      eventsFile
    };

    this.active = {
      session,
      context,
      eventsFile,
      requestIds: new WeakMap(),
      lastUiByPage: new WeakMap(),
      recentUi: [],
      externalBrowser: false
    };

    await writeJson(path.join(dir, "session.json"), session);
    await this.instrument(context);

    const page = context.pages()[0] || await context.newPage();
    await page.goto(startUrl, { waitUntil: "domcontentloaded" });
    return session;
  }

  private async instrument(context: BrowserContext) {
    await context.exposeBinding("__bssRecordUi", async ({ page }, payload: any) => {
      const active = this.active;
      if (!active || !page) return;
      const event: UiEvidence = {
        id: id("ui"),
        kind: "ui",
        sessionId: active.session.id,
        at: new Date().toISOString(),
        pageUrl: String(payload?.pageUrl || page.url()),
        eventType: ["click", "input", "change", "submit"].includes(payload?.eventType) ? payload.eventType : "click",
        selector: payload?.selector,
        tag: payload?.tag,
        role: payload?.role,
        text: payload?.text,
        label: payload?.label,
        name: payload?.name,
        inputType: payload?.inputType,
        value: redactValue(payload?.value, payload?.name || payload?.label || ""),
        options: redactValue(payload?.options) as UiEvidence["options"],
        visibleOptions: redactValue(payload?.visibleOptions) as string[],
        form: redactValue(payload?.form) as UiEvidence["form"]
      };
      active.lastUiByPage.set(page, event);
      active.recentUi = [...active.recentUi, event].slice(-40);
      await appendJsonl(active.eventsFile, event);
    });

    await context.addInitScript({ content: UI_RECORDER_SCRIPT });

    const attachPage = async (page: Page) => {
      try {
        await page.evaluate(UI_RECORDER_SCRIPT);
      } catch {
        // about:blank or cross-origin navigation may race; addInitScript covers next document.
      }
    };

    for (const page of context.pages()) await attachPage(page);
    context.on("page", page => void attachPage(page));

    context.on("request", request => {
      if (!this.active) return;
      this.active.requestIds.set(request, id("net"));
    });

    context.on("requestfailed", request => {
      void this.captureFailedRequest(request);
    });

    context.on("response", response => {
      void this.captureResponse(response);
    });
  }

  private shouldCapture(request: Request) {
    return ["xhr", "fetch", "document"].includes(request.resourceType());
  }

  private queryOf(url: string) {
    const parsed = new URL(url);
    const result: Record<string, string | string[]> = {};
    for (const [key, value] of parsed.searchParams) {
      const previous = result[key];
      if (previous === undefined) result[key] = value;
      else if (Array.isArray(previous)) previous.push(value);
      else result[key] = [previous, value];
    }
    return result;
  }

  private pageFor(request: Request): Page | undefined {
    try {
      return request.frame().page();
    } catch {
      return undefined;
    }
  }

  private async requestPart(request: Request) {
    const postData = request.postData();
    const rawHeaders = await request.allHeaders();
    const contentType = rawHeaders["content-type"] || "";
    let body: unknown;
    if (postData && /application\/x-www-form-urlencoded/i.test(contentType)) {
      const params = new URLSearchParams(postData);
      const form: Record<string, string | string[]> = {};
      for (const [key, value] of params) {
        const previous = form[key];
        if (previous === undefined) form[key] = value;
        else if (Array.isArray(previous)) previous.push(value);
        else form[key] = [previous, value];
      }
      body = redactValue(form);
    } else if (postData && /multipart\/form-data/i.test(contentType)) {
      const fieldNames = [...postData.matchAll(/name="([^"]+)"/g)].map(match => match[1]!).filter(Boolean);
      body = Object.fromEntries([...new Set(fieldNames)].map(name => [name, "[MULTIPART_VALUE_NOT_CAPTURED]"]));
    } else if (postData && /json|graphql/i.test(contentType)) {
      body = parsePossiblyJson(postData);
    } else if (postData) {
      const parsed = parsePossiblyJson(postData);
      body = parsed && typeof parsed === "object" ? parsed : undefined;
    }
    return {
      method: request.method(),
      url: request.url(),
      resourceType: request.resourceType(),
      headers: redactHeaders(rawHeaders),
      query: redactValue(this.queryOf(request.url())) as Record<string, string | string[]>,
      body: redactValue(body)
    };
  }

  private async captureFailedRequest(request: Request) {
    const active = this.active;
    if (!active || !this.shouldCapture(request)) return;
    const page = this.pageFor(request);
    const ui = this.recentUi(page, active);
    const event: NetworkEvidence = {
      id: active.requestIds.get(request) || id("net"),
      kind: "network",
      sessionId: active.session.id,
      at: new Date().toISOString(),
      pageUrl: page?.url(),
      correlatedUiEvidenceId: ui?.id,
      request: await this.requestPart(request),
      failure: request.failure()?.errorText || "request failed"
    };
    await appendJsonl(active.eventsFile, event);
  }

  private async captureResponse(response: Response) {
    const active = this.active;
    if (!active) return;
    const request = response.request();
    if (!this.shouldCapture(request)) return;

    const page = this.pageFor(request);
    const ui = this.recentUi(page, active);

    let body: unknown;
    let truncated = false;
    const headers = redactHeaders(await response.allHeaders());
    const contentType = headers["content-type"] || "";
    const contentLength = Number(headers["content-length"] || "0");

    if (/json|text|javascript|xml|graphql/i.test(contentType)) {
      try {
        if (!contentLength || contentLength <= this.config.maxResponseBytes) {
          const buffer = await response.body();
          if (buffer.byteLength <= this.config.maxResponseBytes) {
            body = parsePossiblyJson(buffer.toString("utf8"));
          } else {
            truncated = true;
          }
        } else {
          truncated = true;
        }
      } catch {
        // Some streaming or opaque responses cannot be read safely.
      }
    }

    const event: NetworkEvidence = {
      id: active.requestIds.get(request) || id("net"),
      kind: "network",
      sessionId: active.session.id,
      at: new Date().toISOString(),
      pageUrl: page?.url(),
      correlatedUiEvidenceId: ui?.id,
      request: await this.requestPart(request),
      response: {
        status: response.status(),
        headers,
        body: redactValue(body),
        truncated
      }
    };
    await appendJsonl(active.eventsFile, event);
  }

  private recentUi(page?: Page, active = this.active) {
    if (!active || !page) return undefined;
    const ui = active.lastUiByPage.get(page);
    if (!ui) return undefined;
    return Date.now() - Date.parse(ui.at) <= 8_000 ? ui : undefined;
  }

  private currentPage(): Page {
    if (!this.active) throw new Error("No active recording/browser session");
    const pages = this.active.context.pages().filter(p => !p.isClosed());
    if (!pages.length) throw new Error("No active browser page");
    return pages[pages.length - 1]!;
  }

  async inspectTarget(selector: string) {
    const locator = await this.locate(selector);
    return locator.evaluate(INSPECT_TARGET_IN_PAGE);
  }

  private locatorIn(frame: Frame, selector: string) {
    const placeholder = selector.match(/^placeholder=(.+)$/s);
    if (placeholder) return frame.getByPlaceholder(placeholder[1]!);
    const label = selector.match(/^label=(.+)$/s);
    if (label) return frame.getByLabel(label[1]!);
    const text = selector.match(/^text=(.+)$/s);
    if (text) return frame.getByText(text[1]!, { exact: true });
    const role = selector.match(/^role=([a-z]+)(?:\[name=["'](.+)["']\])?$/i);
    if (role) return frame.getByRole(role[1] as "button", role[2] ? { name: role[2] } : {});
    return frame.locator(selector);
  }

  private async locate(selector: string) {
    const page = this.currentPage();
    for (const frame of page.frames()) {
      const locator = this.locatorIn(frame, selector).first();
      if (await locator.count()) return locator;
    }
    throw new Error(`Selector not found in the page or its frames: ${selector}`);
  }

  private recentUserActions() {
    return (this.active?.recentUi || []).slice(-20).map(event => ({
      at: event.at,
      eventType: event.eventType,
      name: event.name,
      label: event.label,
      text: event.text,
      selector: event.selector,
      value: event.value
    }));
  }

  async manualControl(command:
    | { action: "click"; x: number; y: number; button?: "left" | "right" | "middle"; clickCount?: number }
    | { action: "text"; value: string }
    | { action: "key"; key: string }
    | { action: "scroll"; deltaX?: number; deltaY?: number }
  ) {
    const page = this.currentPage();
    if (command.action === "click") {
      const viewport = page.viewportSize();
      if (!viewport || !Number.isFinite(command.x) || !Number.isFinite(command.y)) throw new Error("manual click requires finite x and y coordinates");
      if (command.x < 0 || command.y < 0 || command.x > viewport.width || command.y > viewport.height) {
        throw new Error("manual click coordinates are outside the embedded browser viewport");
      }
      await page.mouse.click(command.x, command.y, {
        button: command.button || "left",
        clickCount: Math.max(1, Math.min(Number(command.clickCount) || 1, 2))
      });
    } else if (command.action === "text") {
      if (typeof command.value !== "string" || command.value.length > 10_000) throw new Error("manual text requires a string no longer than 10000 characters");
      await page.keyboard.insertText(command.value);
    } else if (command.action === "key") {
      if (typeof command.key !== "string" || !command.key || command.key.length > 80) throw new Error("manual key requires a valid key name");
      await page.keyboard.press(command.key);
    } else {
      const deltaX = Math.max(-5_000, Math.min(Number(command.deltaX) || 0, 5_000));
      const deltaY = Math.max(-5_000, Math.min(Number(command.deltaY) || 0, 5_000));
      await page.mouse.wheel(deltaX, deltaY);
    }
    return { ok: true, url: page.url(), title: await page.title().catch(() => "") };
  }

  async control(command: {
    action: "goto" | "snapshot" | "click" | "fill" | "select" | "press" | "wait" | "screenshot";
    selector?: string;
    value?: string | string[];
    url?: string;
    key?: string;
    ms?: number;
  }): Promise<unknown> {
    const page = this.currentPage();
    switch (command.action) {
      case "goto":
        if (!command.url) throw new Error("goto requires url");
        await page.goto(command.url, { waitUntil: "domcontentloaded" });
        return { url: page.url(), title: await page.title() };
      case "click":
        if (!command.selector) throw new Error("click requires selector");
        await (await this.locate(command.selector)).click();
        return { ok: true, url: page.url() };
      case "fill":
        if (!command.selector || typeof command.value !== "string") throw new Error("fill requires selector and string value");
        await (await this.locate(command.selector)).fill(command.value);
        return { ok: true };
      case "select":
        if (!command.selector || command.value === undefined) throw new Error("select requires selector and value");
        await (await this.locate(command.selector)).selectOption(command.value);
        return { ok: true };
      case "press":
        if (!command.selector || !command.key) throw new Error("press requires selector and key");
        await (await this.locate(command.selector)).press(command.key);
        return { ok: true };
      case "wait":
        await page.waitForTimeout(Math.max(0, Math.min(command.ms || 500, 30_000)));
        return { ok: true };
      case "screenshot": {
        const dir = path.join(this.config.dataDir, "screenshots");
        await ensureDir(dir);
        const file = path.join(dir, `${Date.now()}.png`);
        await page.screenshot({ path: file, fullPage: true });
        return { file, url: page.url() };
      }
      case "snapshot": {
        const frames = [];
        for (const frame of page.frames()) {
          try {
            frames.push({ frameUrl: frame.url(), ...(await frame.locator("body").evaluate(SNAPSHOT_IN_PAGE) as any) });
          } catch {
            frames.push({ frameUrl: frame.url(), unavailable: true });
          }
        }
        return { ...frames[0], frames: frames.slice(1), recentUserActions: this.recentUserActions() };
      }
    }
  }

  disposeImmediate() {
    const active = this.active;
    this.active = undefined;
    if (!active) return;
    void active.context.close().catch(() => {});
    void active.browser?.close().catch(() => {});
  }

  async stop(): Promise<RecordingSession> {
    if (!this.active) throw new Error("No active recording");
    const active = this.active;
    this.active = undefined;

    active.session.stoppedAt = new Date().toISOString();
    const dir = path.dirname(active.eventsFile);
    await writeJson(path.join(dir, "session.json"), active.session);

    if (!active.externalBrowser) {
      await active.context.close().catch(() => {});
      await active.browser?.close().catch(() => {});
    }
    return active.session;
  }
}
