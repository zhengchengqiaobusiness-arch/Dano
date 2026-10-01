import * as fs from "node:fs/promises";
import * as fsSync from "node:fs";
import * as path from "node:path";
import { randomUUID } from "node:crypto";
import type { RpcUploadedFileRef } from "./types.js";
import type { UploadConfig } from "./types.js";

export type UploadState = "draft" | "reading" | "referenced" | "orphaned";

export interface UploadRegistryConfig extends UploadConfig {
  now?: () => number;
  requireOwnership?: boolean;
}

export interface UploadMetadata {
  ownerUserId?: string;
  ownerClientId?: string;
  workspacePath?: string;
  sessionId?: string;
  correlationId?: string;
}

export type UploadAccess = Pick<
  UploadMetadata,
  "ownerUserId" | "ownerClientId" | "workspacePath" | "sessionId"
> & { sourceSessionId?: string };

export interface StoredUpload extends RpcUploadedFileRef {
  state: UploadState;
  createdAt: number;
  lastAccessedAt: number;
  ownerUserId?: string;
  ownerClientId?: string;
  previousClientIds: string[];
  workspacePath?: string;
  sessionId?: string;
  correlationId?: string;
  refCount: number;
}

const UUID =
  "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const SHA256 = "[a-f0-9]{64}";
const UPLOAD_FILE_RE = new RegExp(`^(${SHA256})(\\.[a-z0-9][a-z0-9._-]*)?$`, "i");
const UPLOAD_PART_RE = new RegExp(
  `^(?:\\.(${SHA256})-${UUID}|\\.incoming-${UUID})\\.part$`,
  "i",
);
const UPLOAD_INDEX_VERSION = 1;
const UPLOAD_RECORDS_DIRECTORY = "records";
const UPLOAD_RECORD_RE = /^([A-Za-z0-9_-]+)\.json$/;

export function isManagedUploadFileName(name: string): boolean {
  return UPLOAD_FILE_RE.test(name);
}

export class UploadRegistry {
  private readonly uploads = new Map<string, StoredUpload>();
  private readonly uploadDir: string;
  private readonly recordsDirectoryPath: string;
  private readonly now: () => number;

  constructor(private readonly config: UploadRegistryConfig) {
    this.uploadDir = path.resolve(config.uploadDir);
    this.recordsDirectoryPath = path.join(
      this.uploadDir,
      UPLOAD_RECORDS_DIRECTORY,
    );
    this.now = config.now ?? Date.now;
  }

  async initialize(): Promise<void> {
    await fs.mkdir(this.uploadDir, { recursive: true });
    await fs.mkdir(this.recordsDirectoryPath, { recursive: true });
    await this.loadPersistedUploads();
    await this.scanUploadDir(this.uploadDir);
  }

  async createFilePath(workspacePath: string, hash: string, name: string): Promise<{
    id: string;
    filePath: string;
    partPath: string;
    relativePath: string;
  }> {
    const id = randomUUID();
    const uploadDir = this.workspaceUploadDir(workspacePath);
    await fs.mkdir(uploadDir, { recursive: true });
    const extension = extensionForName(name);
    const storageName = `${hash}${extension}`;
    const filePath = path.join(uploadDir, storageName);
    return {
      id,
      filePath,
      partPath: path.join(uploadDir, `.${hash}-${id}.part`),
      relativePath: path.posix.join("uploads", storageName),
    };
  }

  async createIncomingPartPath(workspacePath: string): Promise<{
    id: string;
    partPath: string;
  }> {
    const id = randomUUID();
    const uploadDir = this.workspaceUploadDir(workspacePath);
    await fs.mkdir(uploadDir, { recursive: true });
    return {
      id,
      partPath: path.join(uploadDir, `.incoming-${id}.part`),
    };
  }

  register(
    ref: RpcUploadedFileRef,
    metadata: UploadMetadata = {},
  ): StoredUpload {
    const filePath = path.resolve(ref.path);
    this.assertManagedPath(filePath);
    this.assertOwnershipMetadata(metadata, filePath);
    const now = this.now();
    const stored: StoredUpload = {
      ...ref,
      path: filePath,
      state: "draft",
      createdAt: now,
      lastAccessedAt: now,
      refCount: 1,
      ownerUserId: metadata.ownerUserId,
      ownerClientId: metadata.ownerClientId,
      previousClientIds: [],
      workspacePath: metadata.workspacePath
        ? path.resolve(metadata.workspacePath)
        : undefined,
      sessionId: metadata.sessionId,
      correlationId: metadata.correlationId,
    };
    this.uploads.set(stored.id, stored);
    this.persist(stored);
    return stored;
  }

