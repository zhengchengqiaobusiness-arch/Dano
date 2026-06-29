import { beforeEach, describe, expect, it, vi } from "vitest";

const getBridgeClientId = vi.hoisted(() => vi.fn());

vi.mock("../composables/bridgeStore.svelte", () => ({
  getBridgeClientId,
}));

import {
  createUploadingComposerAttachment,
  getComposerUploadMimeType,
  markComposerAttachmentOrphaned,
  toRpcImageContent,
  toRpcUploadedFileRefs,
  uploadComposerAttachment,
} from "./attachments";

describe("composer attachment uploads", () => {
  beforeEach(() => {
    getBridgeClientId.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (String(url).startsWith("/api/uploads/lookup?")) {
          return Promise.resolve({ ok: false, status: 404 });
        }
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "upload-1",
            name: "sample.png",
            size: 4,
            mimeType: "image/png",
            path: "/tmp/sample.png",
          }),
        });
      }),
    );
  });

  it("includes the current bridge client id when uploading", async () => {
    getBridgeClientId.mockReturnValue("client_1");

    await uploadComposerAttachment(
      new File([new Uint8Array([1])], "sample.png", { type: "image/png" }),
      new AbortController().signal,
    );

    const [lookupUrl] = vi.mocked(fetch).mock.calls[0]!;
    expect(new URL(String(lookupUrl), "http://dano.test").pathname).toBe(
      "/api/uploads/lookup",
    );

    const [url, init] = vi.mocked(fetch).mock.calls[1]!;
    const parsed = new URL(String(url), "http://dano.test");
    expect(parsed.pathname).toBe("/api/uploads");
    expect(parsed.searchParams.get("clientId")).toBe("client_1");
    expect(parsed.searchParams.get("name")).toBe("sample.png");
    expect(parsed.searchParams.get("mimeType")).toBe("image/png");
    expect(parsed.searchParams.get("sha256")).toBe(
      "4bf5122f344554c53bde2ebb8cd2b7e3d1600ad631c385a5d7cce23c7785459a",
    );
    expect(init).toEqual(
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "image/png" },
      }),
    );
  });

  it("uses application/octet-stream when the browser provides no MIME type", async () => {
    getBridgeClientId.mockReturnValue("client_1");

    await uploadComposerAttachment(
      new File(["abc"], "archive.bin", { type: "" }),
      new AbortController().signal,
    );

    const [url, init] = vi.mocked(fetch).mock.calls[1]!;
    const parsed = new URL(String(url), "http://dano.test");
    expect(parsed.searchParams.get("mimeType")).toBe("application/octet-stream");
    expect(init).toEqual(
      expect.objectContaining({
        headers: { "Content-Type": "application/octet-stream" },
      }),
    );
  });

  it("returns an existing upload ref without posting bytes when hash lookup hits", async () => {
    getBridgeClientId.mockReturnValue("client_1");
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: vi.fn().mockResolvedValue({
        id: "upload-existing",
        name: "same.txt",
        size: 4,
        mimeType: "text/plain",
        path: "/tmp/workspace/uploads/hash.txt",
        relativePath: "uploads/hash.txt",
      }),
    } as unknown as Response);

    await expect(
      uploadComposerAttachment(
        new File(["same"], "same.txt", { type: "text/plain" }),
        new AbortController().signal,
      ),
    ).resolves.toMatchObject({
      id: "upload-existing",
      relativePath: "uploads/hash.txt",
    });

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(String(vi.mocked(fetch).mock.calls[0]![0])).toContain(
      "/api/uploads/lookup?",
    );
  });

  it("creates uploading file attachments without base64 data", () => {
    const attachment = createUploadingComposerAttachment(
      new File(["hello"], "notes.txt", { type: "text/plain" }),
      new AbortController(),
    );

    expect(attachment).toEqual(
      expect.objectContaining({
        type: "file",
        name: "notes.txt",
        size: 5,
        mimeType: "text/plain",
        status: "uploading",
      }),
    );
    expect(attachment.data).toBeUndefined();
    expect(attachment.previewUrl).toBeUndefined();
  });

  it("normalizes arbitrary upload MIME types", () => {
    expect(
      getComposerUploadMimeType(
        new File(["%PDF"], "doc.pdf", { type: "application/pdf" }),
      ),
    ).toBe("application/pdf");
    expect(getComposerUploadMimeType(new File([""], "unknown"))).toBe(
      "application/octet-stream",
    );
  });

  it("does not convert uploaded file refs into image payloads", () => {
    const attachment = {
      id: "attachment-1",
      type: "file" as const,
      name: "report.pdf",
      size: 3,
      mimeType: "application/pdf",
      status: "uploaded" as const,
      file: {
        id: "upload-1",
        name: "report.pdf",
        size: 3,
        mimeType: "application/pdf",
        path: "/tmp/report.pdf",
      },
    };

    expect(toRpcImageContent([attachment])).toEqual([]);
    expect(toRpcUploadedFileRefs([attachment])).toEqual([attachment.file]);
  });

  it("marks uploaded attachments orphaned for the current client", async () => {
    getBridgeClientId.mockReturnValue("client_1");

    await markComposerAttachmentOrphaned({
      id: "upload-1",
      name: "sample.png",
      size: 4,
      mimeType: "image/png",
      path: "/tmp/sample.png",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/uploads/upload-1/orphan?clientId=client_1",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
