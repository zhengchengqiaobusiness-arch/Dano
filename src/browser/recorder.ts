import path from "node:path";
import { unlink, writeFile } from "node:fs/promises";
import { chromium, type BrowserContext, type Browser, type Request, type Response, type Page } from "playwright";
import type { EvidenceEvent, NetworkEvidence, OperationKind, RecordingSession, UiEvidence } from "../domain.js";
import type { StudioConfig } from "../config.js";
import { appendJsonl, ensureDir, id, readJsonl, writeJson } from "../utils.js";
import { parsePossiblyJson, redactHeaders, redactValue } from "../security/redact.js";
import { UI_RECORDER_SCRIPT } from "./page-script.js";
import { PageActions, type PageSnapshot } from "./page-actions.js";
import { buildManualSteps, renderManualStepsMarkdown, type ManualStep } from "../record/manual-steps.js";
import { killCommandLineMatches, killProcessTree } from "../process-lifecycle.js";
import { persistOriginCredentials } from "../credentials/credential-store.js";
import { buildCapabilityCandidates } from "../inference/build-candidates.js";
import { summarizeCatalog } from "../inference/export-scope.js";
import { finalizeCapabilities } from "../inference/finalize-capabilities.js";
import { authenticationFailureReason, businessFailureReason, inferUiOperationIntent, isSuccessfulNetworkEvidence } from "../inference/heuristics.js";
import { loadSessionCookies, saveSessionCookies } from "./login-profile.js";
import { reviewCatalog } from "../review/catalog-review.js";

const FORM_ACTION_BUDGET = 3;
const EXPECTABLE_OPERATIONS = new Set<OperationKind>(["query", "create", "update", "review", "delete", "upload", "download", "action"]);
const LOGIN_BLOCKED_ACTIONS = new Set(["goto", "next-page", "click", "fill", "select", "choose", "press", "exercise-form", "submit-form"]);
const DEFAULT_VIEWPORT = { width: 1440, height: 960 };
const MIN_PAGE_VIEWPORT = { width: 1440, height: 900 };
const MAX_PREVIEW_VIEWPORT = { width: 3840, height: 2160 };
const OPERATION_LABEL: Partial<Record<OperationKind, string>> = {
  query: "查询", create: "新增", update: "修改", review: "审核", delete: "删除",
  upload: "上传", download: "导出/下载", action: "业务动作"
};

interface NavigationCoverage {
  discovered: number;
  visited: number;
  remaining: number;
  unvisited: Array<{ label: string; selector: string; url: string }>;
  operationRequirements?: Array<{ url: string; label: string; operations: OperationKind[] }>;
}

function normalizeNavigationUrl(rawUrl: string, baseUrl?: string) {
  try {
    const url = new URL(rawUrl, baseUrl);
    for (const key of [...url.searchParams.keys()]) {
      if (/^(?:utm_.+|_t|t|timestamp|cacheBust)$/i.test(key)) url.searchParams.delete(key);
    }
    if (url.hash.includes("?")) {
      const split = url.hash.slice(1).split("?");
      const route = split.shift() || "";
      const params = new URLSearchParams(split.join("?"));
      for (const key of [...params.keys()]) {
        if (/^(?:utm_.+|_t|t|timestamp|cacheBust)$/i.test(key)) params.delete(key);
      }
      url.hash = params.size ? `${route}?${params}` : route;
    }
    return url.href;
  } catch {
    return rawUrl;
  }
}

export function recordingStopReadiness(
  events: EvidenceEvent[],
  expectedOperations: OperationKind[] = [],
  pageCoverage?: NavigationCoverage,
  completeFieldCoverage = false
) {
  const byId = new Map(events.map(event => [event.id, event]));
  const candidates = finalizeCapabilities(buildCapabilityCandidates(events), events);
  const primary = summarizeCatalog(candidates).primary;
  const successfulOperations = new Set(primary.filter(capability => capability.evidence.some(ref => {
    const event = byId.get(ref.eventId);
    return event?.kind === "network" && isSuccessfulNetworkEvidence(event);
  })).map(capability => capability.operation));
  const expected = [...new Set(expectedOperations)].filter(operation => EXPECTABLE_OPERATIONS.has(operation));
  const missingOperations = expected.filter(operation => !successfulOperations.has(operation));
  const missingPages = pageCoverage?.unvisited || [];
  const eventIndex = new Map(events.map((event, index) => [event.id, index]));
  const requirements = pageCoverage?.operationRequirements || [];
  const requirementByUrl = new Map(requirements.map(item => [normalizeNavigationUrl(item.url), item]));
  const successfulByPage = new Map<string, Set<OperationKind>>();
  const credit = (url: string | undefined, operation: OperationKind) => {
    if (!url || operation === "unknown" || !EXPECTABLE_OPERATIONS.has(operation)) return;
    const key = normalizeNavigationUrl(url);
    if (!requirementByUrl.has(key)) return;
    const operations = successfulByPage.get(key) || new Set<OperationKind>();
    operations.add(operation);
    successfulByPage.set(key, operations);
  };
  for (const capability of primary) {
    for (const ref of capability.evidence) {
      const network = byId.get(ref.eventId);
      if (network?.kind !== "network" || !isSuccessfulNetworkEvidence(network)) continue;
      credit(network.pageUrl, capability.operation);
      const before = eventIndex.get(network.id) ?? events.length;
      for (let index = before - 1; index >= 0; index -= 1) {
        const event = events[index];
        if (event?.kind !== "ui" || event.sessionId !== network.sessionId) continue;
        const intent = inferUiOperationIntent(`${event.text || ""} ${event.label || ""}`, event.pageUrl);
        if (intent !== capability.operation) continue;
        const key = normalizeNavigationUrl(event.pageUrl);
        if (!requirementByUrl.has(key)) continue;
        credit(key, capability.operation);
        break;
      }
    }
  }
  const missingPageOperations = requirements.flatMap(item => {
    const successful = successfulByPage.get(normalizeNavigationUrl(item.url)) || new Set<OperationKind>();
    const operations = item.operations.filter(operation => !successful.has(operation));
    return operations.length ? [{ url: item.url, label: item.label, operations }] : [];
  });
  const failures = events.flatMap(event => {
    if (event.kind !== "network" || isSuccessfulNetworkEvidence(event)) return [];
    const reason = businessFailureReason(event);
    return reason ? [{ url: event.request.url, reason }] : [];
  });
  const contractReview = reviewCatalog(candidates, events, expected, completeFieldCoverage);
  const coverageReady = missingOperations.length === 0 && missingPages.length === 0 && missingPageOperations.length === 0;
  return {
    ready: coverageReady && contractReview.status === "passed",
    expectedOperations: expected,
    successfulOperations: [...successfulOperations],
    missingOperations,
    pageCoverage,
    missingPages,
    missingPageOperations,
    contractReview,
    message: !coverageReady
      ? `录制尚未完成：${missingOperations.length ? `${missingOperations.map(operation => OPERATION_LABEL[operation] || operation).join("、")}没有取得业务成功响应。` : ""}${missingPages.length ? `还有 ${missingPages.length} 个已发现菜单页面没有实际访问：${missingPages.slice(0, 8).map(item => item.label).join("、")}${missingPages.length > 8 ? "等" : ""}。` : ""}${missingPageOperations.length ? `还有 ${missingPageOperations.length} 个页面缺少能力成功证据：${missingPageOperations.slice(0, 8).map(item => `${item.label}（${item.operations.map(operation => OPERATION_LABEL[operation] || operation).join("、")}）`).join("、")}${missingPageOperations.length > 8 ? "等" : ""}。` : ""}继续当前浏览器会话，修复后再结束录制。${failures.length ? ` 最近的业务失败：${failures.at(-1)!.reason}` : ""}`
      : contractReview.status === "passed"
        ? "录制证据和请求合同均已实时审核通过，可以结束录制。"
        : `录制操作已有成功响应，但请求合同尚未通过实时审核。${contractReview.summary}`
  };
}

