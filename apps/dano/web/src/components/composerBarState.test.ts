import { describe, expect, it } from "vitest";
import { canSubmitComposerMessage } from "./composerSubmit";

const base = {
  connectionStatus: "connected" as const,
  hasUploadingAttachments: false,
  hasFailedAttachments: false,
  hasText: true,
  hasSubmittableAttachments: false,
};

describe("canSubmitComposerMessage", () => {
  it("allows text messages while connected", () => {
    expect(canSubmitComposerMessage(base)).toBe(true);
  });

  it("allows text messages while disconnected so send can reconnect first", () => {
    expect(
      canSubmitComposerMessage({
        ...base,
        connectionStatus: "disconnected",
      }),
    ).toBe(true);
  });

  it("blocks messages while an explicit connection attempt is in progress", () => {
    expect(
      canSubmitComposerMessage({
        ...base,
        connectionStatus: "connecting",
      }),
    ).toBe(false);
  });

  it("blocks empty messages without uploaded attachments", () => {
    expect(
      canSubmitComposerMessage({
        ...base,
        hasText: false,
      }),
    ).toBe(false);
  });

  it("allows uploaded attachments without text", () => {
    expect(
      canSubmitComposerMessage({
        ...base,
        hasText: false,
        hasSubmittableAttachments: true,
      }),
    ).toBe(true);
  });

  it("blocks while attachments are still uploading or failed", () => {
    expect(
      canSubmitComposerMessage({
        ...base,
        hasUploadingAttachments: true,
      }),
    ).toBe(false);
    expect(
      canSubmitComposerMessage({
        ...base,
        hasFailedAttachments: true,
      }),
    ).toBe(false);
  });
});
