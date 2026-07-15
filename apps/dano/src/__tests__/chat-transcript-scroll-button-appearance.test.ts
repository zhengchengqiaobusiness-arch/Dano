import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../web/src/components/ChatTranscript.svelte", import.meta.url),
  "utf8",
);

describe("ChatTranscript scroll-to-bottom button appearance", () => {
  it("keeps touch feedback inside the circular control", () => {
    const buttonRule = source.match(/\.scroll-bottom-button\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(buttonRule).toContain("appearance: none");
    expect(buttonRule).toContain("border-radius: 999px");
    expect(buttonRule).toContain("touch-action: manipulation");
    expect(buttonRule).toContain("-webkit-tap-highlight-color: transparent");
  });

  it("preserves tactile and keyboard focus feedback", () => {
    expect(source).toContain(".scroll-bottom-button:active");
    expect(source).toContain("transform: scale(0.96)");
    expect(source).toContain(".scroll-bottom-button:focus-visible");
    expect(source).toContain("outline: 2px solid var(--focus-ring)");
  });
});
