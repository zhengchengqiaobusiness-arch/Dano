import type { RpcImageContent, RpcUploadedFileRef } from "@dano/types/protocol";
import { getBridgeClientId } from "../composables/bridgeStore.svelte";

const SUPPORTED_IMAGE_MIME_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

const MIME_TYPE_BY_EXTENSION: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

export const MAX_COMPOSER_ATTACHMENTS = 10;
export const MAX_COMPOSER_ATTACHMENT_BYTES = 50 * 1024 * 1024;

export interface ComposerAttachment {
  id: string;
  type: "image";
  name: string;
  size: number;
  mimeType: string;
  previewUrl: string;
  status: "uploading" | "uploaded" | "failed";
  data?: string;
  file?: RpcUploadedFileRef;
  error?: string;
  abortController?: AbortController;
}

export const COMPOSER_ATTACHMENT_ACCEPT = [
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ...SUPPORTED_IMAGE_MIME_TYPES,
].join(",");

export function formatAttachmentSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function getSupportedImageMimeType(
  file: Pick<File, "name" | "type">,
): string | null {
  const mimeType = file.type.trim().toLowerCase();
  if (SUPPORTED_IMAGE_MIME_TYPES.has(mimeType)) {
    return mimeType;
  }

  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  return MIME_TYPE_BY_EXTENSION[extension] ?? null;
}

export function extractSupportedImageFiles(
  source: Iterable<File> | ArrayLike<File> | null | undefined,
): File[] {
  if (!source) return [];
  return Array.from(source).filter(file => getSupportedImageMimeType(file));
}

export async function createComposerAttachments(
  files: Iterable<File> | ArrayLike<File>,
): Promise<{ attachments: ComposerAttachment[]; rejectedNames: string[] }> {
  const attachments: ComposerAttachment[] = [];
  const rejectedNames: string[] = [];

  for (const file of Array.from(files)) {
    const mimeType = getSupportedImageMimeType(file);
    if (!mimeType) {
      rejectedNames.push(file.name);
      continue;
    }

    const data = await fileToBase64(file);
    attachments.push({
      id: createAttachmentId(),
      type: "image",
      name: file.name,
      size: file.size,
      mimeType,
      data,
      previewUrl: createDataUrl(mimeType, data),
      status: "uploaded",
    });
  }

  return { attachments, rejectedNames };
}

export function createUploadingComposerAttachment(
  file: File,
  mimeType: string,
  abortController: AbortController,
): ComposerAttachment {
  return {
    id: createAttachmentId(),
    type: "image",
    name: file.name,
    size: file.size,
    mimeType,
    previewUrl: URL.createObjectURL(file),
    status: "uploading",
    abortController,
  };
}

export async function uploadComposerAttachment(
  file: File,
  mimeType: string,
  signal: AbortSignal,
): Promise<RpcUploadedFileRef> {
  const clientId = getBridgeClientId();
  if (!clientId) throw new Error("Upload requires an active client");
  const query = new URLSearchParams({ clientId, name: file.name, mimeType });
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

function createDataUrl(mimeType: string, data: string): string {
  return `data:${mimeType};base64,${data}`;
}

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  return bytesToBase64(bytes);
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;

  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize);
    for (let chunkIndex = 0; chunkIndex < chunk.length; chunkIndex += 1) {
      binary += String.fromCharCode(chunk[chunkIndex] ?? 0);
    }
  }

  return btoa(binary);
}
