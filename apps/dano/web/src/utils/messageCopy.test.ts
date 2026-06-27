import { describe, expect, it } from "vitest";
import { userMessageCopyText, userMessagePlainText } from "./messageCopy";

describe("messageCopy", () => {
  const userMessage = {
    id: "u1",
    role: "user",
    content: "first line\nsecond line",
  } as const;

  it("returns user message plain text for explicit copy actions", () => {
    expect(userMessagePlainText(userMessage)).toBe("first line\nsecond line");
  });

  it("rejects assistant messages for user bubble copy", () => {
    expect(userMessagePlainText({ ...userMessage, role: "assistant" })).toBeNull();
  });

  it("copies selected text when it matches the original user content", () => {
    expect(userMessageCopyText(userMessage, "first line\nsecond line", "ignored")).toBe(
      "first line\nsecond line",
    );
  });

  it("copies selected text when browser selection matches rendered wrapping", () => {
    expect(userMessageCopyText(userMessage, "first line second line", "first line second line")).toBe(
      "first line\nsecond line",
    );
  });

  it("rejects partial selections so normal copy still works", () => {
    expect(userMessageCopyText(userMessage, "first line", "first line second line")).toBeNull();
  });
});
