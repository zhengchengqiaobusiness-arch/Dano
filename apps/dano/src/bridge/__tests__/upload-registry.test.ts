import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { UploadRegistry } from "../upload-registry.js";

const DAY = 24 * 60 * 60 * 1000;

let tmpDir: string;
let now: number;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-upload-registry-"));
  now = Date.UTC(2026, 0, 1);
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function registry(maxTotalBytes = 1024): UploadRegistry {
  return new UploadRegistry({
    uploadDir: tmpDir,
    maxTotalBytes,
    draftTtlMs: DAY,
    referencedTtlMs: DAY * 2,
    orphanedTtlMs: 5 * 60 * 1000,
    cleanupIntervalMs: 60 * 60 * 1000,
    now: () => now,
  });
}

function writeUpload(
  uploads: UploadRegistry,
  size: number,
  ownerClientId = "client_1",
) {
  const { id, filePath } = uploads.createFilePath("image/png");
  fs.writeFileSync(filePath, Buffer.alloc(size));
  return uploads.register(
    {
      id,
      name: "sample.png",
      size,
      mimeType: "image/png",
      path: filePath,
      previewUrl: `/api/uploads/${encodeURIComponent(id)}/preview`,
    },
    { ownerClientId },
  );
}

describe("UploadRegistry", () => {
  it("registers managed paths and rejects mismatched resolve paths", async () => {
    const uploads = registry();
    await uploads.initialize();
    const upload = writeUpload(uploads, 4);

    expect(path.dirname(upload.path)).toBe(tmpDir);
    expect(path.basename(upload.path)).toMatch(/^upload-[0-9a-f-]+\.png$/);
    expect(uploads.resolve({ id: upload.id, path: upload.path })).toBe(upload);
    expect(
      uploads.resolve({ id: upload.id, path: path.join(tmpDir, "other.png") }),
    ).toBeNull();
  });

  it("removes expired orphaned uploads", async () => {
    const uploads = registry();
    await uploads.initialize();
    const upload = writeUpload(uploads, 3);
    uploads.markOrphaned(upload.id);
    now += 5 * 60 * 1000 + 1;

    await expect(uploads.cleanupExpiredUploads()).resolves.toBe(1);
    expect(fs.existsSync(upload.path)).toBe(false);
    expect(uploads.resolve(upload)).toBeNull();
  });

  it("keeps expired reading uploads because model reads must not lose files", async () => {
    const uploads = registry();
    await uploads.initialize();
    const upload = writeUpload(uploads, 3);
    uploads.markReading(upload.id);
    now += DAY * 3;

    await expect(uploads.cleanupExpiredUploads()).resolves.toBe(0);
    expect(fs.existsSync(upload.path)).toBe(true);
    expect(uploads.resolve(upload)?.state).toBe("reading");
  });

  it("uses referenced TTL separately from draft TTL", async () => {
    const uploads = registry();
    await uploads.initialize();
    const upload = writeUpload(uploads, 3);
    uploads.markReferenced(upload.id);
    now += DAY + 1;

    await expect(uploads.cleanupExpiredUploads()).resolves.toBe(0);
    expect(uploads.resolve(upload)?.state).toBe("referenced");

    now += DAY;
    await expect(uploads.cleanupExpiredUploads()).resolves.toBe(1);
    expect(fs.existsSync(upload.path)).toBe(false);
  });

  it("refuses cleanupBeforeUpload when active uploads already exceed capacity", async () => {
    const uploads = registry(5);
    await uploads.initialize();
    writeUpload(uploads, 6);

    await expect(uploads.cleanupBeforeUpload(1)).resolves.toBe(false);
  });

  it("deletes orphaned uploads before refusing a new upload", async () => {
    const uploads = registry(8);
    await uploads.initialize();
    const orphaned = writeUpload(uploads, 6);
    uploads.markOrphaned(orphaned.id);
    const active = writeUpload(uploads, 6);

    await expect(uploads.cleanupBeforeUpload(1)).resolves.toBe(true);
    expect(fs.existsSync(orphaned.path)).toBe(false);
    expect(fs.existsSync(active.path)).toBe(true);
    expect(uploads.resolve(active)?.state).toBe("draft");
  });

  it("marks client draft uploads orphaned without touching reading uploads", async () => {
    const uploads = registry();
    await uploads.initialize();
    const draft = writeUpload(uploads, 1, "client_1");
    const reading = writeUpload(uploads, 1, "client_1");
    uploads.markReading(reading.id);

    expect(uploads.markClientDraftsOrphaned("client_1")).toBe(1);
    expect(uploads.resolve(draft)?.state).toBe("orphaned");
    expect(uploads.resolve(reading)?.state).toBe("reading");
  });

  it("scans only managed upload files and adopts fresh files while deleting expired managed files", async () => {
    const freshId = "11111111-1111-4111-8111-111111111111";
    const expiredId = "22222222-2222-4222-8222-222222222222";
    const partId = "33333333-3333-4333-8333-333333333333";
    const freshPath = path.join(tmpDir, `upload-${freshId}.png`);
    const expiredPath = path.join(tmpDir, `upload-${expiredId}.jpg`);
    const partPath = path.join(tmpDir, `upload-${partId}.part`);
    const ignoredPath = path.join(tmpDir, "user-file.png");
    fs.writeFileSync(freshPath, Buffer.alloc(7));
    fs.writeFileSync(expiredPath, Buffer.alloc(5));
    fs.writeFileSync(partPath, Buffer.alloc(4));
    fs.writeFileSync(ignoredPath, Buffer.alloc(2));
    const old = new Date(now - 5 * 60 * 1000 - 1);
    fs.utimesSync(expiredPath, old, old);
    fs.utimesSync(partPath, old, old);

    const uploads = registry();
    await uploads.initialize();

    expect(uploads.resolve({ id: freshId, path: freshPath })?.state).toBe(
      "orphaned",
    );
    expect(fs.existsSync(expiredPath)).toBe(false);
    expect(fs.existsSync(partPath)).toBe(false);
    expect(fs.existsSync(ignoredPath)).toBe(true);
    expect(uploads.getTotalBytes()).toBe(7);
  });
});
