import * as fs from "node:fs/promises";
import * as path from "node:path";
import { randomUUID } from "node:crypto";
import type { RpcUploadedFileRef } from "./types.js";
import type { UploadConfig } from "./types.js";

export type UploadState = "draft" | "reading" | "referenced" | "orphaned";

export interface UploadRegistryConfig extends UploadConfig {
  now?: () => number;
}

export interface UploadMetadata {
  ownerClientId?: string;
  sessionId?: string;
  correlationId?: string;
}

export interface StoredUpload extends RpcUploadedFileRef {
  state: UploadState;
  createdAt: number;
  lastAccessedAt: number;
  ownerClientId?: string;
  sessionId?: string;
  correlationId?: string;
  refCount: number;
}

const UUID =
  "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const UPLOAD_FILE_RE = new RegExp(
  `^upload-(${UUID})(\\.[a-z0-9][a-z0-9._-]*)$`,
  "i",
);
const UPLOAD_PART_RE = new RegExp(`^upload-(${UUID})\\.part$`, "i");

export class UploadRegistry {
  private readonly uploads = new Map<string, StoredUpload>();
  private readonly uploadDir: string;
  private readonly now: () => number;

  constructor(private readonly config: UploadRegistryConfig) {
    this.uploadDir = path.resolve(config.uploadDir);
    this.now = config.now ?? Date.now;
  }

  async initialize(): Promise<void> {
    await fs.mkdir(this.uploadDir, { recursive: true });
    await this.scanUploadDir();
  }

  createFilePath(mimeType: string): {
    id: string;
    filePath: string;
    partPath: string;
  } {
    const id = randomUUID();
    const extension = extensionForMimeType(mimeType);
    const filePath = path.join(this.uploadDir, `upload-${id}${extension}`);
    return {
      id,
      filePath,
      partPath: path.join(this.uploadDir, `upload-${id}.part`),
    };
  }

  register(
    ref: RpcUploadedFileRef,
    metadata: UploadMetadata = {},
  ): StoredUpload {
    const filePath = path.resolve(ref.path);
    this.assertManagedPath(filePath);
    const now = this.now();
    const stored: StoredUpload = {
      ...ref,
      path: filePath,
      state: "draft",
      createdAt: now,
      lastAccessedAt: now,
      refCount: 1,
      ownerClientId: metadata.ownerClientId,
      sessionId: metadata.sessionId,
      correlationId: metadata.correlationId,
    };
    this.uploads.set(stored.id, stored);
    return stored;
  }

  resolve(ref: Pick<RpcUploadedFileRef, "id" | "path">): StoredUpload | null {
    const upload = this.uploads.get(ref.id);
    if (!upload || path.resolve(upload.path) !== path.resolve(ref.path)) {
      return null;
    }
    return upload;
  }

  touch(id: string): StoredUpload | null {
    const upload = this.uploads.get(id);
    if (!upload) return null;
    upload.lastAccessedAt = this.now();
    return upload;
  }

  markDraft(id: string): StoredUpload | null {
    return this.mark(id, "draft");
  }

  markReading(id: string): StoredUpload | null {
    return this.mark(id, "reading");
  }

  markReferenced(
    id: string,
    metadata: Pick<UploadMetadata, "sessionId" | "correlationId"> = {},
  ): StoredUpload | null {
    const upload = this.mark(id, "referenced");
    if (!upload) return null;
    upload.sessionId = metadata.sessionId;
    upload.correlationId = metadata.correlationId;
    return upload;
  }

  markOrphaned(id: string): StoredUpload | null {
    return this.mark(id, "orphaned");
  }

  markClientDraftsOrphaned(clientId: string): number {
    let count = 0;
    for (const upload of this.uploads.values()) {
      if (upload.ownerClientId === clientId && upload.state === "draft") {
        this.mark(upload.id, "orphaned");
        count++;
      }
    }
    return count;
  }

  async deleteUpload(id: string): Promise<void> {
    const upload = this.uploads.get(id);
    if (!upload) return;
    await this.remove(upload);
  }

