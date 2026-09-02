import path from "node:path";
import { chromium, type BrowserContext, type Browser, type Frame, type Locator, type Request, type Response, type Page } from "playwright";
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
  private actionBusy = 0;
  private previewInFlight?: Promise<Buffer>;
  private lastPreview?: { at: number; buffer: Buffer };

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
    if (this.lastPreview && (this.actionBusy > 0 || Date.now() - this.lastPreview.at < 220)) {
      return this.lastPreview.buffer;
    }
    if (this.previewInFlight) return this.previewInFlight;
    this.previewInFlight = this.capturePreview().finally(() => {
      this.previewInFlight = undefined;
    });
    return this.previewInFlight;
  }

  private async capturePreview() {
    const page = this.currentPage();
    const buffer = await page.screenshot({
      type: "jpeg",
      quality: 42,
      fullPage: false,
      animations: "disabled",
      caret: "hide"
    });
    this.lastPreview = { at: Date.now(), buffer };
    return buffer;
  }

  private async withAction<T>(work: () => Promise<T>): Promise<T> {
    this.actionBusy += 1;
    try {
      return await work();
    } finally {
      this.actionBusy -= 1;
    }
  }

  private async waitForPageQuiet(page: Page, timeout = 1_800) {
    await page.waitForTimeout(80);
    const busy = page.locator(".el-loading-mask, .el-overlay.is-loading, .nprogress-busy, .ant-spin-spinning").first();
    await busy.waitFor({ state: "hidden", timeout }).catch(() => {});
  }

  async reload() {
    return this.withAction(async () => {
      const page = this.currentPage();
      await page.reload({ waitUntil: "domcontentloaded" });
      await this.waitForPageQuiet(page);
      return { url: page.url(), title: await page.title() };
    });
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
    const armPage = (target: Page) => {
      target.setDefaultTimeout(5_000);
      target.setDefaultNavigationTimeout(15_000);
    };
    for (const existing of context.pages()) armPage(existing);
    context.on("page", armPage);

    const page = context.pages()[0] || await context.newPage();
    await page.goto(startUrl, { waitUntil: "domcontentloaded" });
    await this.waitForPageQuiet(page);
    return session;
  }

  private async instrument(context: BrowserContext) {
    await context.exposeBinding("__bssRecordUi", async ({ page }, payload: any) => {
      await this.writeUiEvent(page, payload);
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

  private async writeUiEvent(page: Page | undefined, payload: any) {
    const active = this.active;
    if (!active || !page) return undefined;
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
    const last = active.recentUi.at(-1);
    if (last && last.eventType === event.eventType && last.name === event.name && last.label === event.label && Object.is(last.value, event.value)
      && Date.parse(event.at) - Date.parse(last.at) < 800) {
      last.at = event.at;
      active.lastUiByPage.set(page, last);
      return last;
    }
    active.lastUiByPage.set(page, event);
    active.recentUi = [...active.recentUi, event].slice(-40);
    await appendJsonl(active.eventsFile, event);
    return event;
  }

  private async captureActiveControl(eventType: UiEvidence["eventType"]) {
    const page = this.currentPage();
    await page.evaluate(`(() => {
      const el = document.activeElement;
      if (!(el instanceof HTMLElement) || el === document.body) return;
      el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      if (typeof window.__bssFlushUi === "function") window.__bssFlushUi(${JSON.stringify(eventType)}, el);
    })()`).catch(() => {});
    await page.waitForTimeout(40);
    return this.active?.recentUi.at(-1);
  }

  private recentUi(page?: Page, active = this.active) {
    if (!active || !page) return undefined;
    const ui = active.lastUiByPage.get(page);
    if (!ui) return undefined;
    return Date.now() - Date.parse(ui.at) <= 30_000 ? ui : undefined;
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

  private locatorIn(root: Frame | Locator, selector: string) {
    const placeholder = selector.match(/^placeholder=(.+)$/s);
    if (placeholder) return root.getByPlaceholder(placeholder[1]!);
    const label = selector.match(/^label=(.+)$/s);
    if (label) return root.getByLabel(label[1]!);
    const text = selector.match(/^text=(.+)$/s);
    if (text) return root.getByText(text[1]!, { exact: true });
    const role = selector.match(/^role=([a-z]+)(?:\[name=["'](.+)["']\])?$/i);
    if (role) return root.getByRole(role[1] as "button", role[2] ? { name: role[2] } : {});
    return root.locator(selector);
  }

  private isDayTextSelector(selector: string) {
    const text = selector.match(/^text=(.+)$/s)?.[1]?.trim();
    return Boolean(text && /^\d{1,2}$/.test(text));
  }

  private isDateCellSelector(selector: string) {
    return /el-date-picker|el-date-table|el-picker-panel|available\.today|date-table-cell/.test(selector) || this.isDayTextSelector(selector);
  }

  private async locate(selector: string) {
    const page = this.currentPage();
    const deadline = Date.now() + 4_000;
    const dayOnly = this.isDayTextSelector(selector);
    while (Date.now() < deadline) {
      for (const frame of page.frames()) {
        const popper = frame.locator(".el-picker-panel:visible, .el-select-dropdown:visible, .el-popper:visible, [role='listbox']:visible").last();
        const dialog = frame.locator(".el-dialog:visible, [role='dialog']:visible, .ant-modal:visible, .arco-modal:visible").last();
        const scopes: Array<Frame | Locator> = [];
        if (await popper.count()) scopes.push(popper);
        if (await dialog.count()) scopes.push(dialog);
        if (!dayOnly) scopes.push(frame);
        for (const scope of scopes) {
          const locator = this.locatorIn(scope, selector).first();
          if (await locator.count()) return locator;
        }
      }
      await page.waitForTimeout(80);
    }
    throw new Error(`Selector not found in the page or its frames: ${selector}`);
  }

  private async clickTarget(selector: string) {
    const locator = await this.locate(selector);
    const wrapper = locator.locator("xpath=ancestor-or-self::*[contains(@class,'el-select') or contains(@class,'el-date-editor') or contains(@class,'el-cascader')][1]");
    if (await wrapper.count()) return wrapper.first();
    return locator;
  }

  private async clickSafely(locator: Locator) {
    const kind = await locator.first().evaluate(el => {
      const safe = el.closest(".el-dialog,.el-picker-panel,.el-select-dropdown,.el-popper,.el-date-picker,.el-select,.el-date-editor,.ant-modal,.arco-modal,[role='dialog'],[role='listbox'],[role='option']");
      const mask = el.closest(".el-overlay,.el-overlay-dialog,.v-modal,.ant-modal-mask,.arco-modal-mask");
      if (mask && !safe) return "mask";
      const box = el.getBoundingClientRect();
      const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      if (top && top !== el && !el.contains(top) && !top.contains(el)) {
        const coveredByMask = top.closest(".el-overlay,.el-overlay-dialog") && !top.closest(".el-dialog,.el-picker-panel,.el-select-dropdown,.el-popper,[role='option']");
        if (coveredByMask) return "occluded";
      }
      return "ok";
    }).catch(() => "ok");
    if (kind === "mask") throw new Error("Refusing to click the modal mask; that would close the dialog");
    if (kind === "occluded") throw new Error("Target is behind an open dialog; click a control inside the dialog or picker");
    await locator.first().click({ force: true, timeout: 4_000 });
  }

  private async pickCalendarDay(dayText: string) {
    const page = this.currentPage();
    const panel = page.locator(".el-picker-panel:visible, .el-date-picker:visible, .el-date-range-picker:visible").last();
    if (!(await panel.count())) throw new Error("No visible date picker");
    const day = String(Number(dayText));
    const cell = panel.locator(".el-date-table-cell__text, .el-date-table-cell, td.available .cell, td.available").filter({ hasText: new RegExp(`^\\s*${day}\\s*$`) }).last();
    await this.clickSafely(cell);
  }

  private async fillField(selector: string, value: string) {
    const locator = await this.locate(selector);
    const dateWrap = locator.locator("xpath=ancestor-or-self::*[contains(@class,'el-date-editor') or contains(@class,'el-date-picker')][1]");
    const input = ((await dateWrap.count()) ? dateWrap : locator).locator("input").first();
    const target = (await input.count()) ? input : locator;
    await target.fill(value, { force: true, timeout: 4_000 });
    if ((await dateWrap.count()) || /^\d{4}-\d{2}-\d{2}/.test(value)) {
      await target.press("Enter").catch(() => {});
    }
  }

  private async chooseOption(selector: string, value: string | string[]) {
    const labels = (Array.isArray(value) ? value : [value]).map(item => String(item));
    if (labels.length === 1 && /^\d{4}-\d{2}-\d{2}/.test(labels[0]!)) {
      await this.fillField(selector, labels[0]!);
      return { ok: true, url: this.currentPage().url() };
    }
    const target = await this.clickTarget(selector);
    const page = this.currentPage();
    await this.waitForPageQuiet(page, 1_200);
    for (const label of labels) {
      const option = page.getByRole("option", { name: label });
      const openAndPick = async () => {
        await this.clickSafely(target);
        await option.first().waitFor({ state: "visible", timeout: 2_500 });
        await this.clickSafely(option.first());
      };
      try {
        await openAndPick();
      } catch {
        await this.clickSafely(target);
        await option.first().waitFor({ state: "visible", timeout: 2_500 });
        await this.clickSafely(option.first());
      }
    }
    await this.waitForPageQuiet(page, 1_200);
    return { ok: true, url: page.url() };
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
    return this.withAction(async () => {
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
      const observed = command.action === "scroll"
        ? undefined
        : await this.captureActiveControl(command.action === "text" ? "input" : command.action === "key" ? "change" : "click");
      return {
        ok: true,
        url: page.url(),
        title: await page.title().catch(() => ""),
        observed: observed ? { eventType: observed.eventType, label: observed.label, name: observed.name, value: observed.value, selector: observed.selector } : undefined
      };
    });
  }

  async control(command: {
    action: "goto" | "snapshot" | "click" | "fill" | "select" | "choose" | "press" | "wait" | "screenshot";
    selector?: string;
    value?: string | string[];
    url?: string;
    key?: string;
    ms?: number;
  }): Promise<unknown> {
    return this.withAction(async () => {
      const page = this.currentPage();
      switch (command.action) {
        case "goto":
          if (!command.url) throw new Error("goto requires url");
          await page.goto(command.url, { waitUntil: "domcontentloaded" });
          await this.waitForPageQuiet(page);
          return { url: page.url(), title: await page.title() };
        case "click":
          if (!command.selector) throw new Error("click requires selector");
          if (this.isDateCellSelector(command.selector)) {
            const day = command.selector.match(/(\d{1,2})/)?.[1] || (command.selector.includes("today") ? String(new Date().getDate()) : "");
            if (day) {
              await this.pickCalendarDay(day);
              await this.waitForPageQuiet(page);
              return { ok: true, url: page.url() };
            }
          }
          await this.clickSafely(await this.clickTarget(command.selector));
          await this.waitForPageQuiet(page);
          return { ok: true, url: page.url() };
        case "fill":
          if (!command.selector || typeof command.value !== "string") throw new Error("fill requires selector and string value");
          await this.fillField(command.selector, command.value);
          return { ok: true };
        case "choose":
          if (!command.selector || command.value === undefined) throw new Error("choose requires selector and value");
          return this.chooseOption(command.selector, command.value);
        case "select":
          if (!command.selector || command.value === undefined) throw new Error("select requires selector and value");
          try {
            await (await this.locate(command.selector)).selectOption(command.value, { timeout: 1_500 });
            return { ok: true };
          } catch {
            return this.chooseOption(command.selector, command.value);
          }
        case "press":
          if (!command.selector || !command.key) throw new Error("press requires selector and key");
          await (await this.locate(command.selector)).press(command.key, { timeout: 4_000 });
          return { ok: true };
        case "wait":
          await page.waitForTimeout(Math.max(0, Math.min(command.ms || 500, 8_000)));
          return { ok: true };
        case "screenshot": {
          const dir = path.join(this.config.dataDir, "screenshots");
          await ensureDir(dir);
          const file = path.join(dir, `${Date.now()}.jpg`);
          await page.screenshot({ path: file, type: "jpeg", quality: 55, fullPage: false, animations: "disabled" });
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
    });
  }

  disposeImmediate() {
    const active = this.active;
    this.active = undefined;
    this.lastPreview = undefined;
    this.previewInFlight = undefined;
    this.actionBusy = 0;
    if (!active) return;
    void active.context.close().catch(() => {});
    void active.browser?.close().catch(() => {});
  }

  async stop(): Promise<RecordingSession> {
    if (!this.active) throw new Error("No active recording");
    const active = this.active;
    this.active = undefined;
    this.lastPreview = undefined;
    this.previewInFlight = undefined;
    this.actionBusy = 0;

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
