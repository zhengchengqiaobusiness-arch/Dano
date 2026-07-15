import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../web/src/layout/AppHeader.svelte", import.meta.url),
  "utf8",
);

describe("AppHeader control appearance", () => {
  it("shares one shadow between the connection and new-session controls", () => {
    const newSessionRule = source.match(/\.new-session-button\s*\{([^}]*)\}/)?.[1] ?? "";
    const connectionRule = source.match(/\.connection-status\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(source).toContain(
      "--header-control-shadow: 0 2px 8px rgba(0, 0, 0, 0.04)",
    );
    expect(newSessionRule).toContain("box-shadow: var(--header-control-shadow)");
    expect(connectionRule).toContain("box-shadow: var(--header-control-shadow)");
  });

  it("uses the same text color for connection and new-session controls", () => {
    const newSessionRule = source.match(/\.new-session-button\s*\{([^}]*)\}/)?.[1] ?? "";
    const connectionRule = source.match(/\.connection-status\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(newSessionRule).toContain("color: var(--text)");
    expect(connectionRule).toContain("color: var(--text)");
  });
});
