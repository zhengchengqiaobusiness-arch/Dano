import type { RpcImageContent, RpcUploadedFileRef } from "@dano/types/protocol";
import { getBridgeClientId } from "../composables/bridgeStore.svelte";

export const MAX_COMPOSER_ATTACHMENTS = 10;
export const MAX_COMPOSER_ATTACHMENT_BYTES = 50 * 1024 * 1024;
export const DEFAULT_ATTACHMENT_MIME_TYPE = "application/octet-stream";

export interface ComposerAttachment {
  id: string;
  type: "file" | "image";
  name: string;
  size: number;
  mimeType: string;
  previewUrl?: string;
  status: "uploading" | "uploaded" | "failed";
  data?: string;
  file?: RpcUploadedFileRef;
  error?: string;
  abortController?: AbortController;
}

export const COMPOSER_ATTACHMENT_ACCEPT = "";

export function formatAttachmentSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function getComposerUploadMimeType(file: Pick<File, "type">): string {
  return file.type.trim().toLowerCase() || DEFAULT_ATTACHMENT_MIME_TYPE;
}

export function createUploadingComposerAttachment(
  file: File,
  abortController: AbortController,
): ComposerAttachment {
  const mimeType = getComposerUploadMimeType(file);
  return {
    id: createAttachmentId(),
    type: mimeType.startsWith("image/") ? "image" : "file",
    name: file.name,
    size: file.size,
    mimeType,
    previewUrl: mimeType.startsWith("image/") ? URL.createObjectURL(file) : undefined,
    status: "uploading",
    abortController,
  };
}

export async function uploadComposerAttachment(
  file: File,
  signal: AbortSignal,
): Promise<RpcUploadedFileRef> {
  const clientId = getBridgeClientId();
  if (!clientId) throw new Error("Upload requires an active client");
  const mimeType = getComposerUploadMimeType(file);
  const sha256 = await sha256File(file);
  const query = new URLSearchParams({ clientId, name: file.name, mimeType });
  if (sha256) {
    query.set("sha256", sha256);
    const existing = await fetch(`/api/uploads/lookup?${query.toString()}`, {
      method: "GET",
      signal,
    });
    if (existing.ok) {
      return (await existing.json()) as RpcUploadedFileRef;
    }
    if (existing.status !== 404) {
      throw new Error(`Upload lookup failed (${existing.status})`);
    }
  }
  const response = await fetch(`/api/uploads?${query.toString()}`, {
    method: "POST",
    headers: { "Content-Type": mimeType },
    body: file,
    signal,
  });
  if (!response.ok) {
    throw new Error(`Upload failed (${response.status})`);
  }
  return (await response.json()) as RpcUploadedFileRef;
}

export async function imageFileToRpcData(file: File): Promise<string | undefined> {
  if (!getComposerUploadMimeType(file).startsWith("image/")) return undefined;
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

export async function markComposerAttachmentOrphaned(
  file: RpcUploadedFileRef,
): Promise<void> {
  const clientId = getBridgeClientId();
  if (!clientId) return;
  const query = new URLSearchParams({ clientId });
  await fetch(`/api/uploads/${encodeURIComponent(file.id)}/orphan?${query}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
}

export function toRpcImageContent(
  attachments: readonly ComposerAttachment[],
): RpcImageContent[] {
  return attachments.flatMap(({ data, mimeType }) =>
    data ? [{ type: "image" as const, data, mimeType }] : [],
  );
}

export function toRpcUploadedFileRefs(
  attachments: readonly ComposerAttachment[],
): RpcUploadedFileRef[] {
  return attachments.flatMap(attachment =>
    attachment.status === "uploaded" && attachment.file ? [attachment.file] : [],
  );
}

function createAttachmentId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) {
    return cryptoApi.randomUUID();
  }
  return `attachment_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
}

async function sha256File(file: File): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    await file.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest), byte =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}
