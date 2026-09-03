import path from "node:path";
import { writeFile } from "node:fs/promises";
import { chromium, type BrowserContext, type Browser, type Request, type Response, type Page } from "playwright";
import type { EvidenceEvent, NetworkEvidence, RecordingSession, UiEvidence } from "../domain.js";
import type { StudioConfig } from "../config.js";
import { appendJsonl, ensureDir, id, writeJson } from "../utils.js";
import { parsePossiblyJson, redactHeaders, redactValue } from "../security/redact.js";
import { UI_RECORDER_SCRIPT } from "./page-script.js";
import { PageActions, type PageSnapshot } from "./page-actions.js";
import { buildManualSteps, renderManualStepsMarkdown, type ManualStep } from "../record/manual-steps.js";

const FORM_ACTION_BUDGET = 3;

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

interface ActionGuard {
  exerciseFormCount: number;
  submitFormCount: number;
  failedKeys: string[];
  followManualSteps: boolean;
}

interface ActiveRecording {
  session: RecordingSession;
  browser?: Browser;
  context: BrowserContext;
  eventsFile: string;
  requestIds: WeakMap<Request, string>;
  lastUiByPage: WeakMap<Page, UiEvidence>;
  recentUi: UiEvidence[];
  manualEvents: UiEvidence[];
  externalBrowser: boolean;
  guard: ActionGuard;
}

const EMPTY_JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQAAAQABAAD/2wAAAAF/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9k=",
  "base64"
);

