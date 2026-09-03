import test from "node:test";
import assert from "node:assert/strict";
import { normalizePreviewViewport } from "../src/browser/recorder.js";

test("preview viewport follows the workbench pane without stretching past safe bounds", () => {
  assert.deepEqual(normalizePreviewViewport(), { width: 1440, height: 960 });
  assert.deepEqual(normalizePreviewViewport({ width: 1600, height: 900 }), { width: 1600, height: 900 });
  assert.deepEqual(normalizePreviewViewport({ width: 400, height: 200 }), { width: 640, height: 400 });
  assert.deepEqual(normalizePreviewViewport({ width: 4000, height: 3000 }), { width: 1920, height: 1200 });
});
