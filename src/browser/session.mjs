import { headersFromStorageState, mergePackedHeaders, writeAuthVault } from "../auth-vault.mjs";
import { originFromUrl, saveStorageState } from "../session-store.mjs";
import { attachCredentialCapture, attachNetwork, latestCredentialHeaders } from "./network.mjs";

const sessions = new Map();

export const DESKTOP_VIEWPORT = { width: 1440, height: 900 };

export function desktopViewport(raw) {
  const width = Math.round(Number(raw?.width) || 0);
  const height = Math.round(Number(raw?.height) || 0);
  if (width >= 1280 && height >= 720) return { width, height };
  return { ...DESKTOP_VIEWPORT };
}

export function browserSession(recordingId) {
  return sessions.get(recordingId) || null;
}

export function sameDocumentNav(current, next) {
  try {
    const a = new URL(current);
    const b = new URL(next);
    if (a.origin !== b.origin || a.pathname !== b.pathname || a.search !== b.search) return false;
    return a.hash !== b.hash;
  } catch {
    return false;
  }
}

export async function openBrowser({ recordingId, url, storageState, viewport }) {
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: true });
  const size = desktopViewport(viewport);
  const scale = 1;
  const contextOptions = { viewport: size, deviceScaleFactor: scale };
  if (storageState) contextOptions.storageState = storageState;
  const context = await browser.newContext(contextOptions);
  attachNetwork(recordingId, context);
  const page = await context.newPage();
  attachCredentialCapture(recordingId, context, page);
  const state = {
    browser, context, page, viewport: size,
    origin: originFromUrl(url),
    epoch: 1,
    refs: new Map(),
    lastMainUrl: page.url(),
    lastPointerMoveAt: 0,
    actionSeq: 0,
  };
  page.on("filechooser", (chooser) => {
    if (state.agentClick) state.pendingFileChooser = chooser;
    else state.pendingFileChooser = chooser;
  });
  page.on("framenavigated", (frame) => {
    if (frame !== page.mainFrame()) return;
    const next = frame.url();
    const prev = state.lastMainUrl || page.url();
    state.lastMainUrl = next;
    if (!sameDocumentNav(prev, next)) {
      state.epoch += 1;
      state.refs.clear();
    }
    clearTimeout(state.persistTimer);
    state.persistTimer = setTimeout(() => {
      persistBrowserSession(recordingId).catch(() => {});
    }, 500);
  });
  sessions.set(recordingId, state);
  if (url) await page.goto(url, { waitUntil: "domcontentloaded" });
  return state;
}

export function getPage(recordingId) {
  return sessions.get(recordingId)?.page || null;
}

const pageLocks = new Map();
export const PAGE_LOCK_MS = 15000;

export function raceWall(promise, ms, fallback) {
  let timer;
  const raced = Promise.race([
    Promise.resolve(promise).then(
      (value) => ({ done: true, value }),
      (error) => ({ done: true, error }),
    ).finally(() => clearTimeout(timer)),
    new Promise((resolve) => {
      timer = setTimeout(() => resolve({ done: false }), Math.max(1, Number(ms) || PAGE_LOCK_MS));
    }),
  ]);
  return raced.then((hit) => {
    if (hit?.done) {
      if (hit.error) throw hit.error;
      return hit.value;
    }
    return typeof fallback === "function" ? fallback() : fallback;
  });
}

export function withPage(recordingId, fn, { lockMs = PAGE_LOCK_MS } = {}) {
  const prev = pageLocks.get(recordingId) || Promise.resolve();
  const run = prev.then(() => fn(), () => fn());
  let timer;
  const hold = Promise.race([
    run.then(() => {}, () => {}).finally(() => clearTimeout(timer)),
    new Promise((resolve) => {
      timer = setTimeout(resolve, Math.max(1, Number(lockMs) || PAGE_LOCK_MS));
    }),
  ]);
  pageLocks.set(recordingId, hold);
  return run;
}

export function withoutPageLock(recordingId, fn) {
  return Promise.resolve().then(fn);
}

export async function persistBrowserSession(recordingId) {
  const state = sessions.get(recordingId);
  if (!state?.context) return null;
  try {
    const saved = await state.context.storageState();
    const url = state.page?.url?.() || state.origin;
    const headers = mergePackedHeaders(headersFromStorageState(saved), latestCredentialHeaders(recordingId));
    if (Object.keys(headers).length) await writeAuthVault(recordingId, headers);
    return await saveStorageState(url, saved);
  } catch {
    return null;
  }
}

export async function closeBrowser(recordingId) {
  const state = sessions.get(recordingId);
  if (!state) return;
  clearTimeout(state.persistTimer);
  await persistBrowserSession(recordingId);
  sessions.delete(recordingId);
  await state.browser.close().catch(() => {});
}

export async function captureFrame(recordingId) {
  const page = getPage(recordingId);
  if (!page) return null;
  const state = sessions.get(recordingId);
  if (state.capturing) return null;
  state.capturing = true;
  try {
    const bytes = await page.screenshot({ type: "jpeg", quality: 92 });
    const size = page.viewportSize() || state.viewport;
    return {
      data: Buffer.from(bytes).toString("base64"),
      width: size.width,
      height: size.height,
    };
  } catch {
    return null;
  } finally {
    state.capturing = false;
  }
}
