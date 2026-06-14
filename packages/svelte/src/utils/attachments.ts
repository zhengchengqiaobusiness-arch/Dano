import type { RpcImageContent } from "@pi-web/bridge/types";

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

export interface ComposerAttachment extends RpcImageContent {
  id: string;
  name: string;
  size: number;
  previewUrl: string;
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
    });
  }

  return { attachments, rejectedNames };
}

export function toRpcImageContent(
  attachments: readonly ComposerAttachment[],
): RpcImageContent[] {
  return attachments.map(({ type, data, mimeType }) => ({
    type,
    data,
    mimeType,
  }));
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
