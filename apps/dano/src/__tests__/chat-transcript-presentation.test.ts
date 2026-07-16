import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../web/src/components/ChatTranscript.svelte", import.meta.url),
  "utf8",
);

describe("ChatTranscript presentation", () => {
  it("uses the shared icon renderer for tool calls and standalone results", () => {
    expect(source).toContain("transcriptToolIconName");
    expect(source).toContain("toolSummaryName(item.message.toolName");
    expect(source).toContain("toolSummaryName(block.toolName");
    expect(source).toContain("<CodeXml");
    expect(source).toContain("<BookOpenText");
    expect(source).toContain("<FilePenLine");
    expect(source).toContain("<PenLine");
    expect(source).toContain("aria-label={accessibleName}");
  });

  it("renders an accessible animated skeleton only for initial loading", () => {
    expect(source).toContain('class="conversation-skeleton"');
    expect(source).toContain('role="status"');
    expect(source).toContain('aria-label={t("chatTranscript.loadingTitle")}');
    expect(source).toContain("@keyframes conversation-skeleton-shimmer");
    expect(source).toContain("@media (prefers-reduced-motion: reduce)");
    expect(source).toContain("animation: none");
    expect(source).toContain("history-loader-spinner");
    expect(source).toContain("assistant-pending-dot");
  });
});