  resolve(
    ref: Pick<RpcUploadedFileRef, "id" | "path">,
    access?: UploadAccess,
  ): StoredUpload | null {
    const upload = this.uploads.get(ref.id);
    if (!upload || path.resolve(upload.path) !== path.resolve(ref.path)) {
      return null;
    }
    if (!this.authorize(upload, access, true)) return null;
    return upload;
  }

  touch(
    id: string,
    access?: UploadAccess,
  ): StoredUpload | null {
    const upload = this.uploads.get(id);
    if (!upload || !this.authorize(upload, access, false)) return null;
    upload.lastAccessedAt = this.now();
    this.persist(upload);
    return upload;
  }

  peek(id: string): StoredUpload | null {
    return this.uploads.get(id) ?? null;
  }

  markDraft(id: string, access?: UploadAccess): StoredUpload | null {
    return this.markAuthorized(id, "draft", access);
  }

  markReading(id: string, access?: UploadAccess): StoredUpload | null {
    return this.markAuthorized(id, "reading", access);
  }

  markReferenced(
    id: string,
    metadata: UploadMetadata = {},
  ): StoredUpload | null {
    const upload = this.uploads.get(id);
    if (!upload || !this.authorize(upload, metadata, false)) return null;
    upload.state = "referenced";
    upload.lastAccessedAt = this.now();
    upload.sessionId = metadata.sessionId;
    upload.correlationId = metadata.correlationId;
    this.persist(upload);
    return upload;
  }

