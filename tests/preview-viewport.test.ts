import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { isUsablePreviewBuffer, normalizePreviewScale, normalizePreviewViewport, readJpegDimensions } from "../src/browser/recorder.js";

const root = path.resolve(import.meta.dirname, "..");

test("page viewport stays at a desktop size and ignores a small preview pane", () => {
  assert.deepEqual(normalizePreviewViewport(), { width: 1440, height: 960, scale: 1 });
  assert.deepEqual(normalizePreviewViewport({ width: 1600, height: 900 }), { width: 1600, height: 900, scale: 1 });
  assert.deepEqual(normalizePreviewViewport({ width: 500, height: 400 }), { width: 1440, height: 960, scale: 1 });
  assert.deepEqual(normalizePreviewViewport({ width: 1100, height: 700 }), { width: 1440, height: 960, scale: 1 });
  assert.deepEqual(normalizePreviewViewport({ width: 1800, height: 720, scale: 1.5 }), { width: 1440, height: 960, scale: 1 });
  assert.equal(normalizePreviewScale(1.25), 1.25);
  assert.equal(normalizePreviewScale(3), 2);
  const large = normalizePreviewViewport({ width: 5000, height: 4000 });
  assert.ok(large.width <= 3840 && large.height <= 2160);
  assert.ok(large.width >= 1440 && large.height >= 900);
});

test("host pixel ratio must not zoom the recorded page", () => {
  assert.deepEqual(
    normalizePreviewViewport({ width: 1600, height: 900, scale: 1.25 }),
    { width: 1600, height: 900, scale: 1 }
  );
  assert.deepEqual(
    normalizePreviewViewport({ width: 1548, height: 988, scale: 2 }),
    { width: 1548, height: 988, scale: 1 }
  );
});

test("recording page uses a desktop viewport instead of the preview pane", async () => {
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
  assert.match(server, /scale/);
  const viewportRoute = server.match(/pathname === "\/api\/browser\/viewport"[\s\S]*?return;/)?.[0] || "";
  assert.match(viewportRoute, /rememberViewport/);
  assert.doesNotMatch(viewportRoute, /browser_changed/);
  assert.match(app, /function rememberPaneViewport\(/);
  assert.match(app, /scale:\s*1/);
  assert.doesNotMatch(app, /devicePixelRatio/);
  assert.match(app, /browser_changed[\s\S]*rememberPaneViewport/);
  assert.doesNotMatch(app, /\/api\/browser\/viewport/);
  assert.doesNotMatch(app, /viewport:\s*previewPaneSize\(\)/);
  assert.match(recorder, /quality:\s*82/);
  assert.match(recorder, /scale:\s*"css"/);
  assert.match(recorder, /deviceScaleFactor:\s*1/);
  assert.doesNotMatch(recorder, /deviceScaleFactor:\s*size\.scale/);
});

function jpegStub(width: number, height: number, bytes = 900) {
  const buffer = Buffer.alloc(bytes, 0);
  buffer[0] = 0xff;
  buffer[1] = 0xd8;
  buffer[2] = 0xff;
  buffer[3] = 0xc0;
  buffer.writeUInt16BE(11, 4);
  buffer[6] = 8;
  buffer.writeUInt16BE(height, 7);
  buffer.writeUInt16BE(width, 9);
  buffer[11] = 1;
  return buffer;
}

test("tiny or 1x1 preview JPEGs are rejected so the last good frame stays", () => {
  const empty = Buffer.from(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wAAAAF/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9k=",
    "base64"
  );
  assert.equal(isUsablePreviewBuffer(undefined), false);
  assert.equal(isUsablePreviewBuffer(Buffer.alloc(100)), false);
  assert.equal(isUsablePreviewBuffer(empty), false);
  assert.deepEqual(readJpegDimensions(jpegStub(1, 1)), { width: 1, height: 1 });
  assert.equal(isUsablePreviewBuffer(jpegStub(1, 1, 900)), false);
  assert.equal(isUsablePreviewBuffer(jpegStub(1440, 960, 900)), true);
});

test("failed or overlay-time captures keep the last preview instead of a black placeholder", async () => {
  const [recorder, server, app] = await Promise.all([
    readFile(path.join(root, "src", "browser", "recorder.ts"), "utf8"),
    readFile(path.join(root, "src", "web", "server.ts"), "utf8"),
    readFile(path.join(root, "web", "app.js"), "utf8")
  ]);
  const capture = recorder.match(/private async capturePreview\([\s\S]*?^  private /m)?.[0] || "";
  const shot = recorder.match(/private takePreviewScreenshot\([\s\S]*?^  private /m)?.[0] || "";
  const withAction = recorder.match(/private async withAction[\s\S]*?^  private /m)?.[0] || "";
  const watchLayer = recorder.match(/private watchLayerPaint\([\s\S]*?^  private /m)?.[0] || "";
  assert.match(shot, /animations:\s*"allow"/);
  assert.doesNotMatch(shot, /EMPTY_JPEG|animations:\s*"disabled"/);
  assert.doesNotMatch(capture, /EMPTY_JPEG|animations:\s*"disabled"/);
  assert.match(capture, /throw new Error\("preview unavailable"\)/);
  assert.doesNotMatch(withAction, /lastPreview = undefined/);
  assert.doesNotMatch(watchLayer, /lastPreview = undefined/);
  assert.match(recorder, /screenshotInFlight/);
  assert.match(server, /isUsablePreviewBuffer/);
  assert.match(app, /blob\.size < 800/);
  assert.match(app, /naturalWidth < 200/);
  assert.match(app, /AbortSignal\.timeout\(2500\)/);
});
