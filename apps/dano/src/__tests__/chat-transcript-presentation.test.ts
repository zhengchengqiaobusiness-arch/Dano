import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../web/src/components/ChatTranscript.svelte", import.meta.url),
  "utf8",
);
const activityRowSource = readFileSync(
  new URL("../../web/src/components/ToolActivityRow.svelte", import.meta.url),
  "utf8",
);

describe("ChatTranscript presentation", () => {
  it("routes tool calls and standalone results through the Activity Trail", () => {
    expect(source).toContain("buildToolActivities");
    expect(source).toContain("orphanToolActivity");
    expect(source).toContain("<ToolActivityRow");
    expect(activityRowSource).toContain("<SquareTerminal");
    expect(activityRowSource).toContain("<BookOpenText");
    expect(activityRowSource).toContain("<FilePenLine");
    expect(activityRowSource).toContain("<ListChecks");
    expect(activityRowSource).toContain("<WandSparkles");
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
