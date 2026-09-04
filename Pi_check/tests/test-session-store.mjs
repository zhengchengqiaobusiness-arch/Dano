/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  isLoginUrl,
  looksLoggedIn,
  originFromUrl,
  playwrightStateFromTokens,
} from "../src/session-store.mjs";
import { FRAME_JPEG_QUALITY, frameScreenshotOptions, normalizeDeviceScale, normalizeViewport, shouldFlushFrame, shouldRecordConsole, shouldStoreResponseBody } from "../src/browser-capture.mjs";

test("登录态按站点 origin 判断，不打印凭据", () => {
  assert.equal(originFromUrl("http://admin.example:90/oa/x"), "http://admin.example:90");
  assert.equal(isLoginUrl("http://admin.example:90/login?redirect=/oa"), true);
  assert.equal(isLoginUrl("http://admin.example:90/oa/common/hotel-apply"), false);
  const empty = { cookies: [], origins: [] };
  assert.equal(looksLoggedIn(empty), false);
  const state = playwrightStateFromTokens("http://admin.example:90", {
    accessToken: "token-value",
    refreshToken: "refresh-value",
    tenantId: "1",
  });
  assert.equal(looksLoggedIn(state), true);
  assert.deepEqual(state.origins[0].localStorage.map((row) => row.name), [
    "ACCESS_TOKEN",
    "REFRESH_TOKEN",
    "tenantId",
  ]);
});

test("静态资源和调试 console 不落盘，业务请求仍保存", () => {
  assert.equal(shouldStoreResponseBody("stylesheet"), false);
  assert.equal(shouldStoreResponseBody("image"), false);
  assert.equal(shouldStoreResponseBody("script"), false);
  assert.equal(shouldStoreResponseBody("xhr"), true);
  assert.equal(shouldStoreResponseBody("fetch"), true);
  assert.equal(shouldStoreResponseBody("document"), true);
  assert.equal(shouldRecordConsole("error"), true);
  assert.equal(shouldRecordConsole("warning"), true);
  assert.equal(shouldRecordConsole("log"), false);
  assert.equal(shouldRecordConsole("info"), false);
  assert.equal(shouldFlushFrame({ kind: "pointer_up" }), true);
  assert.equal(shouldFlushFrame({ kind: "key", key: "Enter" }), true);
  assert.equal(shouldFlushFrame({ kind: "pointer_move" }), false);
  assert.ok(FRAME_JPEG_QUALITY >= 85);
  assert.deepEqual(normalizeViewport({ width: 1600, height: 720 }), { width: 1600, height: 720 });
  assert.equal(normalizeViewport({ width: 100, height: 100 }), null);
  assert.equal(normalizeDeviceScale({ devicePixelRatio: 1 }), 1);
  assert.equal(normalizeDeviceScale({ devicePixelRatio: 1.5 }), 1.5);
  assert.equal(normalizeDeviceScale({ devicePixelRatio: 3 }), 1.5);
  assert.equal(frameScreenshotOptions(1).scale, "css");
  assert.equal(frameScreenshotOptions(1.5).scale, "device");
  assert.equal(frameScreenshotOptions(1.5).quality, FRAME_JPEG_QUALITY);
});