  async scanUploadDir(): Promise<void> {
    await fs.mkdir(this.uploadDir, { recursive: true });
    const entries = await fs.readdir(this.uploadDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const filePath = path.join(this.uploadDir, entry.name);
      const partMatch = UPLOAD_PART_RE.exec(entry.name);
      if (partMatch) {
        await this.removeExpiredPath(filePath, this.config.orphanedTtlMs);
        continue;
      }

      const fileMatch = UPLOAD_FILE_RE.exec(entry.name);
      if (!fileMatch) continue;
      const [, id] = fileMatch;
      if (!id || this.uploads.has(id)) continue;

      const stats = await fs.stat(filePath);
      if (this.isExpired(stats.mtimeMs, this.config.orphanedTtlMs)) {
        await fs.rm(filePath, { force: true });
        continue;
      }

      const now = this.now();
      this.uploads.set(id, {
        id,
        name: entry.name,
        size: stats.size,
        mimeType: "application/octet-stream",
        path: path.resolve(filePath),
        previewUrl: `/api/uploads/${encodeURIComponent(id)}/preview`,
        state: "orphaned",
        createdAt: stats.mtimeMs || now,
        lastAccessedAt: stats.mtimeMs || now,
        refCount: 0,
      });
    }
  }

  async cleanupExpiredUploads(): Promise<number> {
    let removed = 0;
    for (const upload of [...this.uploads.values()]) {
      if (!this.canCleanup(upload)) continue;
      await this.remove(upload);
      removed++;
    }
    return removed;
  }

  async cleanupBeforeUpload(incomingSize: number): Promise<boolean> {
    await this.scanUploadDir();
    for (const upload of [...this.uploads.values()]) {
      if (upload.state === "orphaned") await this.remove(upload);
    }
    await this.cleanupExpiredUploads();
    return this.getTotalBytes() + incomingSize <= this.config.maxTotalBytes;
  }

  getTotalBytes(): number {
    let total = 0;
    for (const upload of this.uploads.values()) total += upload.size;
    return total;
  }

  async dispose(): Promise<void> {
    for (const upload of [...this.uploads.values()]) {
      if (upload.path.endsWith(".part")) {
        await this.remove(upload);
      }
    }
    this.uploads.clear();
  }

  private mark(id: string, state: UploadState): StoredUpload | null {
    const upload = this.uploads.get(id);
    if (!upload) return null;
    upload.state = state;
    upload.lastAccessedAt = this.now();
    return upload;
  }

  private canCleanup(upload: StoredUpload): boolean {
    if (upload.state === "reading") return false;
    if (upload.state === "draft") {
      return this.isExpired(upload.lastAccessedAt, this.config.draftTtlMs);
    }
    if (upload.state === "referenced") {
      return this.isExpired(upload.lastAccessedAt, this.config.referencedTtlMs);
    }
    return this.isExpired(upload.lastAccessedAt, this.config.orphanedTtlMs);
  }

  private async remove(upload: StoredUpload): Promise<void> {
    this.uploads.delete(upload.id);
    await fs.rm(upload.path, { force: true });
  }

  private async removeExpiredPath(filePath: string, ttlMs: number): Promise<void> {
    const stats = await fs.stat(filePath);
    if (this.isExpired(stats.mtimeMs, ttlMs)) {
      await fs.rm(filePath, { force: true });
    }
  }

  private isExpired(timestamp: number, ttlMs: number): boolean {
    return this.now() - timestamp > ttlMs;
  }

  private assertManagedPath(filePath: string): void {
    const resolved = path.resolve(filePath);
    if (
      path.dirname(resolved) !== this.uploadDir ||
      !UPLOAD_FILE_RE.test(path.basename(resolved))
    ) {
      throw new Error("Upload path must be a Dano-managed file inside uploadDir");
    }
  }
}

function extensionForMimeType(mimeType: string): string {
  switch (mimeType) {
    case "image/png":
      return ".png";
    case "image/jpeg":
      return ".jpg";
    case "image/gif":
      return ".gif";
    case "image/webp":
      return ".webp";
    default:
      return ".bin";
  }
}
