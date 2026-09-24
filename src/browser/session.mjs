import { originFromUrl } from "../session-store.mjs";
import { attachNetwork } from "./network.mjs";

const sessions = new Map();

export function browserSession(recordingId) {
  return sessions.get(recordingId) || null;
}

export async function openBrowser({ recordingId, url, storageState, viewport }) {
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: true });
  const size = viewport?.width && viewport?.height ? viewport : { width: 1440, height: 900 };
  const contextOptions = { viewport: size };
  if (storageState) contextOptions.storageState = storageState;
  const context = await browser.newContext(contextOptions);
  attachNetwork(recordingId, context);
  const page = await context.newPage();
  const state = {
    browser, context, page, viewport: size,
    origin: originFromUrl(url),
    epoch: 1,
    refs: new Map(),
    lastPointerMoveAt: 0,
    actionSeq: 0,
  };
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) {
      state.epoch += 1;
      state.refs.clear();
    }
  });
  sessions.set(recordingId, state);
  if (url) await page.goto(url, { waitUntil: "domcontentloaded" });
  return state;
}

export function getPage(recordingId) {
  return sessions.get(recordingId)?.page || null;
}

export async function closeBrowser(recordingId) {
  const state = sessions.get(recordingId);
  if (!state) return;
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
    const bytes = await page.screenshot({ type: "jpeg", quality: 60 });
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