async function releaseChromiumDebugLog(profileDir: string, timeoutMs = 2_000) {
  const file = path.join(profileDir, "Default", "chrome_debug.log");
  const started = Date.now();
  while (true) {
    try {
      await unlink(file);
      return;
    } catch (error: any) {
      if (error?.code === "ENOENT") return;
      if (!new Set(["EBUSY", "EPERM", "EACCES"]).has(error?.code) || Date.now() - started >= timeoutMs) throw error;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  }
}

export function normalizePreviewScale(input?: number | null) {
  const scale = Number(input);
  if (!Number.isFinite(scale) || scale < 1) return 1;
  return Math.min(2, Math.round(scale * 100) / 100);
}

export type PreviewViewport = { width: number; height: number; scale: number };

export interface LoginPageState {
  detected: boolean;
  reason?: string;
  pageUrl?: string;
  frameUrl?: string;
}

export function dragInterpolationSteps(from: { x: number; y: number }, to: { x: number; y: number }, maxStep = 3) {
  const dist = Math.hypot(to.x - from.x, to.y - from.y);
  if (!Number.isFinite(dist) || dist <= 0) return 1;
  return Math.max(1, Math.min(160, Math.ceil(dist / Math.max(1, maxStep))));
}

export function normalizePreviewViewport(input?: { width?: number; height?: number; scale?: number } | null): PreviewViewport {
  const rawWidth = Math.round(Number(input?.width));
  const rawHeight = Math.round(Number(input?.height));
  let width = Number.isFinite(rawWidth) && rawWidth >= 80 ? rawWidth : DEFAULT_VIEWPORT.width;
  let height = Number.isFinite(rawHeight) && rawHeight >= 80 ? rawHeight : DEFAULT_VIEWPORT.height;
  if (width < MIN_PAGE_VIEWPORT.width || height < MIN_PAGE_VIEWPORT.height) {
    width = DEFAULT_VIEWPORT.width;
    height = DEFAULT_VIEWPORT.height;
  }
  if (width > MAX_PREVIEW_VIEWPORT.width || height > MAX_PREVIEW_VIEWPORT.height) {
    const down = Math.min(MAX_PREVIEW_VIEWPORT.width / width, MAX_PREVIEW_VIEWPORT.height / height);
    width = Math.max(MIN_PAGE_VIEWPORT.width, Math.round(width * down));
    height = Math.max(MIN_PAGE_VIEWPORT.height, Math.round(height * down));
  }
  return { width, height, scale: 1 };
}

export const MIN_PREVIEW_BYTES = 800;
export const MIN_PREVIEW_WIDTH = 200;
export const MIN_PREVIEW_HEIGHT = 120;

export function readJpegDimensions(buffer: Buffer): { width: number; height: number } | undefined {
  if (buffer.length < 4 || buffer[0] !== 0xff || buffer[1] !== 0xd8) return undefined;
  let offset = 2;
  while (offset + 1 < buffer.length) {
    if (buffer[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = buffer.readUInt8(offset + 1);
    if (marker === 0x00 || marker === 0xff) {
      offset += 1;
      continue;
    }
    if (marker === 0xd8) {
      offset += 2;
      continue;
    }
    if (marker === 0xd9) break;
    if (marker >= 0xd0 && marker <= 0xd7) {
      offset += 2;
      continue;
    }
    if (offset + 3 >= buffer.length) return undefined;
    const size = buffer.readUInt16BE(offset + 2);
    if (size < 2) return undefined;
    if (marker >= 0xc0 && marker <= 0xc3) {
      if (offset + 8 >= buffer.length) return undefined;
      const height = buffer.readUInt16BE(offset + 5);
      const width = buffer.readUInt16BE(offset + 7);
      if (width < 1 || height < 1) return undefined;
      return { width, height };
    }
    offset += 2 + size;
  }
  return undefined;
}

export function isUsablePreviewBuffer(buffer: Buffer | undefined | null): buffer is Buffer {
  if (!buffer || buffer.byteLength < MIN_PREVIEW_BYTES) return false;
  const size = readJpegDimensions(buffer);
  if (!size) return true;
  return size.width >= MIN_PREVIEW_WIDTH && size.height >= MIN_PREVIEW_HEIGHT;
}

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

interface LoginPageEvidence {
  detected: boolean;
  href: string;
}

const DETECT_LOGIN_IN_PAGE = new Function(String.raw`
  const visible = (element) => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
  };
  const describe = (element) => {
    const label = (element.labels && element.labels[0] && element.labels[0].textContent) || (element.closest("label") && element.closest("label").textContent) || "";
    return [element.type, element.name, element.id, element.autocomplete, element.placeholder, element.getAttribute("aria-label"), label]
      .filter(Boolean).join(" ");
  };
  const inputs = [...document.querySelectorAll("input")].filter(visible);
  const password = inputs.filter(element => element.type === "password");
  const accounts = inputs.filter(element => /用户|账号|帐号|工号|手机|邮箱|username|account|email|phone|mobile/i.test(describe(element)));
  const otp = inputs.filter(element => /验证码|动态码|短信码|one.?time|verification|captcha|otp/i.test(describe(element)));
  const controls = [...document.querySelectorAll("button,input[type='submit'],a,[role='button'],h1,h2,h3")].filter(visible);
  const loginText = controls.map(element => String(element.value || element.textContent || "").replace(/\s+/g, " ").trim())
    .some(text => text.length <= 40 && /登录|登陆|登入|统一身份认证|sign\s*in|log\s*in|login/i.test(text));
  const formAction = [...document.querySelectorAll("form")].some(form => {
    const action = String(form.getAttribute("action") || "");
    if (/(?:^|[\/_-])login(?:[\/?#_-]|$)|sign[\s_-]*in/i.test(action)) return true;
    return [...form.querySelectorAll("button,input[type='submit'],[role='button']")].some(control => {
      const text = String(control.value || control.textContent || "").replace(/\s+/g, " ").trim();
      return /^(登录|登陆|登入|sign\s*in|log\s*in|login)$/i.test(text);
    });
  });
  const urlIntent = /(?:^|[\/#?&=._-])(?:login|log-in|signin|sign-in|sso)(?:$|[\/#?&=._-])/i.test(location.href);
  const qr = [...document.querySelectorAll("canvas,img,svg")].filter(visible)
    .some(element => /二维码|qr.?code|qrcode/i.test(String(element.id) + " " + String(element.className) + " " + String(element.getAttribute("alt") || "")));
  return {
    detected: (loginText || formAction || urlIntent) && (password.length > 0 || accounts.length > 0 || otp.length > 0 || qr),
    href: location.href
  };
`) as () => LoginPageEvidence;

interface ActionGuard {
  consecutiveFailures: number;
  followManualSteps: boolean;
  formScopeKey?: string;
  wholeFormExercised?: boolean;
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
  navigationTargets: Map<string, { label: string; selector: string; url: string }>;
  visitedNavigationKeys: Set<string>;
  pageOperationRequirements: Map<string, { label: string; operations: Set<OperationKind> }>;
  authenticationFailure?: { at: number; reason: string; pageUrl?: string };
}

export class BrowserRecorder {
  private active?: ActiveRecording;
  private browserPid?: number;
  private browserLaunched = false;
  private actionBusy = 0;
  private readonly networkJobs = new Set<Promise<void>>();
  private readonly pendingRequests = new Map<Request, number>();
  private previewInFlight?: Promise<Buffer>;
  private screenshotInFlight?: Promise<Buffer | undefined>;
  private lastPreview?: { at: number; buffer: Buffer };
  private focused?: Page;
  private pageError?: string;
  private lastGoodUrl?: string;
  private lastTitle = { at: 0, value: "" };
  private layerHotUntil = 0;
  private layerWatch?: Promise<void>;
  private inventoryTimer?: ReturnType<typeof setTimeout>;
  private manualPointer?: { x: number; y: number; down: boolean };
  private readonly actions = new PageActions({
    page: () => this.currentPage(),
    writePageInventory: (page, snapshot) => this.writePageInventory(page, snapshot),
    recentUserActions: () => this.recentUserActions(),
    drainNetwork: timeout => this.drainNetwork(timeout),
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

  private navigationKey(rawUrl: string, baseUrl?: string) {
    return normalizeNavigationUrl(rawUrl, baseUrl);
  }

  private pageOperationCoverage() {
    const active = this.active;
    if (!active) return [];
    return [...active.pageOperationRequirements.entries()].map(([url, requirement]) => ({
      url,
      label: requirement.label,
      operations: [...requirement.operations]
    }));
  }

  private navigationCoverage() {
    const active = this.active;
    if (!active) return { discovered: 0, visited: 0, remaining: 0, unvisited: [] as Array<{ label: string; selector: string; url: string }> };
    const targets = [...active.navigationTargets.entries()];
    const unvisited = targets.filter(([key]) => !active.visitedNavigationKeys.has(key)).map(([, target]) => target);
    return {
      discovered: targets.length,
      visited: targets.length - unvisited.length,
      remaining: unvisited.length,
      unvisited
    };
  }

  private updateNavigationCoverage(page: Page, snapshot: PageSnapshot) {
    const active = this.active;
    if (!active) return;
    const currentKey = this.navigationKey(String(snapshot.url || page.url()), page.url());
    if (!active.navigationTargets.has(currentKey)) {
      active.navigationTargets.set(currentKey, {
        label: String(snapshot.title || currentKey),
        selector: `url=${currentKey}`,
        url: currentKey
      });
    }
    active.visitedNavigationKeys.add(currentKey);
    for (const target of snapshot.navigationInventory || []) {
      const key = this.navigationKey(target.url, page.url());
      if (!active.navigationTargets.has(key)) active.navigationTargets.set(key, { ...target, url: key });
    }
    if (active.session.completePageCoverage) {
      const pageKey = this.navigationKey(String(snapshot.url || page.url()), page.url());
      const expected = new Set(active.session.expectedOperations || []);
      const applicable = (snapshot.availableOperations || []).filter(operation => expected.has(operation));
      const prior = active.pageOperationRequirements.get(pageKey);
      const operations = prior?.operations || new Set<OperationKind>();
      // Query controls can be transient (global search popovers, dashboard
      // cards, stale SPA content). A later stable page-scope snapshot is the
      // authoritative view for whether this page has a submit-able query.
      // Keep write/action requirements monotonic because row actions may
      // legitimately disappear after a filter changes the result set.
      if (snapshot.scope === "page" && !applicable.includes("query")) operations.delete("query");
      for (const operation of applicable) operations.add(operation);
      const target = active.navigationTargets.get(pageKey);
      active.pageOperationRequirements.set(pageKey, {
        label: target?.label || prior?.label || String(snapshot.title || pageKey),
        operations
      });
    }
    snapshot.navigationInventory = (snapshot.navigationInventory || []).map(target => {
      const key = this.navigationKey(target.url, page.url());
      return { ...target, url: key, visited: active.visitedNavigationKeys.has(key) };
    });
    snapshot.navigationCoverage = this.navigationCoverage();
    active.session.discoveredPages = [...active.navigationTargets.keys()];
    active.session.visitedPages = [...active.visitedNavigationKeys].filter(key => active.navigationTargets.has(key));
    active.session.pageOperations = this.pageOperationCoverage();
  }

  private markNavigationTargetVisited(urlOrSelector: string) {
    const active = this.active;
    if (!active) return;
    const directKey = this.navigationKey(urlOrSelector, this.currentPage().url());
    if (active.navigationTargets.has(directKey)) active.visitedNavigationKeys.add(directKey);
    for (const [key, target] of active.navigationTargets) {
      if (target.selector === urlOrSelector) active.visitedNavigationKeys.add(key);
    }
  }

  isActive() {
    return Boolean(this.active);
  }

  browserProcessId() {
    return this.browserPid;
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
    const cached = this.lastGoodPreview();
    const layerHot = this.layerHotUntil > Date.now();
    if (cached && (this.actionBusy > 0 || layerHot || Date.now() - cached.at < 180)) {
      return cached.buffer;
    }
    if (cached && this.screenshotInFlight) return cached.buffer;
    if (this.previewInFlight) return this.previewInFlight;
    this.previewInFlight = this.capturePreview().finally(() => {
      this.previewInFlight = undefined;
    });
    return this.previewInFlight;
  }

  private lastGoodPreview() {
    return this.lastPreview && isUsablePreviewBuffer(this.lastPreview.buffer) ? this.lastPreview : undefined;
  }

  private takePreviewScreenshot(page: Page): Promise<Buffer | undefined> {
    if (this.screenshotInFlight) return this.screenshotInFlight;
    this.screenshotInFlight = page.screenshot({
      type: "jpeg",
      quality: 82,
      scale: "css",
      fullPage: false,
      animations: "allow",
      caret: "hide",
      timeout: 4_000
    }).then(buffer => {
      if (!isUsablePreviewBuffer(buffer)) return undefined;
      this.lastPreview = { at: Date.now(), buffer };
      this.lastGoodUrl = page.url();
      return buffer;
    }).catch(() => undefined).finally(() => {
      this.screenshotInFlight = undefined;
    });
    return this.screenshotInFlight;
  }

  private async capturePreview() {
    const page = this.currentPage();
    const cached = this.lastGoodPreview();
    if (this.isErrorUrl(page.url())) {
      this.pageError = this.pageError || `页面打开失败：${page.url()}`;
      if (cached) return cached.buffer;
      throw new Error("preview unavailable");
    }
    const buffer = await this.withTimeout(this.takePreviewScreenshot(page), 1_600, undefined);
    if (isUsablePreviewBuffer(buffer)) return buffer;
    if (cached) return cached.buffer;
    throw new Error("preview unavailable");
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
    }
  }

  private async waitForPageQuiet(page: Page, timeout = 800) {
    void page;
    await this.actions.waitForPageQuiet(timeout);
    await this.drainNetwork(Math.max(timeout, 1_200));
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

  async start(
    startUrl: string,
    name = "recording",
    viewport?: { width?: number; height?: number; scale?: number },
    expectedOperations: OperationKind[] = [],
    completeFieldCoverage = false,
    completePageCoverage = false
  ): Promise<RecordingSession> {
    if (this.active) throw new Error(`Recording already active: ${this.active.session.id}`);

    const sessionId = id("rec");
    const dir = path.join(this.config.recordingsDir, sessionId);
    await ensureDir(dir);
    await ensureDir(this.config.profileDir);
    const size = normalizePreviewViewport(viewport);

    const launchOptions = {
      headless: this.config.headless,
      viewport: { width: size.width, height: size.height },
      deviceScaleFactor: 1,
      args: ["--disable-features=TranslateUI,IsolateOrigins,site-per-process", "--disable-background-timer-throttling", "--disable-site-isolation-trials"]
    };
    const context = await chromium.launchPersistentContext(this.config.profileDir, launchOptions).catch(error => {
      if (!/Executable doesn't exist/i.test(String(error))) throw error;
      return chromium.launchPersistentContext(this.config.profileDir, { ...launchOptions, channel: "chrome" });
    });
    const sessionCookies = await loadSessionCookies(this.config.profileDir);
    if (sessionCookies.length) await context.addCookies(sessionCookies);
    const restoredPages = context.pages();
    const page = restoredPages[0] || await context.newPage();
    await Promise.all(restoredPages.slice(1).map(stale => stale.close().catch(() => {})));

    const eventsFile = path.join(dir, "events.jsonl");
    const session: RecordingSession = {
      id: sessionId,
      name,
      startedAt: new Date().toISOString(),
      startUrl,
      eventsFile,
      expectedOperations: [...new Set(expectedOperations)].filter(operation => EXPECTABLE_OPERATIONS.has(operation)),
      completeFieldCoverage,
      completePageCoverage
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
      guard: { consecutiveFailures: 0, followManualSteps: false },
      navigationTargets: new Map(),
      visitedNavigationKeys: new Set(),
      pageOperationRequirements: new Map()
    };
    this.captureBrowserPid(context);

    await writeJson(path.join(dir, "session.json"), session);
    await this.instrument(context);
    const armPage = (target: Page) => this.armPage(target);
    for (const existing of context.pages()) armPage(existing);
    context.on("page", armPage);

    this.setFocused(page);
    try {
      await page.goto(startUrl, { waitUntil: "domcontentloaded" });
      await this.waitForPageQuiet(page, 5_000);
      if (await this.actions.isStartupSplash()) {
        await page.reload({ waitUntil: "domcontentloaded" });
        await this.waitForPageQuiet(page, 8_000);
      }
      if (await this.actions.isStartupSplash()) {
        this.pageError = `页面启动界面没有完成加载：${startUrl}`;
      } else if (await this.pageLooksFailed(page)) {
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

  async fitViewport(viewport?: { width?: number; height?: number; scale?: number }) {
    const size = normalizePreviewViewport(viewport);
    if (!this.active) return size;
    for (const page of this.livePages()) {
      const current = page.viewportSize();
      if (current?.width === size.width && current?.height === size.height) continue;
      await page.setViewportSize({ width: size.width, height: size.height });
    }
    return size;
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
      if (this.shouldCapture(request) && !this.isNoiseUrl(request.url())) {
        this.pendingRequests.set(request, Date.now());
      }
    });

    context.on("requestfailed", request => {
      this.pendingRequests.delete(request);
      this.trackNetwork(this.captureFailedRequest(request));
    });

    context.on("requestfinished", request => {
      this.pendingRequests.delete(request);
    });

    context.on("response", response => {
      this.pendingRequests.delete(response.request());
      this.trackNetwork(this.captureResponse(response));
    });
  }

  private trackNetwork(job: Promise<void>) {
    const tracked = job.catch(() => {});
    this.networkJobs.add(tracked);
    void tracked.finally(() => this.networkJobs.delete(tracked));
  }

  private isNoiseUrl(url: string) {
    return /\/im\/|unread-count|online-status|notify-message|get-permission-info/i.test(url);
  }

  private async drainNetwork(timeout = 1_500) {
    const watchFrom = Date.now() - 80;
    const deadline = Date.now() + timeout;
    const relevant = () =>
      this.networkJobs.size > 0
      || [...this.pendingRequests.entries()].some(([, started]) => started >= watchFrom);
    while (relevant() && Date.now() < deadline) {
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

  private clearsAuthenticationFailure(event: NetworkEvidence) {
    const status = event.response?.status;
    if (!status || status >= 400) return false;
    if (authenticationFailureReason(event.response)) return false;
    try {
      const url = new URL(event.request.url);
      const page = event.pageUrl ? new URL(event.pageUrl) : undefined;
      if (page && url.origin !== page.origin) return false;
      return /\/admin-api\/|\/api\//i.test(url.pathname);
    } catch {
      return false;
    }
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
    await persistOriginCredentials(this.config.dataDir, request.url(), rawHeaders).catch(() => {});
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

  async loginPageState(): Promise<LoginPageState> {
    if (!this.active) return { detected: false };
    const page = this.currentPage();
    for (const frame of page.frames()) {
      const evidence = await this.withTimeout(frame.evaluate(DETECT_LOGIN_IN_PAGE), 900, undefined);
      if (!evidence?.detected) continue;
      return {
        detected: true,
        reason: "检测到登录页面，已暂停自动操作。请在内置浏览器完成登录后点击“我已完成，继续自动执行”。",
        pageUrl: page.url(),
        frameUrl: evidence.href
      };
    }
    return { detected: false };
  }

  private loginPauseResult(state: LoginPageState) {
    return {
      ok: false,
      stopped: true,
      followManualSteps: false,
      loginRequired: true,
      reason: state.reason,
      pageUrl: state.pageUrl,
      frameUrl: state.frameUrl
    };
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
    const loginFailure = authenticationFailureReason(event.response);
    if (loginFailure) {
      active.authenticationFailure = { at: Date.now(), reason: loginFailure, pageUrl: page?.url() };
    } else if (this.clearsAuthenticationFailure(event)) {
      active.authenticationFailure = undefined;
    }
    await appendJsonl(active.eventsFile, event);
  }

  private stabilizeUiEvent(event: UiEvidence): UiEvidence {
    const value = event.value === undefined || event.value === null ? "" : String(event.value);
    const cssy = (text?: string) => Boolean(text && ((/^[a-z]+\./i.test(text) && text.includes(">")) || /(?:arco-|el-|ant-|vxe-)[\w-]*\./.test(text)));
    const actionName = String(event.text || event.label || "").replace(/\s+/g, " ").trim();
    const actionClick = event.tag === "button" || event.role === "button" || event.inputType === "submit" || event.inputType === "button"
      || /^(确\s*认|确\s*定|取\s*消|提交|保存|搜索|查询|关闭)/.test(actionName);
    if (actionClick && actionName && actionName.length <= 40 && !cssy(actionName)) {
      return {
        ...event,
        label: actionName.replace(/\s+/g, "") || event.label,
        selector: cssy(event.selector) ? `role=button[name="${actionName}"]` : event.selector
      };
    }
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
    this.updateNavigationCoverage(page, snapshot);
    const fromFields = (snapshot.formFields || []).map(field => ({
      name: typeof field.name === "string" ? field.name : undefined,
      label: typeof field.label === "string" ? field.label : undefined,
      type: String(field.kind || field.type || "text"),
      value: field.value,
      required: Boolean(field.required),
      options: Array.isArray(field.options) ? field.options : undefined,
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
      const editable = el.matches('input:not([type="button"]):not([type="submit"]):not([type="reset"]),select,textarea,[contenteditable="true"],[role="combobox"]');
      if (editable) {
        el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (${JSON.stringify(eventType)} !== "click" && typeof window.__bssFlushUi === "function") {
        window.__bssFlushUi(${JSON.stringify(eventType)}, el);
      }
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
      await this.withTimeout(page.waitForURL(url => !this.isTransientUrl(url.toString()), { timeout: 2_000 }), 2_000, undefined);
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
    this.layerHotUntil = Date.now() + 650;
    if (this.layerWatch) return;
    this.layerWatch = (async () => {
      await new Promise(resolve => setTimeout(resolve, 420));
      if (!this.isActive()) return;
      await this.actions.nudgeOverlayFrames();
      const page = this.currentPage();
      for (const frame of page.frames()) {
        if (frame === page.mainFrame()) continue;
        await frame.waitForLoadState("domcontentloaded").catch(() => {});
      }
    })().catch(() => {}).finally(() => {
      this.layerWatch = undefined;
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
    | { action: "drag"; phase?: "start" | "move" | "end" | "path"; x?: number; y?: number; toX?: number; toY?: number; points?: Array<{ x: number; y: number }>; button?: "left" | "right" | "middle" }
    | { action: "text"; value: string }
    | { action: "key"; key: string }
    | { action: "scroll"; deltaX?: number; deltaY?: number }
  ) {
    const dragPhase = command.action === "drag" ? command.phase || "path" : undefined;
    const liveDrag = dragPhase === "start" || dragPhase === "move";
    const result = await this.withAction(async () => {
      const page = this.currentPage();
      const known = new Set(this.livePages());
      const watch = command.action === "click" || command.action === "key" || (command.action === "drag" && !liveDrag)
        ? this.watchNewPages()
        : undefined;
      if (command.action === "click") {
        await this.releaseManualPointer(page);
        const point = this.manualViewportPoint(page, command.x, command.y, "click");
        await page.mouse.click(point.x, point.y, {
          button: command.button || "left",
          clickCount: Math.max(1, Math.min(Number(command.clickCount) || 1, 2))
        });
        this.manualPointer = { ...point, down: false };
      } else if (command.action === "drag") {
        await this.applyManualDrag(page, command);
      } else if (command.action === "text") {
        if (typeof command.value !== "string" || command.value.length > 10_000) throw new Error("manual text requires a string no longer than 10000 characters");
        await page.keyboard.insertText(command.value);
      } else if (command.action === "key") {
        if (typeof command.key !== "string" || !command.key || command.key.length > 80) throw new Error("manual key requires a valid key name");
        await page.keyboard.press(command.key);
      } else if (command.action === "scroll") {
        const deltaX = Math.max(-5_000, Math.min(Number(command.deltaX) || 0, 5_000));
        const deltaY = Math.max(-5_000, Math.min(Number(command.deltaY) || 0, 5_000));
        await page.mouse.wheel(deltaX, deltaY);
      } else {
        throw new Error("manual control requires click, drag, text, key, or scroll");
      }
      const observed = liveDrag || command.action === "scroll"
        ? undefined
        : await this.captureActiveControl(command.action === "text" ? "input" : command.action === "key" ? "change" : "click", page);
      if (!liveDrag && command.action !== "scroll") this.scheduleInventory();
      try {
        if (command.action === "click" || command.action === "key" || (command.action === "drag" && !liveDrag)) {
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
    if (command.action === "click" || command.action === "key" || (command.action === "drag" && !liveDrag)) this.watchLayerPaint();
    return result;
  }

  private manualViewportPoint(page: Page, x: number, y: number, label: string) {
    const viewport = page.viewportSize();
    if (!viewport || !Number.isFinite(x) || !Number.isFinite(y)) throw new Error(`manual ${label} requires finite x and y coordinates`);
    const next = {
      x: Math.min(viewport.width, Math.max(0, x)),
      y: Math.min(viewport.height, Math.max(0, y))
    };
    if (Math.abs(next.x - x) > 1 || Math.abs(next.y - y) > 1) {
      throw new Error(`manual ${label} coordinates are outside the embedded browser viewport`);
    }
    return next;
  }

  private manualDragPoints(page: Page, command: { x?: number; y?: number; toX?: number; toY?: number; points?: Array<{ x: number; y: number }> }) {
    const raw = Array.isArray(command.points) && command.points.length
      ? command.points
      : [{ x: command.x, y: command.y }, { x: command.toX, y: command.toY }];
    const points = raw.slice(0, 240).map(item => this.manualViewportPoint(page, Number(item?.x), Number(item?.y), "drag"));
    if (points.length < 2) throw new Error("manual drag requires at least two points");
    return points;
  }

  private async moveManualMouse(page: Page, point: { x: number; y: number }) {
    const from = this.manualPointer || point;
    await page.mouse.move(point.x, point.y, { steps: dragInterpolationSteps(from, point) });
    this.manualPointer = { ...point, down: this.manualPointer?.down ?? false };
  }

  private async releaseManualPointer(page: Page, button: "left" | "right" | "middle" = "left") {
    if (!this.manualPointer?.down) return;
    await page.mouse.up({ button });
    this.manualPointer = { ...this.manualPointer, down: false };
  }

  private async applyManualDrag(page: Page, command: {
    phase?: "start" | "move" | "end" | "path";
    x?: number;
    y?: number;
    toX?: number;
    toY?: number;
    points?: Array<{ x: number; y: number }>;
    button?: "left" | "right" | "middle";
  }) {
    const button = command.button || "left";
    const phase = command.phase || "path";
    if (phase === "path") {
      const points = this.manualDragPoints(page, command);
      await this.releaseManualPointer(page, button);
      await this.moveManualMouse(page, points[0]!);
      await page.mouse.down({ button });
      this.manualPointer = { ...points[0]!, down: true };
      for (const point of points.slice(1)) await this.moveManualMouse(page, point);
      await page.mouse.up({ button });
      this.manualPointer = { ...points[points.length - 1]!, down: false };
      return;
    }
    const point = this.manualViewportPoint(page, Number(command.x), Number(command.y), "drag");
    if (phase === "start") {
      await this.releaseManualPointer(page, button);
      await this.moveManualMouse(page, point);
      await page.mouse.down({ button });
      this.manualPointer = { ...point, down: true };
      return;
    }
    if (!this.manualPointer?.down) throw new Error("manual drag move/end requires an active pointer");
    await this.moveManualMouse(page, point);
    if (phase === "end") {
      await page.mouse.up({ button });
      this.manualPointer = { ...point, down: false };
    }
  }

  private resetActionGuard(formScopeKey?: string) {
    if (!this.active) return;
    this.active.guard = {
      consecutiveFailures: 0,
      followManualSteps: false,
      formScopeKey,
      wholeFormExercised: false
    };
  }

  private async ensureFormScope() {
    if (!this.active) return;
    const formScopeKey = await this.actions.formScopeKey();
    if (this.active.guard.formScopeKey !== formScopeKey) this.resetActionGuard(formScopeKey);
  }

  resumeAfterManualTakeover() {
    this.resetActionGuard();
  }

  private async requireWholeFormBeforeDirectFieldAction(selector?: string) {
    const active = this.active;
    if (!active?.session.completeFieldCoverage || !selector) return undefined;
    await this.ensureFormScope();
    if (active.guard.wholeFormExercised) return undefined;
    const snapshot = await this.actions.captureSnapshot();
    const normalized = selector.replace(/^label=/i, "");
    const target = (snapshot.formFields || []).find(field =>
      field.selector === selector || field.label === normalized || field.name === normalized
    );
    if (!target || target.skip || target.disabled) return undefined;
    const fields = (snapshot.formFields || []).filter(field => !field.skip && !field.disabled);
    return {
      ok: false,
      requiresWholeForm: true,
      recommendedAction: "exercise-form",
      reason: "当前录制要求完整字段覆盖；首次填写必须调用一次 exercise-form，不能从单字段操作开始。",
      todoFields: fields,
      todoCount: fields.length,
      formFields: snapshot.formFields || [],
      followManualSteps: false
    };
  }

  private async markWholeFormExercised() {
    await this.ensureFormScope();
    if (this.active) this.active.guard.wholeFormExercised = true;
  }

  async stopReadiness() {
    const active = this.active;
    if (!active) return {
      ready: true,
      expectedOperations: [] as OperationKind[],
      successfulOperations: [] as OperationKind[],
      missingOperations: [] as OperationKind[],
      pageCoverage: undefined,
      missingPages: [] as Array<{ url: string; title?: string }>,
      missingPageOperations: [] as Array<{ url: string; label: string; operations: OperationKind[] }>,
      missingFields: [] as NonNullable<PageSnapshot["todoFields"]>,
      contractReview: reviewCatalog([]),
      nextAction: { action: "none" },
      message: "当前没有活动录制。"
    };
    return this.recordingAudit(300);
  }

  private async recordingAudit(drainMs = 0, knownSnapshot?: PageSnapshot) {
    const active = this.active;
    if (!active) throw new Error("No active recording");
    if (drainMs > 0) await this.drainNetwork(drainMs);
    const events = await readJsonl<EvidenceEvent>(active.eventsFile);
    const pageCoverage = active.session.completePageCoverage
      ? { ...this.navigationCoverage(), operationRequirements: this.pageOperationCoverage() }
      : undefined;
    const base = recordingStopReadiness(
      events,
      active.session.expectedOperations || [],
      pageCoverage,
      active.session.completeFieldCoverage === true
    );
    if (active.session.completeFieldCoverage) await this.ensureFormScope();
    const snapshot = active.session.completeFieldCoverage
      ? knownSnapshot || await this.actions.captureSnapshot()
      : knownSnapshot;
    const eligibleFields = (snapshot?.formFields || []).filter(field => !field.skip && !field.disabled);
    const needsWholeFormPass = active.session.completeFieldCoverage && eligibleFields.length > 0 && !active.guard.wholeFormExercised;
    const missingFields = active.session.completeFieldCoverage
      ? needsWholeFormPass ? eligibleFields : (snapshot?.todoFields || []).filter(field => !field.skip && !field.disabled)
      : [];
    const currentPageKey = this.navigationKey(String(snapshot?.url || this.currentPage().url()), this.currentPage().url());
    const currentPageOperations = base.missingPageOperations.find(item => this.navigationKey(item.url) === currentPageKey)?.operations || [];
    const ready = base.ready && missingFields.length === 0;
    const contractFinding = base.contractReview.findings[0];
    const nextAction = missingFields.length
      ? { action: "exercise-form", fields: missingFields.map(field => field.label || field.name).filter(Boolean) }
      : currentPageOperations.length
        ? { action: "perform-operation", operations: currentPageOperations }
        : base.missingOperations.length
          ? { action: "perform-operation", operations: base.missingOperations }
        : base.missingPages.length
            ? { action: "next-page", target: base.missingPages[0] }
            : contractFinding
              ? {
                  action: contractFinding.next === "re-record" ? "perform-operation" : "repair-contract",
                  ...(contractFinding.capabilityId ? { capabilityId: contractFinding.capabilityId } : {}),
                  finding: contractFinding.message
                }
            : { action: "record-stop" };
    const fieldMessage = missingFields.length
      ? ` 当前页面仍缺 ${missingFields.length} 个字段：${missingFields.slice(0, 8).map(field => field.label || field.name).filter(Boolean).join("、")}。`
      : "";
    return {
      ...base,
      ready,
      missingFields,
      nextAction,
      message: ready
        ? "实时审核通过：要求的页面、字段、操作、业务成功响应和请求合同均已覆盖，可以结束录制。"
        : `实时审核未通过。${base.message}${fieldMessage}只继续处理 nextAction 指向的缺口。`
    };
  }

  private manualTakeoverReason(action: string, selector?: string, detail?: string) {
    const formAction = action === "exercise-form" || action === "submit-form";
    const location = selector || (formAction ? "当前表单" : "当前页面");
    const instruction = action === "click"
      ? `在左侧内置浏览器找到并点击“${location}”`
      : action === "fill"
        ? `在左侧内置浏览器的“${location}”字段填写页面接受的值`
        : action === "choose" || action === "select"
          ? `在左侧内置浏览器打开“${location}”并选择一个真实候选`
          : action === "submit-form"
            ? "在左侧内置浏览器检查表单报错，补齐后点击提交/确定，直到页面确认成功"
            : action === "exercise-form"
              ? "在左侧内置浏览器按页面提示补齐当前表单仍为空或报错的字段"
              : `在左侧内置浏览器完成“${action}”这一步`;
    return [
      `自动${action}连续失败 ${FORM_ACTION_BUDGET} 次。`,
      `问题位置：${location}`,
      `失败原因：${detail || "页面没有确认该操作成功"}`,
      `请手动操作：${instruction}。完成并确认页面已生效后，点击右侧“我已完成，继续自动执行”。`
    ].join("\n");
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

  private resetFailureStreak() {
    if (!this.active || this.active.guard.followManualSteps) return;
    this.active.guard.consecutiveFailures = 0;
  }

  private recordFailure(action: string, detail?: string, selector?: string) {
    if (!this.active) return undefined;
    this.active.guard.consecutiveFailures += 1;
    if (this.active.guard.consecutiveFailures < FORM_ACTION_BUDGET) return undefined;
    return this.stopBecauseStuck(this.manualTakeoverReason(action, selector, detail));
  }

  private async guardedPageAction<T>(action: string, selector: string | undefined, work: () => Promise<T>): Promise<T | ReturnType<BrowserRecorder["stopBecauseStuck"]>> {
    try {
      const result = await work();
      this.resetFailureStreak();
      return result;
    } catch (error: any) {
      const stopped = this.recordFailure(action, String(error?.message || error), selector);
      if (stopped) return stopped;
      throw error;
    }
  }

  private async guardedFormAction(action: "exercise-form" | "submit-form", work: () => Promise<{ ok?: boolean }>) {
    await this.ensureFormScope();
    try {
      for (let automaticAttempts = 1; automaticAttempts <= FORM_ACTION_BUDGET; automaticAttempts += 1) {
        const result = await work() as { ok?: boolean; retryReady?: boolean; businessFailure?: string; loginRequired?: boolean; loginReason?: string; todoFields?: Array<{ label?: string; name?: string }> };
        if (result.loginRequired) {
          return {
            ...result,
            automaticAttempts,
            stopped: true,
            followManualSteps: false,
            reason: `检测到登录状态失效（${result.loginReason || result.businessFailure || "未登录"}），已暂停自动操作。请在内置浏览器完成登录后点击“我已完成，继续自动执行”。`
          };
        }
        if (result.ok) {
          this.resetFailureStreak();
          return { ...result, automaticAttempts, followManualSteps: false };
        }
        const unfinished = (result.todoFields || []).map(field => field.label || field.name).filter(Boolean).slice(0, 8);
        const detail = result.businessFailure || (unfinished.length ? `未能完成字段：${unfinished.join("、")}` : undefined);
        const stopped = this.recordFailure(action, detail);
        if (stopped) return { ...result, automaticAttempts, ...stopped };
        if (action === "submit-form" && result.retryReady) continue;
        return { ...result, automaticAttempts, followManualSteps: false };
      }
      return this.stopBecauseStuck(this.manualTakeoverReason(action));
    } catch (error: any) {
      const stopped = this.recordFailure(action, String(error?.message || error));
      if (stopped) return stopped;
      throw error;
    }
  }

  async control(command: {
    action: "goto" | "next-page" | "snapshot" | "click" | "fill" | "select" | "choose" | "press" | "wait" | "screenshot" | "exercise-form" | "submit-form";
    selector?: string;
    value?: string | string[];
    url?: string;
    key?: string;
    ms?: number;
  }): Promise<unknown> {
    const actionStartedAt = Date.now();
    if (LOGIN_BLOCKED_ACTIONS.has(command.action)) {
      const login = await this.loginPageState();
      if (login.detected) return this.loginPauseResult(login);
    }
    const result = await this.withAction(async () => {
      const page = this.currentPage();
      const known = new Set(this.livePages());
      const watch = command.action === "click" ? this.watchNewPages() : undefined;
      switch (command.action) {
        case "goto":
          if (!command.url) throw new Error("goto requires url");
          if (this.active?.session.completePageCoverage) {
            const requested = this.navigationKey(command.url, page.url());
            const current = this.navigationKey(page.url());
            const start = this.navigationKey(this.active.session.startUrl);
            if (requested !== current && requested !== start && !this.active.navigationTargets.has(requested)) {
              return {
                ok: false,
                requiresGroundedNavigation: true,
                followManualSteps: false,
                reason: "全页面录制只允许访问当前页、录制起始页或 snapshot 真实发现的同源菜单地址；不能猜测路由。",
                navigationCoverage: this.navigationCoverage()
              };
            }
          }
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
          this.markNavigationTargetVisited(command.url);
          this.resetActionGuard();
          return { url: this.currentPage().url(), title: await this.withTimeout(this.currentPage().title(), 1_200, ""), pageError: this.pageError };
        case "next-page": {
          const currentSnapshot = await this.actions.captureSnapshot();
          const currentKey = this.navigationKey(String(currentSnapshot.url || page.url()), page.url());
          const currentRequirement = this.active?.pageOperationRequirements.get(currentKey);
          if (this.active?.session.completeFieldCoverage) {
            await this.ensureFormScope();
            const eligibleFields = (currentSnapshot.formFields || []).filter(field => !field.skip && !field.disabled);
            const todoFields = this.active.guard.wholeFormExercised
              ? (currentSnapshot.todoFields || []).filter(field => !field.skip && !field.disabled)
              : eligibleFields;
            if (todoFields.length) {
              return {
                ok: false,
                requiresCurrentPageCompletion: true,
                followManualSteps: false,
                reason: `当前页面还有 ${todoFields.length} 个业务字段未完成，不能跳到下一页。`,
                todoFields,
                navigationCoverage: this.navigationCoverage()
              };
            }
          }
          if (currentRequirement?.operations.size) {
            const readiness = await this.stopReadiness();
            const missing = readiness.missingPageOperations.find(item => this.navigationKey(item.url) === currentKey);
            if (missing) {
              return {
                ok: false,
                requiresCurrentPageCompletion: true,
                followManualSteps: false,
                reason: `当前页面还缺少${missing.operations.map(operation => OPERATION_LABEL[operation] || operation).join("、")}的业务成功响应，不能跳到下一页。`,
                missingOperations: missing.operations,
                navigationCoverage: this.navigationCoverage()
              };
            }
          }
          const next = this.navigationCoverage().unvisited[0];
          if (!next) return { ok: true, done: true, navigationCoverage: this.navigationCoverage() };
          try {
            await page.goto(next.url, { waitUntil: "domcontentloaded" });
            await this.waitForPageQuiet(page);
            if (await this.pageLooksFailed(page)) this.pageError = `无法打开页面：${next.url}`;
            else {
              this.setFocused(page);
              this.lastGoodUrl = page.url();
              this.pageError = undefined;
              this.markNavigationTargetVisited(next.url);
            }
          } catch {
            this.pageError = `无法打开页面：${next.url}`;
          }
          this.resetActionGuard();
          const snapshot = await this.actions.captureSnapshot();
          return { ok: !this.pageError, done: false, target: next, pageError: this.pageError, snapshot, navigationCoverage: this.navigationCoverage() };
        }
        case "click": {
          if (!command.selector) throw new Error("click requires selector");
          {
            const required = await this.requireWholeFormBeforeDirectFieldAction(command.selector);
            if (required) {
              watch?.stop();
              return required;
            }
          }
          try {
            return await this.guardedPageAction("click", command.selector, async () => {
              const clicked = await this.actions.click(command.selector!);
              await this.followAfterGesture(page, known, watch?.appeared || []);
              this.markNavigationTargetVisited(command.selector!);
              return { ...clicked, url: this.currentPage().url(), pageError: this.pageError };
            });
          } finally {
            watch?.stop();
          }
        }
        case "fill":
          if (!command.selector || typeof command.value !== "string") throw new Error("fill requires selector and string value");
          {
            const required = await this.requireWholeFormBeforeDirectFieldAction(command.selector);
            if (required) return required;
          }
          return this.guardedPageAction("fill", command.selector, async () => {
            await this.actions.fillField(command.selector!, command.value as string);
            await this.actions.recordFormInventory().catch(() => {});
            return { ok: true };
          });
        case "choose":
          if (!command.selector || command.value === undefined) throw new Error("choose requires selector and value");
          {
            const required = await this.requireWholeFormBeforeDirectFieldAction(command.selector);
            if (required) return required;
          }
          return this.guardedPageAction("choose", command.selector, async () => {
            const result = await this.actions.chooseOption(command.selector!, command.value!);
            await this.waitForPageQuiet(page);
            await this.actions.recordFormInventory().catch(() => {});
            return result;
          });
        case "select":
          if (!command.selector || command.value === undefined) throw new Error("select requires selector and value");
          {
            const required = await this.requireWholeFormBeforeDirectFieldAction(command.selector);
            if (required) return required;
          }
          return this.guardedPageAction("select", command.selector, async () => {
            try {
              await (await this.actions.locate(command.selector!)).selectOption(command.value!, { timeout: 1_500 });
              await this.waitForPageQuiet(page);
              await this.actions.recordFormInventory().catch(() => {});
              return { ok: true };
            } catch {
              const result = await this.actions.chooseOption(command.selector!, command.value!);
              await this.waitForPageQuiet(page);
              await this.actions.recordFormInventory().catch(() => {});
              return result;
            }
          });
        case "press":
          if (!command.selector || !command.key) throw new Error("press requires selector and key");
          {
            const required = await this.requireWholeFormBeforeDirectFieldAction(command.selector);
            if (required) return required;
          }
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
          const exercised = await this.guardedFormAction("exercise-form", () => this.actions.exerciseForm(Boolean(this.active?.session.completeFieldCoverage)));
          if (exercised.ok) await this.markWholeFormExercised();
          return exercised;
        }
        case "submit-form": {
          return this.guardedFormAction("submit-form", () => this.actions.submitForm());
        }
      }
    });
    if (result && typeof result === "object" && (result as { loginRequired?: boolean }).loginRequired) return result;
    const login = await this.loginPageState();
    if (login.detected) return this.loginPauseResult(login);
    if (command.action === "click") await this.currentPage().waitForTimeout(80);
    if (LOGIN_BLOCKED_ACTIONS.has(command.action)) await this.drainNetwork(600);
    const responseLogin = this.active?.authenticationFailure;
    if (LOGIN_BLOCKED_ACTIONS.has(command.action) && responseLogin && responseLogin.at >= actionStartedAt) {
      return this.loginPauseResult({
        detected: true,
        reason: `检测到登录状态失效（${responseLogin.reason}），已暂停自动操作。请在内置浏览器完成登录后点击“我已完成，继续自动执行”。`,
        pageUrl: responseLogin.pageUrl || this.currentPage().url()
      });
    }
    if (command.action === "click") this.watchLayerPaint();
    if (!LOGIN_BLOCKED_ACTIONS.has(command.action)) return result;
    const resultObject = result && typeof result === "object" ? result as Record<string, unknown> : { result };
    const knownSnapshot = command.action === "next-page"
      ? resultObject.snapshot as PageSnapshot | undefined
      : undefined;
    return {
      ...resultObject,
      recordingAudit: await this.recordingAudit(0, knownSnapshot)
    };
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
    this.screenshotInFlight = undefined;
    this.actionBusy = 0;
    this.layerHotUntil = 0;
    this.manualPointer = undefined;
    clearTimeout(this.inventoryTimer);
    if (!active) return Promise.resolve();
    return (async () => {
      await saveSessionCookies(this.config.profileDir, await active.context.cookies()).catch(() => {});
      await Promise.all([
        active.context.close().catch(() => {}),
        active.browser?.close().catch(() => {})
      ]);
    })();
  }

  async disposeAndKill(_reason = "dispose") {
    const closing = this.disposeImmediate();
    await Promise.race([
      closing,
      new Promise<void>(resolve => setTimeout(resolve, 1_500))
    ]);
    await this.killBrowserProcess();
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
    this.screenshotInFlight = undefined;
    this.actionBusy = 0;
    this.layerHotUntil = 0;
    this.manualPointer = undefined;
    clearTimeout(this.inventoryTimer);

    try {
      await this.drainNetwork();
      active.session.stoppedAt = new Date().toISOString();
      await this.writeManualStepsFile(active);
      const dir = path.dirname(active.eventsFile);
      await writeJson(path.join(dir, "session.json"), active.session);
      if (!active.externalBrowser) {
        await saveSessionCookies(this.config.profileDir, await active.context.cookies()).catch(() => {});
        await Promise.race([
          Promise.all([
            active.context.close().catch(() => {}),
            active.browser?.close().catch(() => {})
          ]),
          new Promise<void>(resolve => setTimeout(resolve, 1_500))
        ]);
      }
      return active.session;
    } finally {
      if (!active.externalBrowser) await this.killBrowserProcess();
    }
  }

  private captureBrowserPid(context: BrowserContext) {
    this.browserLaunched = true;
    try {
      this.browserPid = (context.browser() as Browser & { process?: () => { pid?: number } })?.process?.()?.pid;
    } catch {
      this.browserPid = undefined;
    }
  }

  private async killBrowserProcess() {
    const pid = this.browserPid;
    const launched = this.browserLaunched;
    this.browserPid = undefined;
    this.browserLaunched = false;
    if (pid) await killProcessTree(pid);
    if (launched) await killCommandLineMatches(this.config.profileDir);
    if (launched) await releaseChromiumDebugLog(this.config.profileDir);
  }
}