  markOrphaned(
    id: string,
    access?: UploadAccess,
  ): StoredUpload | null {
    const upload = this.uploads.get(id);
    if (!upload || !this.authorize(upload, access, false)) return null;
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

  async deleteUpload(id: string, access?: UploadAccess): Promise<boolean> {
    const upload = this.uploads.get(id);
    if (!upload || !this.authorize(upload, access, false)) return false;
    await this.remove(upload);
    return true;
  }

  async scanUploadDir(uploadDir = this.uploadDir): Promise<void> {
    await fs.mkdir(uploadDir, { recursive: true });
    const entries = await fs.readdir(uploadDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const filePath = path.join(uploadDir, entry.name);
      const partMatch = UPLOAD_PART_RE.exec(entry.name);
      if (partMatch) {
        await this.removeExpiredPath(filePath, this.config.orphanedTtlMs);
        continue;
      }

      const fileMatch = UPLOAD_FILE_RE.exec(entry.name);
      if (!fileMatch) continue;
      if (this.hasPath(filePath)) continue;

      const stats = await fs.stat(filePath);
      if (this.isExpired(stats.mtimeMs, this.config.orphanedTtlMs)) {
        await fs.rm(filePath, { force: true });
        continue;
      }

      if (this.config.requireOwnership) continue;

      const now = this.now();
      const id = randomUUID();
      const upload: StoredUpload = {
        id,
        name: entry.name,
        size: stats.size,
        mimeType: "application/octet-stream",
        path: path.resolve(filePath),
        relativePath: path.posix.join("uploads", entry.name),
        previewUrl: `/api/uploads/${encodeURIComponent(id)}/preview`,
        state: "orphaned",
        createdAt: stats.mtimeMs || now,
        lastAccessedAt: stats.mtimeMs || now,
        previousClientIds: [],
        refCount: 0,
      };
      this.uploads.set(id, upload);
      this.persist(upload);
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

  async cleanupBeforeUpload(
    incomingSize: number,
    workspacePath?: string,
  ): Promise<boolean> {
    if (workspacePath) await this.scanUploadDir(this.workspaceUploadDir(workspacePath));
    else await this.scanUploadDir();
    for (const upload of [...this.uploads.values()]) {
      if (
        upload.state === "orphaned" &&
        (!workspacePath || samePath(upload.workspacePath, workspacePath))
      ) {
        await this.remove(upload);
      }
    }
    await this.cleanupExpiredUploads();
    return this.getTotalBytes() + incomingSize <= this.config.maxTotalBytes;
  }

  transferOwnership(
    sourceUserId: string,
    targetUserId: string,
    mapPath: (candidatePath: string) => string,
  ): () => void {
    const originals = [...this.uploads.values()]
      .filter(upload => upload.ownerUserId === sourceUserId)
      .map(upload => ({ ...upload, previousClientIds: [...upload.previousClientIds] }));
    const rollback = () => {
      for (const upload of originals) {
        this.uploads.set(upload.id, upload);
        this.persist(upload);
      }
    };
    try {
      for (const original of originals) {
        const transferred: StoredUpload = {
          ...original,
          ownerUserId: targetUserId,
          path: mapPath(original.path),
          workspacePath: original.workspacePath
            ? mapPath(original.workspacePath)
            : undefined,
        };
        this.assertManagedPath(transferred.path);
        this.assertOwnershipMetadata(transferred, transferred.path);
        this.uploads.set(transferred.id, transferred);
        this.persist(transferred);
      }
    } catch (error) {
      rollback();
      throw error;
    }
    return rollback;
  }

  async deleteOwnedByUser(ownerUserId: string): Promise<number> {
    let removed = 0;
    for (const upload of [...this.uploads.values()]) {
      if (upload.ownerUserId !== ownerUserId) continue;
      const retained = {
        ...upload,
        previousClientIds: [...upload.previousClientIds],
      };
      try {
        await this.remove(upload);
        removed += 1;
      } catch (error) {
        this.uploads.set(retained.id, retained);
        throw error;
      }
    }
    return removed;
  }

  getTotalBytes(): number {
    let total = 0;
    const seenPaths = new Set<string>();
    for (const upload of this.uploads.values()) {
      if (seenPaths.has(upload.path)) continue;
      seenPaths.add(upload.path);
      total += upload.size;
    }
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
    this.persist(upload);
    return upload;
  }

  private markAuthorized(
    id: string,
    state: UploadState,
    access?: UploadAccess,
  ): StoredUpload | null {
    const upload = this.uploads.get(id);
    if (!upload || !this.authorize(upload, access, false)) return null;
    return this.mark(id, state);
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
    if (![...this.uploads.values()].some(other => other.path === upload.path)) {
      await fs.rm(upload.path, { force: true });
    }
    await fs.rm(this.recordPath(upload.id), { force: true });
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
      path.basename(path.dirname(resolved)) !== "uploads" ||
      !UPLOAD_FILE_RE.test(path.basename(resolved))
    ) {
      throw new Error("Upload path must be a Dano-managed file inside uploads");
    }
  }

  private assertOwnershipMetadata(
    metadata: UploadMetadata,
    filePath: string,
  ): void {
    if (!this.config.requireOwnership) return;
    if (
      !metadata.ownerUserId ||
      !metadata.ownerClientId ||
      !metadata.workspacePath ||
      !metadata.sessionId ||
      !this.isWorkspaceUploadPath(filePath, metadata.workspacePath)
    ) {
      throw new Error(
        "User-owned upload requires User, Client, Agent Session, and Runtime Workspace ownership",
      );
    }
  }

  private authorize(
    upload: StoredUpload,
    access: UploadAccess | undefined,
    allowClientRecovery: boolean,
  ): boolean {
    if (!upload.ownerUserId) return true;
    if (
      !access ||
      upload.ownerUserId !== access.ownerUserId ||
      !samePath(upload.workspacePath, access.workspacePath) ||
      (upload.sessionId !== undefined &&
        upload.sessionId !== access.sessionId &&
        !(
          upload.state === "draft" &&
          upload.ownerClientId === access.ownerClientId &&
          upload.sessionId === access.sourceSessionId
        ))
    ) {
      return false;
    }
    if (upload.ownerClientId === access.ownerClientId) return true;
    if (!allowClientRecovery || !upload.sessionId || !access.ownerClientId) {
      return false;
    }
    if (
      upload.ownerClientId &&
      !upload.previousClientIds.includes(upload.ownerClientId)
    ) {
      upload.previousClientIds.push(upload.ownerClientId);
    }
    upload.ownerClientId = access.ownerClientId;
    this.persist(upload);
    return true;
  }

  private isWorkspaceUploadPath(
    filePath: string,
    workspacePath: string,
  ): boolean {
    const uploadDir = this.workspaceUploadDir(workspacePath);
    const resolved = path.resolve(filePath);
    if (!resolved.startsWith(`${uploadDir}${path.sep}`)) return false;
    try {
      const realWorkspace = fsSync.realpathSync.native(workspacePath);
      const realUploadDirectory = fsSync.realpathSync.native(uploadDir);
      const realFile = fsSync.realpathSync.native(resolved);
      return (
        isInsidePath(realUploadDirectory, realWorkspace) &&
        isInsidePath(realFile, realUploadDirectory)
      );
    } catch {
      return false;
    }
  }

  private async loadPersistedUploads(): Promise<void> {
    const entries = await fs.readdir(this.recordsDirectoryPath, {
      withFileTypes: true,
    });
    for (const entry of entries) {
      const recordMatch = UPLOAD_RECORD_RE.exec(entry.name);
      if (!entry.isFile() || !recordMatch?.[1]) continue;
      let parsed: unknown;
      try {
        parsed = JSON.parse(
          await fs.readFile(
            path.join(this.recordsDirectoryPath, entry.name),
            "utf8",
          ),
        ) as unknown;
      } catch {
        continue;
      }
      const upload = parsePersistedUpload(parsed);
      if (
        !upload ||
        Buffer.from(recordMatch[1], "base64url").toString("utf8") !== upload.id
      ) {
        continue;
      }
      try {
        this.assertManagedPath(upload.path);
        this.assertOwnershipMetadata(upload, upload.path);
        if (
          upload.workspacePath &&
          !this.isWorkspaceUploadPath(upload.path, upload.workspacePath)
        ) {
          continue;
        }
        const stats = await fs.stat(upload.path);
        if (!stats.isFile()) continue;
        this.uploads.set(upload.id, upload);
      } catch {
        // Invalid or missing records are ignored, never adopted with guessed owner data.
      }
    }
  }

  private persist(upload: StoredUpload): void {
    fsSync.mkdirSync(this.recordsDirectoryPath, { recursive: true });
    const recordPath = this.recordPath(upload.id);
    const tempPath = `${recordPath}.${randomUUID()}.tmp`;
    try {
      fsSync.writeFileSync(
        tempPath,
        JSON.stringify({ version: UPLOAD_INDEX_VERSION, upload }),
        { encoding: "utf8", mode: 0o600 },
      );
      fsSync.renameSync(tempPath, recordPath);
    } finally {
      fsSync.rmSync(tempPath, { force: true });
    }
  }

  private recordPath(id: string): string {
    return path.join(
      this.recordsDirectoryPath,
      `${Buffer.from(id).toString("base64url")}.json`,
    );
  }

  private workspaceUploadDir(workspacePath: string): string {
    return path.join(path.resolve(workspacePath), "uploads");
  }

  private hasPath(filePath: string): boolean {
    const resolved = path.resolve(filePath);
    return [...this.uploads.values()].some(upload => upload.path === resolved);
  }
}

function extensionForName(name: string): string {
  const extension = path.extname(name).toLowerCase();
  return /^\.[a-z0-9][a-z0-9._-]{0,31}$/i.test(extension) ? extension : "";
}

function samePath(left: string | undefined, right: string | undefined): boolean {
  return Boolean(left && right && path.resolve(left) === path.resolve(right));
}

function isInsidePath(candidatePath: string, rootPath: string): boolean {
  return (
    candidatePath === rootPath || candidatePath.startsWith(`${rootPath}${path.sep}`)
  );
}

function parsePersistedUpload(value: unknown): StoredUpload | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  if (record.version !== UPLOAD_INDEX_VERSION) return null;
  return parseStoredUpload(record.upload);
}

function parseStoredUpload(value: unknown): StoredUpload | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const state = record.state;
  if (
    typeof record.id !== "string" ||
    typeof record.name !== "string" ||
    typeof record.size !== "number" ||
    typeof record.mimeType !== "string" ||
    typeof record.path !== "string" ||
    typeof record.relativePath !== "string" ||
    (record.previewUrl !== undefined && typeof record.previewUrl !== "string") ||
    !["draft", "reading", "referenced", "orphaned"].includes(String(state)) ||
    typeof record.createdAt !== "number" ||
    typeof record.lastAccessedAt !== "number" ||
    typeof record.refCount !== "number" ||
    (record.ownerUserId !== undefined &&
      typeof record.ownerUserId !== "string") ||
    (record.ownerClientId !== undefined &&
      typeof record.ownerClientId !== "string") ||
    !Array.isArray(record.previousClientIds) ||
    record.previousClientIds.some(clientId => typeof clientId !== "string") ||
    (record.workspacePath !== undefined &&
      typeof record.workspacePath !== "string") ||
    (record.sessionId !== undefined && typeof record.sessionId !== "string") ||
    (record.correlationId !== undefined &&
      typeof record.correlationId !== "string")
  ) {
    return null;
  }
  return record as unknown as StoredUpload;
}
