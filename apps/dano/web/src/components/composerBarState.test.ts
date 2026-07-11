import { describe, expect, it } from "vitest";
import {
  canSubmitComposerMessage,
  shouldRejectCompactAttachments,
} from "./composerSubmit";

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

describe("compact attachment submission", () => {
  it("submits compact-shaped text with attachments when slash commands are disabled", () => {
    expect(
      shouldRejectCompactAttachments({
        message: "/compact keep this literal",
        hasAttachments: true,
        slashCommandsEnabled: false,
      }),
    ).toBe(false);
  });

  it("keeps the compact attachment warning when slash commands are enabled", () => {
    expect(
      shouldRejectCompactAttachments({
        message: "/compact keep decisions",
        hasAttachments: true,
        slashCommandsEnabled: true,
      }),
    ).toBe(true);
  });

  it("does not reject attachments for ordinary slash text", () => {
    expect(
      shouldRejectCompactAttachments({
        message: "/docs/reference",
        hasAttachments: true,
        slashCommandsEnabled: true,
      }),
    ).toBe(false);
  });
});