export class BrowserRecorder {
  private active?: ActiveRecording;
  private actionBusy = 0;
  private readonly networkJobs = new Set<Promise<void>>();
  private previewInFlight?: Promise<Buffer>;
  private lastPreview?: { at: number; buffer: Buffer };
  private focused?: Page;
  private pageError?: string;
  private lastGoodUrl?: string;
  private lastTitle = { at: 0, value: "" };
  private layerHotUntil = 0;
  private layerWatch?: Promise<void>;
  private inventoryTimer?: ReturnType<typeof setTimeout>;
  private readonly actions = new PageActions({
    page: () => this.currentPage(),
    writePageInventory: (page, snapshot) => this.writePageInventory(page, snapshot),
    recentUserActions: () => this.recentUserActions(),
    recordedManualSteps: () => this.recordedManualSteps(),
    followManualSteps: () => Boolean(this.active?.guard.followManualSteps),
    recordSelectObservation: info => this.writeUiEvent(this.currentPage(), {
      eventType: "change",
      label: info.label,
      name: info.name,
      scope: info.scope,
      value: info.value,
      inputType: "select",
      options: info.options,
      visibleOptions: info.options.map(item => item.label)
    })
  });

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
    const pages = this.livePages().map(item => ({
      url: item.url(),
      current: item === page
    }));
    let title = this.lastTitle.value;
    if (Date.now() - this.lastTitle.at > 900) {
      title = await this.withTimeout(page.title(), 400, this.lastTitle.value);
      this.lastTitle = { at: Date.now(), value: title };
    }
    return {
      active: true as const,
      session: this.active.session,
      url: page.url(),
      title,
      viewport: page.viewportSize(),
      pageError: this.pageError,
      pages
    };
  }

  async preview(): Promise<Buffer> {
    const layerHot = this.layerHotUntil > Date.now();
    if (this.lastPreview && !layerHot && (this.actionBusy > 0 || Date.now() - this.lastPreview.at < 180)) {
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
    if (this.isErrorUrl(page.url())) {
      this.pageError = this.pageError || `页面打开失败：${page.url()}`;
      return this.lastPreview?.buffer || EMPTY_JPEG;
    }
    const buffer = await this.withTimeout<Buffer | undefined>(page.screenshot({
      type: "jpeg",
      quality: 48,
      fullPage: false,
      animations: "allow",
      caret: "hide"
    }), 900, undefined);
    if (!buffer || buffer.length < 800) return this.lastPreview?.buffer || EMPTY_JPEG;
    this.lastPreview = { at: Date.now(), buffer };
    this.lastGoodUrl = page.url();
    return buffer;
  }

  private setFocused(page?: Page) {
    this.focused = page;
  }

  private async withAction<T>(work: () => Promise<T>): Promise<T> {
    this.actionBusy += 1;
    try {
      return await work();
    } finally {
      this.actionBusy -= 1;
      this.lastPreview = undefined;
    }
  }

  private async waitForPageQuiet(page: Page, timeout = 800) {
    void page;
    await this.actions.waitForPageQuiet(timeout);
  }

  async reload() {
    return this.withAction(async () => {
      const page = this.currentPage();
      try {
        await page.reload({ waitUntil: "domcontentloaded" });
        await this.waitForPageQuiet(page);
        if (await this.pageLooksFailed(page)) this.pageError = `页面刷新失败：${page.url()}`;
        else {
          this.pageError = undefined;
          this.lastGoodUrl = page.url();
        }
      } catch {
        this.pageError = `页面刷新失败：${page.url()}`;
      }
      return { url: this.currentPage().url(), title: await this.withTimeout(this.currentPage().title(), 1_200, ""), pageError: this.pageError };
    });
  }

  async start(startUrl: string, name = "recording"): Promise<RecordingSession> {
    if (this.active) throw new Error(`Recording already active: ${this.active.session.id}`);

    const sessionId = id("rec");
    const dir = path.join(this.config.recordingsDir, sessionId);
    await ensureDir(dir);
    await ensureDir(this.config.profileDir);

    const launchOptions = {
      headless: this.config.headless,
      viewport: { width: 1440, height: 960 },
      deviceScaleFactor: 1,
      args: ["--disable-features=TranslateUI,IsolateOrigins,site-per-process", "--disable-background-timer-throttling", "--disable-site-isolation-trials"]
    };
    const context = await chromium.launchPersistentContext(this.config.profileDir, launchOptions).catch(error => {
      if (!/Executable doesn't exist/i.test(String(error))) throw error;
      return chromium.launchPersistentContext(this.config.profileDir, { ...launchOptions, channel: "chrome" });
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
      manualEvents: [],
      externalBrowser: false,
      guard: { exerciseFormCount: 0, submitFormCount: 0, failedKeys: [], followManualSteps: false }
    };

    await writeJson(path.join(dir, "session.json"), session);
    await this.instrument(context);
    const armPage = (target: Page) => this.armPage(target);
    for (const existing of context.pages()) armPage(existing);
    context.on("page", armPage);

    const page = context.pages()[0] || await context.newPage();
    this.setFocused(page);
    try {
      await page.goto(startUrl, { waitUntil: "domcontentloaded" });
      await this.waitForPageQuiet(page);
      if (await this.pageLooksFailed(page)) {
        this.pageError = `无法打开页面：${startUrl}`;
      } else {
        this.lastGoodUrl = page.url();
        this.pageError = undefined;
      }
    } catch {
      this.pageError = `无法打开页面：${startUrl}`;
    }
    return session;
  }

  private armPage(target: Page) {
    target.setDefaultTimeout(5_000);
    target.setDefaultNavigationTimeout(15_000);
    target.on("crash", () => {
      if (this.focused === target) this.setFocused(undefined);
      this.pageError = "页面已崩溃，已停在上一页";
    });
    target.on("close", () => {
      if (this.focused === target) this.setFocused(undefined);
    });
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
      this.trackNetwork(this.captureFailedRequest(request));
    });

    context.on("response", response => {
      this.trackNetwork(this.captureResponse(response));
    });
  }

  private trackNetwork(job: Promise<void>) {
    const tracked = job.catch(() => {});
    this.networkJobs.add(tracked);
    void tracked.finally(() => this.networkJobs.delete(tracked));
  }

  private async drainNetwork(timeout = 1_500) {
    const deadline = Date.now() + timeout;
    while (this.networkJobs.size && Date.now() < deadline) {
      await Promise.race([...this.networkJobs, new Promise(resolve => setTimeout(resolve, 40))]);
    }
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
    const rawHeaders = await request.allHeaders().catch(() => ({} as Record<string, string>));
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
    const headers = redactHeaders(await response.allHeaders().catch(() => ({})));
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

  private stabilizeUiEvent(event: UiEvidence): UiEvidence {
    const value = event.value === undefined || event.value === null ? "" : String(event.value);
    const cssy = (text?: string) => Boolean(text && ((/^[a-z]+\./i.test(text) && text.includes(">")) || /(?:arco-|el-|ant-)[\w-]*\./.test(text)));
    const polluted = Boolean(event.label && value && event.label === value) || event.label === "字段" || cssy(event.label);
    if (!polluted && !cssy(event.selector)) return event;
    const hit = (event.form || []).find(field =>
      field.label
      && field.label !== value
      && field.label !== "字段"
      && !cssy(field.label)
      && (field.value === event.value || field.name === event.name)
    ) || (event.form || []).find(field => field.label && field.label !== value && field.label !== "字段" && !cssy(field.label) && field.value !== undefined && field.value !== "");
    if (!hit?.label) return event;
    return {
      ...event,
      label: hit.label,
      selector: event.selector && !cssy(event.selector) ? event.selector : `label=${hit.label}`
    };
  }

  private async writeUiEvent(page: Page | undefined, payload: any) {
    const active = this.active;
    if (!active || !page) return undefined;
    const event = this.stabilizeUiEvent({
      id: id("ui"),
      kind: "ui",
      sessionId: active.session.id,
      at: new Date().toISOString(),
      pageUrl: String(payload?.pageUrl || page.url()),
      eventType: ["click", "input", "change", "submit", "snapshot"].includes(payload?.eventType) ? payload.eventType : "click",
      selector: payload?.selector,
      tag: payload?.tag,
      role: payload?.role,
      text: payload?.text,
      label: payload?.label,
      name: payload?.name,
      inputType: payload?.inputType,
      scope: payload?.scope === "dialog" || payload?.scope === "page" ? payload.scope : undefined,
      value: redactValue(payload?.value, payload?.name || payload?.label || ""),
      options: redactValue(payload?.options) as UiEvidence["options"],
      visibleOptions: redactValue(payload?.visibleOptions) as string[],
      form: redactValue(payload?.form) as UiEvidence["form"]
    });
    const last = active.recentUi.at(-1);
    if (last && last.eventType === event.eventType && last.name === event.name && last.label === event.label && Object.is(last.value, event.value)
      && Date.parse(event.at) - Date.parse(last.at) < 800) {
      last.at = event.at;
      active.lastUiByPage.set(page, last);
      return last;
    }
    active.lastUiByPage.set(page, event);
    active.recentUi = [...active.recentUi, event].slice(-40);
    if (event.eventType !== "snapshot") {
      active.manualEvents = [...active.manualEvents, event].slice(-200);
    }
    await appendJsonl(active.eventsFile, event);
    return event;
  }

  private async writePageInventory(page: Page, snapshot: PageSnapshot) {
    const fromFields = (snapshot.formFields || []).map(field => ({
      name: typeof field.name === "string" ? field.name : undefined,
      label: typeof field.label === "string" ? field.label : undefined,
      type: String(field.kind || field.type || "text"),
      value: field.value,
      required: Boolean(field.required),
      options: Array.isArray((field as { options?: unknown }).options) ? (field as { options: UiEvidence["options"] }).options : undefined,
      rangeIndex: typeof (field as { rangeIndex?: unknown }).rangeIndex === "number" ? (field as { rangeIndex: number }).rangeIndex : undefined
    })).filter(field => field.label || field.name);
    const form = fromFields.length ? fromFields : (snapshot.controls || []).flatMap(control => {
      const role = String(control.role || "");
      const tag = String(control.tag || "");
      const type = String(control.type || "");
      const label = String(control.label || "");
      const name = typeof control.name === "string" ? control.name : undefined;
      if (!label && !name) return [];
      if (!/input|select|textarea|combobox|checkbox|switch|radio/i.test(`${tag} ${role} ${type}`)) return [];
      return [{
        name,
        label: label || undefined,
        type: /combobox|select/i.test(`${role} ${tag}`) ? "select" : (type || tag || "text"),
        value: control.value,
        required: Boolean(control.required)
      }];
    });
    if (!form.length) return;
    await this.writeUiEvent(page, {
      eventType: "snapshot",
      pageUrl: snapshot.url || page.url(),
      text: snapshot.scope,
      scope: snapshot.scope === "dialog" || snapshot.scope === "page" ? snapshot.scope : undefined,
      form
    });
  }

  private async captureActiveControl(eventType: UiEvidence["eventType"], page = this.currentPage()) {
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

  private livePages() {
    return this.active?.context.pages().filter(page => !page.isClosed()) || [];
  }

  private isErrorUrl(url: string) {
    return /^(chrome-error:|chrome:\/\/|about:neterror)/i.test(url) || /chromewebdata/i.test(url);
  }

  private isTransientUrl(url: string) {
    return !url || url === "about:blank" || url === "about:srcdoc";
  }

  private async withTimeout<T>(work: Promise<T>, ms: number, fallback: T): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        work,
        new Promise<T>((_, reject) => {
          timer = setTimeout(() => reject(new Error("timeout")), ms);
        })
      ]);
    } catch {
      return fallback;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  private async pageLooksFailed(page: Page) {
    if (page.isClosed()) return true;
    if (this.isErrorUrl(page.url())) return true;
    const info = await this.withTimeout(
      page.evaluate(() => ({
        href: location.href,
        title: document.title,
        body: (document.body?.innerText || "").slice(0, 240),
        errorDom: Boolean(document.querySelector("#main-frame-error, .neterror, #crash-web-page"))
      })),
      1_200,
      { href: page.url(), title: "", body: "", errorDom: false }
    );
    return this.isErrorUrl(info.href)
      || info.errorDom
      || /ERR_|无法访问此网站|This site can’t be reached|This page isn’t working|chrome-error|chromewebdata/i.test(`${info.title}\n${info.body}\n${info.href}`);
  }

  private async adoptIfReady(page: Page) {
    if (page.isClosed()) return false;
    await this.withTimeout(page.waitForLoadState("domcontentloaded"), 2_000, undefined);
    if (this.isTransientUrl(page.url())) {
      await this.withTimeout(page.waitForURL(url => !this.isTransientUrl(url), { timeout: 2_000 }), 2_000, undefined);
      await this.withTimeout(page.waitForLoadState("domcontentloaded"), 2_000, undefined);
    }
    if (page.isClosed() || this.isTransientUrl(page.url()) || await this.pageLooksFailed(page)) {
      this.pageError = `新页面打开失败${page.isClosed() || this.isTransientUrl(page.url()) ? "" : `：${page.url()}`}`;
      if (this.livePages().length > 1) await page.close().catch(() => {});
      return false;
    }
    this.setFocused(page);
    this.lastGoodUrl = page.url();
    this.pageError = undefined;
    await page.bringToFront().catch(() => {});
    return true;
  }

  private watchNewPages() {
    const appeared: Page[] = [];
    const onPage = (page: Page) => {
      appeared.push(page);
    };
    this.active?.context.on("page", onPage);
    return {
      appeared,
      stop: () => this.active?.context.off("page", onPage)
    };
  }

  private watchLayerPaint() {
    this.layerHotUntil = Date.now() + 2_800;
    this.lastPreview = undefined;
    if (this.layerWatch) return;
    this.layerWatch = (async () => {
      const started = Date.now();
      while (Date.now() - started < 2_400 && this.isActive()) {
        await this.actions.nudgeOverlayFrames();
        const page = this.currentPage();
        for (const frame of page.frames()) {
          if (frame === page.mainFrame()) continue;
          await frame.waitForLoadState("domcontentloaded").catch(() => {});
        }
        this.lastPreview = undefined;
        await new Promise(resolve => setTimeout(resolve, 160));
      }
    })().catch(() => {}).finally(() => {
      this.layerWatch = undefined;
      this.lastPreview = undefined;
    });
  }

  private scheduleInventory() {
    clearTimeout(this.inventoryTimer);
    this.inventoryTimer = setTimeout(() => {
      void this.actions.recordFormInventory().catch(() => {});
    }, 280);
  }

  private async followAfterGesture(
    origin: Page,
    known: Set<Page>,
    appeared: Page[] = []
  ) {
    const beforeUrl = origin.isClosed() ? "" : origin.url();
    const newcomers = () => {
      const live = this.livePages().filter(page => page !== origin && !known.has(page));
      return live.length ? live : appeared.filter(page => page !== origin && !known.has(page));
    };
    const settleOrigin = async () => {
      if (origin.isClosed()) return;
      if (await this.pageLooksFailed(origin)) {
        this.pageError = `页面打开失败：${origin.url()}`;
        this.setFocused(origin);
        return;
      }
      this.setFocused(origin);
      this.lastGoodUrl = origin.url();
      this.pageError = undefined;
      await this.waitForPageQuiet(origin, 400);
    };
    let switched = false;
    const takeNewPage = async (page: Page) => {
      if (page.isClosed()) {
        this.pageError = this.pageError || "新页面打开失败";
        if (!origin.isClosed()) this.setFocused(origin);
        return false;
      }
      if (await this.adoptIfReady(page)) {
        switched = true;
        return true;
      }
      if (!origin.isClosed()) this.setFocused(origin);
      return false;
    };

    try {
      if (!origin.isClosed() && origin.url() !== beforeUrl) {
        await settleOrigin();
        return;
      }
      let extra = newcomers();
      if (extra.length) {
        await takeNewPage(extra[extra.length - 1]!);
        return;
      }
      let waited = 0;
      const budget = appeared.length ? 400 : 120;
      while (waited <= budget) {
        if (!origin.isClosed() && origin.url() !== beforeUrl) {
          await settleOrigin();
          return;
        }
        extra = newcomers();
        if (extra.length) {
          await takeNewPage(extra[extra.length - 1]!);
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 20));
        waited += 20;
      }
      if (appeared.some(page => page !== origin && (page.isClosed() || this.isErrorUrl(page.url())))) {
        this.pageError = this.pageError || "新页面打开失败";
      }
      if (!origin.isClosed()) this.setFocused(origin);
    } finally {
      if (!switched) await this.actions.nudgeOverlayFrames();
    }
  }

  private currentPage(): Page {
    if (!this.active) throw new Error("No active recording/browser session");
    if (this.focused && !this.focused.isClosed() && !this.isErrorUrl(this.focused.url())) {
      return this.focused;
    }
    const usable = this.livePages().filter(page => !this.isErrorUrl(page.url()) && !this.isTransientUrl(page.url()));
    if (usable.length) {
      this.setFocused(usable[usable.length - 1]);
      return this.focused!;
    }
    const alive = this.livePages().filter(page => !this.isErrorUrl(page.url()));
    if (alive.length) {
      this.setFocused(alive[alive.length - 1]);
      return this.focused!;
    }
    if (!this.livePages().length) throw new Error("No active browser page");
    return this.livePages()[this.livePages().length - 1]!;
  }

  async inspectTarget(selector: string) {
    const locator = await this.actions.locate(selector);
    return locator.evaluate(INSPECT_TARGET_IN_PAGE);
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

  private recordedManualSteps(): ManualStep[] {
    return buildManualSteps(this.active?.manualEvents || []);
  }

  private async writeManualStepsFile(active: ActiveRecording) {
    const steps = buildManualSteps(active.manualEvents);
    const lastForm = [...active.recentUi].reverse().find(event => event.form?.length)?.form;
    const file = path.join(path.dirname(active.eventsFile), "manual-steps.md");
    active.session.manualStepsFile = file;
    await writeFile(file, renderManualStepsMarkdown(steps, {
      sessionId: active.session.id,
      startUrl: active.session.startUrl,
      form: lastForm
    }), "utf8");
  }

  async manualControl(command:
    | { action: "click"; x: number; y: number; button?: "left" | "right" | "middle"; clickCount?: number }
    | { action: "text"; value: string }
    | { action: "key"; key: string }
    | { action: "scroll"; deltaX?: number; deltaY?: number }
  ) {
    const result = await this.withAction(async () => {
      const page = this.currentPage();
      const known = new Set(this.livePages());
      const watch = command.action === "click" || command.action === "key" ? this.watchNewPages() : undefined;
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
        : await this.captureActiveControl(command.action === "text" ? "input" : command.action === "key" ? "change" : "click", page);
      if (command.action !== "scroll") this.scheduleInventory();
      try {
        if (command.action === "click" || command.action === "key") {
          await this.followAfterGesture(page, known, watch?.appeared || []);
        }
        const current = this.currentPage();
        return {
          ok: true,
          url: current.url(),
          title: await this.withTimeout(current.title(), 1_200, ""),
          pageError: this.pageError,
          observed: observed ? { eventType: observed.eventType, label: observed.label, name: observed.name, value: observed.value, selector: observed.selector } : undefined
        };
      } finally {
        watch?.stop();
      }
    });
    if (command.action === "click" || command.action === "key") this.watchLayerPaint();
    return result;
  }

  private actionKey(action: string, selector?: string) {
    return `${action}:${selector || ""}`;
  }

  private stopBecauseStuck(reason: string) {
    if (this.active) this.active.guard.followManualSteps = true;
    return {
      ok: false,
      stopped: true,
      followManualSteps: true,
      reason,
      recordedManualSteps: this.recordedManualSteps()
    };
  }

  private rememberFailure(action: string, selector?: string) {
    if (!this.active) return;
    this.active.guard.followManualSteps = true;
    const key = this.actionKey(action, selector);
    if (!this.active.guard.failedKeys.includes(key)) this.active.guard.failedKeys.push(key);
  }

  private alreadyFailed(action: string, selector?: string) {
    return Boolean(this.active?.guard.failedKeys.includes(this.actionKey(action, selector)));
  }

  private async guardedPageAction<T>(action: string, selector: string | undefined, work: () => Promise<T>): Promise<T | ReturnType<BrowserRecorder["stopBecauseStuck"]>> {
    if (this.alreadyFailed(action, selector)) {
      return this.stopBecauseStuck(`同一${action}已失败，禁止重试：${selector || ""}。有 recordedManualSteps 就按步骤走，否则请用户切到手动录制。`);
    }
    try {
      return await work();
    } catch (error) {
      this.rememberFailure(action, selector);
      throw error;
    }
  }

  async control(command: {
    action: "goto" | "snapshot" | "click" | "fill" | "select" | "choose" | "press" | "wait" | "screenshot" | "exercise-form" | "submit-form";
    selector?: string;
    value?: string | string[];
    url?: string;
    key?: string;
    ms?: number;
  }): Promise<unknown> {
    const result = await this.withAction(async () => {
      const page = this.currentPage();
      const known = new Set(this.livePages());
      const watch = command.action === "click" ? this.watchNewPages() : undefined;
      switch (command.action) {
        case "goto":
          if (!command.url) throw new Error("goto requires url");
          try {
            await page.goto(command.url, { waitUntil: "domcontentloaded" });
            await this.waitForPageQuiet(page);
            if (await this.pageLooksFailed(page)) {
              this.pageError = `无法打开页面：${command.url}`;
            } else {
              this.setFocused(page);
              this.lastGoodUrl = page.url();
              this.pageError = undefined;
            }
          } catch {
            this.pageError = `无法打开页面：${command.url}`;
          }
          return { url: this.currentPage().url(), title: await this.withTimeout(this.currentPage().title(), 1_200, ""), pageError: this.pageError };
        case "click": {
          if (!command.selector) throw new Error("click requires selector");
          try {
            return await this.guardedPageAction("click", command.selector, async () => {
              const clicked = await this.actions.click(command.selector!);
              await this.followAfterGesture(page, known, watch?.appeared || []);
              return { ...clicked, url: this.currentPage().url(), pageError: this.pageError };
            });
          } finally {
            watch?.stop();
          }
        }
        case "fill":
          if (!command.selector || typeof command.value !== "string") throw new Error("fill requires selector and string value");
          return this.guardedPageAction("fill", command.selector, async () => {
            await this.actions.fillField(command.selector!, command.value as string);
            await this.actions.recordFormInventory().catch(() => {});
            return { ok: true };
          });
        case "choose":
          if (!command.selector || command.value === undefined) throw new Error("choose requires selector and value");
          return this.guardedPageAction("choose", command.selector, async () => {
            const result = await this.actions.chooseOption(command.selector!, command.value!);
            await this.actions.recordFormInventory().catch(() => {});
            return result;
          });
        case "select":
          if (!command.selector || command.value === undefined) throw new Error("select requires selector and value");
          return this.guardedPageAction("select", command.selector, async () => {
            try {
              await (await this.actions.locate(command.selector!)).selectOption(command.value!, { timeout: 1_500 });
              await this.actions.recordFormInventory().catch(() => {});
              return { ok: true };
            } catch {
              const result = await this.actions.chooseOption(command.selector!, command.value!);
              await this.actions.recordFormInventory().catch(() => {});
              return result;
            }
          });
        case "press":
          if (!command.selector || !command.key) throw new Error("press requires selector and key");
          return this.guardedPageAction("press", command.selector, async () => {
            await (await this.actions.locate(command.selector!)).press(command.key!, { timeout: 4_000 });
            return { ok: true };
          });
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
        case "snapshot":
          return this.actions.captureSnapshot();
        case "exercise-form": {
          if ((this.active?.guard.exerciseFormCount || 0) >= FORM_ACTION_BUDGET) {
            return this.stopBecauseStuck(`exercise-form 已用满 ${FORM_ACTION_BUDGET} 次，禁止再循环填表。按 recordedManualSteps 或 manual-steps.md 操作，点不动就请用户切到手动录制。不要 record_stop+analyze 一次还没有成功写响应的新增/修改。`);
          }
          if (this.active) this.active.guard.exerciseFormCount += 1;
          const result = await this.actions.exerciseForm() as { ok?: boolean };
          const used = this.active?.guard.exerciseFormCount || 0;
          const stop = !result.ok && used >= FORM_ACTION_BUDGET;
          if (stop && this.active) this.active.guard.followManualSteps = true;
          return { ...result, followManualSteps: stop };
        }
        case "submit-form": {
          if ((this.active?.guard.submitFormCount || 0) >= FORM_ACTION_BUDGET) {
            return this.stopBecauseStuck(`submit-form 已用满 ${FORM_ACTION_BUDGET} 次，禁止再循环提交。按 recordedManualSteps 操作，或请用户切到手动录制。不要 record_stop+analyze 一次还没有成功写响应的新增/修改。`);
          }
          if (this.active) this.active.guard.submitFormCount += 1;
          const result = await this.actions.submitForm() as { ok?: boolean };
          const used = this.active?.guard.submitFormCount || 0;
          const stop = !result.ok && used >= FORM_ACTION_BUDGET;
          if (stop && this.active) this.active.guard.followManualSteps = true;
          return { ...result, followManualSteps: stop };
        }
      }
    });
    if (command.action === "click") this.watchLayerPaint();
    return result;
  }

  disposeImmediate() {
    const active = this.active;
    this.active = undefined;
    this.setFocused(undefined);
    this.pageError = undefined;
    this.lastGoodUrl = undefined;
    this.lastTitle = { at: 0, value: "" };
    this.lastPreview = undefined;
    this.previewInFlight = undefined;
    this.actionBusy = 0;
    this.layerHotUntil = 0;
    clearTimeout(this.inventoryTimer);
    if (!active) return Promise.resolve();
    return Promise.all([
      active.context.close().catch(() => {}),
      active.browser?.close().catch(() => {})
    ]).then(() => undefined);
  }

  async stop(): Promise<RecordingSession> {
    if (!this.active) throw new Error("No active recording");
    const active = this.active;
    this.active = undefined;
    this.focused = undefined;
    this.pageError = undefined;
    this.lastGoodUrl = undefined;
    this.lastTitle = { at: 0, value: "" };
    this.lastPreview = undefined;
    this.previewInFlight = undefined;
    this.actionBusy = 0;
    this.layerHotUntil = 0;
    clearTimeout(this.inventoryTimer);

    await this.drainNetwork();
    active.session.stoppedAt = new Date().toISOString();
    await this.writeManualStepsFile(active);
    const dir = path.dirname(active.eventsFile);
    await writeJson(path.join(dir, "session.json"), active.session);

    if (!active.externalBrowser) {
      await active.context.close().catch(() => {});
      await active.browser?.close().catch(() => {});
    }
    return active.session;
  }
}
