import path from "node:path";
import { chromium, type BrowserContext, type Browser, type Request, type Response, type Page } from "playwright";
import type { EvidenceEvent, NetworkEvidence, RecordingSession, UiEvidence } from "../domain.js";
import type { StudioConfig } from "../config.js";
import { appendJsonl, ensureDir, id, writeJson } from "../utils.js";
import { parsePossiblyJson, redactHeaders, redactValue } from "../security/redact.js";
import { UI_RECORDER_SCRIPT } from "./ui-script.js";

interface ActiveRecording {
  session: RecordingSession;
  browser?: Browser;
  context: BrowserContext;
  eventsFile: string;
  requestIds: WeakMap<Request, string>;
  lastUiByPage: WeakMap<Page, UiEvidence>;
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
      if (!this.active || !page) return;
      const event: UiEvidence = {
        id: id("ui"),
        kind: "ui",
        sessionId: this.active.session.id,
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
      this.active.lastUiByPage.set(page, event);
      await appendJsonl(this.active.eventsFile, event);
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
    let body: unknown = parsePossiblyJson(postData);
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
    }
    return {
      method: request.method(),
      url: request.url(),
      resourceType: request.resourceType(),
      headers: redactHeaders(rawHeaders),
      query: redactValue(this.queryOf(request.url())) as Record<string, string | string[]>,
      body
    };
  }

  private async captureFailedRequest(request: Request) {
    if (!this.active || !this.shouldCapture(request)) return;
    const page = this.pageFor(request);
    const ui = this.recentUi(page);
    const event: NetworkEvidence = {
      id: this.active.requestIds.get(request) || id("net"),
      kind: "network",
      sessionId: this.active.session.id,
      at: new Date().toISOString(),
      pageUrl: page?.url(),
      correlatedUiEvidenceId: ui?.id,
      request: await this.requestPart(request),
      failure: request.failure()?.errorText || "request failed"
    };
    await appendJsonl(this.active.eventsFile, event);
  }

  private async captureResponse(response: Response) {
    if (!this.active) return;
    const request = response.request();
    if (!this.shouldCapture(request)) return;

    const page = this.pageFor(request);
    const ui = this.recentUi(page);

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
      id: this.active.requestIds.get(request) || id("net"),
      kind: "network",
      sessionId: this.active.session.id,
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
    await appendJsonl(this.active.eventsFile, event);
  }

  private recentUi(page?: Page) {
    if (!this.active || !page) return undefined;
    const ui = this.active.lastUiByPage.get(page);
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
    const page = this.currentPage();
    return page.locator(selector).first().evaluate(el => {
      const text = (value: unknown) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 800);
      const form = el.closest("form");
      return {
        text: text(el.textContent || (el as HTMLInputElement).value || ""),
        label: el.getAttribute("aria-label") || undefined,
        name: el.getAttribute("name") || undefined,
        type: el.getAttribute("type") || undefined,
        role: el.getAttribute("role") || undefined,
        formText: text(form?.textContent || ""),
        formMethod: form?.getAttribute("method") || undefined,
        formAction: form?.getAttribute("action") || undefined
      };
    });
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
        await page.locator(command.selector).first().click();
        return { ok: true, url: page.url() };
      case "fill":
        if (!command.selector || typeof command.value !== "string") throw new Error("fill requires selector and string value");
        await page.locator(command.selector).first().fill(command.value);
        return { ok: true };
      case "select":
        if (!command.selector || command.value === undefined) throw new Error("select requires selector and value");
        await page.locator(command.selector).first().selectOption(command.value);
        return { ok: true };
      case "press":
        if (!command.selector || !command.key) throw new Error("press requires selector and key");
        await page.locator(command.selector).first().press(command.key);
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
        const snapshot = await page.locator("body").evaluate(() => {
          const clean = (v: unknown) => String(v || "").replace(/\s+/g, " ").trim().slice(0, 300);
          const selectorOf = (el: Element) => {
            if ((el as HTMLElement).id) return `#${CSS.escape((el as HTMLElement).id)}`;
            const testid = el.getAttribute("data-testid");
            if (testid) return `[data-testid="${CSS.escape(testid)}"]`;
            const name = el.getAttribute("name");
            if (name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
            const aria = el.getAttribute("aria-label");
            if (aria) return `${el.tagName.toLowerCase()}[aria-label="${CSS.escape(aria)}"]`;
            const parts: string[] = [];
            let node: Element | null = el;
            for (let i = 0; node && i < 4; i++, node = node.parentElement) {
              let part = node.tagName.toLowerCase();
              const classes = [...node.classList].slice(0, 2);
              if (classes.length) part += "." + classes.map(c => CSS.escape(c)).join(".");
              parts.unshift(part);
            }
            return parts.join(" > ");
          };
          const controls = [...document.querySelectorAll(
            'a,button,input,select,textarea,[role="button"],[role="combobox"],[role="option"],[role="link"]'
          )].filter(el => {
            const s = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
          }).slice(0, 250).map(el => ({
            selector: selectorOf(el),
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute("role") || undefined,
            label: el.getAttribute("aria-label") || undefined,
            name: el.getAttribute("name") || undefined,
            type: el.getAttribute("type") || undefined,
            text: clean(el.textContent || (el as HTMLInputElement).value || "")
          }));
          return {
            title: document.title,
            url: location.href,
            text: clean(document.body.innerText).slice(0, 12_000),
            controls
          };
        });
        return snapshot;
      }
    }
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
