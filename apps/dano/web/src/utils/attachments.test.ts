import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  imageFileToRpcData,
} from "./attachments";

function stubImageCodec() {
  const image = { displayWidth: 640, displayHeight: 480, close: vi.fn() };
  const track = { animated: false };
  const decode = vi.fn().mockResolvedValue({ image });
  const close = vi.fn();
  const encode = vi.fn().mockResolvedValue(new Blob(["abc"], { type: "image/webp" }));
  vi.stubGlobal("ImageDecoder", class {
    tracks = { ready: Promise.resolve(), selectedTrack: track };
    completed = Promise.resolve();
    decode = decode;
    close = close;
  });
  vi.stubGlobal("OffscreenCanvas", class {
    getContext = () => ({ drawImage: vi.fn() });
    convertToBlob = encode;
  });
  return { image, track, decode, close, encode };
}

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

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uploads the smaller WebP with matching bytes, hash and metadata", async () => {
    getBridgeClientId.mockReturnValue("client_1");
    const image = { displayWidth: 640, displayHeight: 480, close: vi.fn() };
    vi.stubGlobal("ImageDecoder", class {
      tracks = { ready: Promise.resolve(), selectedTrack: { animated: false } };
      completed = Promise.resolve();
      decode = async () => ({ image });
      close = vi.fn();
    });
    const drawImage = vi.fn();
    const canvas = vi.fn(class {
      getContext = () => ({ drawImage });
      convertToBlob = async () => new Blob(["abc"], { type: "image/webp" });
    });
    vi.stubGlobal("OffscreenCanvas", canvas);

    await uploadComposerAttachment(
      new File(["original image bytes"], "screen.capture.png", { type: "image/png" }),
      new AbortController().signal,
    );

    const [url, init] = vi.mocked(fetch).mock.calls[1]!;
    const query = new URL(String(url), "http://dano.test").searchParams;
    expect(query.get("name")).toBe("screen.capture.webp");
    expect(query.get("mimeType")).toBe("image/webp");
    expect(query.get("sha256")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(init?.headers).toEqual({ "Content-Type": "image/webp" });
    expect(await (init?.body as Blob).text()).toBe("abc");
    expect(canvas).toHaveBeenCalledWith(640, 480);
    expect(drawImage).toHaveBeenCalledWith(image, 0, 0);
    expect(image.close).toHaveBeenCalledOnce();
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

  it("rejects oversized originals before decoding or uploading", async () => {
    getBridgeClientId.mockReturnValue("client_1");
    const decode = vi.fn();
    vi.stubGlobal("ImageDecoder", decode);
    const file = new File(["image"], "large.png", { type: "image/png" });
    Object.defineProperty(file, "size", { value: 50 * 1024 * 1024 + 1 });
    await expect(uploadComposerAttachment(file, new AbortController().signal))
      .rejects.toThrow(/size/i);
    expect(fetch).not.toHaveBeenCalled();
    expect(decode).not.toHaveBeenCalled();
  });

  it("does not upload an attachment cancelled during image conversion", async () => {
    getBridgeClientId.mockReturnValue("client_1");
    const codec = stubImageCodec();
    const controller = new AbortController();
    codec.encode.mockImplementation(async () => {
      controller.abort();
      return new Blob(["abc"], { type: "image/webp" });
    });
    await expect(uploadComposerAttachment(
      new File(["original image bytes"], "sample.png", { type: "image/png" }),
      controller.signal,
    )).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).not.toHaveBeenCalled();
    expect(codec.image.close).toHaveBeenCalledOnce();
    expect(codec.close).toHaveBeenCalledOnce();
  });

  it.each(["image/gif", "image/png", "image/avif"])("preserves animated %s bytes", async type => {
    getBridgeClientId.mockReturnValue("client_1");
    const codec = stubImageCodec();
    codec.track.animated = true;
    const file = new File(["original animation"], "animated", { type });
    await uploadComposerAttachment(file, new AbortController().signal);
    expect(vi.mocked(fetch).mock.calls[1]![1]?.body).toBe(file);
    expect(codec.decode).not.toHaveBeenCalled();
    expect(codec.close).toHaveBeenCalledOnce();
  });

  it.each(["image/svg+xml", "image/webp", "application/pdf"])("leaves %s untouched", async type => {
    getBridgeClientId.mockReturnValue("client_1");
    const codec = stubImageCodec();
    const file = new File(["original"], "original", { type });
    await uploadComposerAttachment(file, new AbortController().signal);
    expect(vi.mocked(fetch).mock.calls[1]![1]?.body).toBe(file);
    expect(codec.decode).not.toHaveBeenCalled();
  });

  it.each([
    new Blob(["123456"], { type: "image/webp" }),
    new Blob(["1234567"], { type: "image/webp" }),
    new Blob(["abc"], { type: "image/png" }),
    new Blob([], { type: "image/webp" }),
  ])("retains the original when encoding does not yield a smaller WebP (%j)", async blob => {
    getBridgeClientId.mockReturnValue("client_1");
    stubImageCodec().encode.mockResolvedValue(blob);
    const file = new File(["source"], "source.png", { type: "image/png" });
    await uploadComposerAttachment(file, new AbortController().signal);
    expect(vi.mocked(fetch).mock.calls[1]![1]?.body).toBe(file);
  });

  it.each(["decode", "encode"] as const)("falls back after %s fails and releases resources", async stage => {
    getBridgeClientId.mockReturnValue("client_1");
    const codec = stubImageCodec();
    codec[stage].mockRejectedValue(new Error("codec failed"));
    const file = new File(["source"], "source.png", { type: "image/png" });
    await uploadComposerAttachment(file, new AbortController().signal);
    expect(vi.mocked(fetch).mock.calls[1]![1]?.body).toBe(file);
    expect(codec.close).toHaveBeenCalledOnce();
    if (stage === "encode") expect(codec.image.close).toHaveBeenCalledOnce();
  });

  it("retains originals when the browser has no image decoder", async () => {
    getBridgeClientId.mockReturnValue("client_1");
    vi.stubGlobal("ImageDecoder", undefined);
    const file = new File(["source"], "source.png", { type: "image/png" });
    await uploadComposerAttachment(file, new AbortController().signal);
    expect(vi.mocked(fetch).mock.calls[1]![1]?.body).toBe(file);
  });

  it("posts bytes without lookup when WebCrypto hashing is unavailable", async () => {
    getBridgeClientId.mockReturnValue("client_1");
    vi.stubGlobal("crypto", {});

    await uploadComposerAttachment(
      new File(["abc"], "http-only.txt", { type: "text/plain" }),
      new AbortController().signal,
    );

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url] = vi.mocked(fetch).mock.calls[0]!;
    const parsed = new URL(String(url), "http://dano.test");
    expect(parsed.pathname).toBe("/api/uploads");
    expect(parsed.searchParams.get("clientId")).toBe("client_1");
    expect(parsed.searchParams.get("sha256")).toBeNull();
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

  it("keeps image uploads available for model image input", async () => {
    await expect(
      imageFileToRpcData(
        new File([new Uint8Array([1, 2, 3])], "sample.png", {
          type: "image/png",
        }),
      ),
    ).resolves.toBe("AQID");
  });

  it("does not convert non-image uploaded file refs into image payloads", () => {
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

  it("keeps uploaded image refs out of the JSON image payload", () => {
    const attachment = {
      id: "attachment-1",
      type: "image" as const,
      name: "large.png",
      size: 7 * 1024 * 1024,
      mimeType: "image/png",
      status: "uploaded" as const,
      previewUrl: "/api/uploads/upload-1/preview",
      data: "big-base64",
      file: {
        id: "upload-1",
        name: "large.png",
        size: 7 * 1024 * 1024,
        mimeType: "image/png",
        path: "/tmp/large.png",
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
