import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { normalizePreviewViewport } from "../src/browser/recorder.js";

const root = path.resolve(import.meta.dirname, "..");

test("preview viewport follows the workbench pane without stretching past safe bounds", () => {
  assert.deepEqual(normalizePreviewViewport(), { width: 1440, height: 960 });
  assert.deepEqual(normalizePreviewViewport({ width: 1600, height: 900 }), { width: 1600, height: 900 });
  assert.deepEqual(normalizePreviewViewport({ width: 1800, height: 720 }), { width: 1800, height: 720 });
  assert.deepEqual(normalizePreviewViewport({ width: 500, height: 400 }), { width: 500, height: 400 });
  const large = normalizePreviewViewport({ width: 4000, height: 3000 });
  assert.ok(large.width <= 2560 && large.height <= 1600);
  assert.ok(Math.abs(large.width / large.height - 4000 / 3000) < 0.02);
});

test("session auto-open and address-bar open share the same remembered pane size", async () => {
  const [server, page, app, recorder] = await Promise.all([
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "workbench-page.ts"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8"),
    readFile(path.join(root, "src", "browser", "recorder.ts"), "utf8")
  ]);
  assert.match(page, /preferredViewport/);
  assert.match(page, /async rememberViewport\(/);
  assert.match(page, /viewport \|\| this\.preferredViewport/);
  assert.match(server, /parseViewport\(body\.viewport\) \|\| page\.preferredViewport/);
  assert.match(server, /pathname === "\/internal\/browser\/start"/);
  const viewportRoute = server.match(/pathname === "\/api\/browser\/viewport"[\s\S]*?return;/)?.[0] || "";
  assert.match(viewportRoute, /rememberViewport/);
  assert.doesNotMatch(viewportRoute, /browser_changed/);
  assert.match(app, /function rememberPaneViewport\(/);
  assert.match(app, /browser_changed[\s\S]*rememberPaneViewport/);
  assert.match(recorder, /quality:\s*62/);
});
