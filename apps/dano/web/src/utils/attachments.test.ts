import { beforeEach, describe, expect, it, vi } from "vitest";

const getBridgeClientId = vi.hoisted(() => vi.fn());

vi.mock("../composables/bridgeStore.svelte", () => ({
  getBridgeClientId,
}));

import {
  markComposerAttachmentOrphaned,
  uploadComposerAttachment,
} from "./attachments";

describe("composer attachment uploads", () => {
  beforeEach(() => {
    getBridgeClientId.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          id: "upload-1",
          name: "sample.png",
          size: 4,
          mimeType: "image/png",
          path: "/tmp/sample.png",
        }),
      }),
    );
  });

  it("includes the current bridge client id when uploading", async () => {
    getBridgeClientId.mockReturnValue("client_1");

    await uploadComposerAttachment(
      new File([new Uint8Array([1])], "sample.png", { type: "image/png" }),
      "image/png",
      new AbortController().signal,
    );

    expect(fetch).toHaveBeenCalledWith(
      "/api/uploads?clientId=client_1&name=sample.png&mimeType=image%2Fpng",
      expect.objectContaining({ method: "POST" }),
    );
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
