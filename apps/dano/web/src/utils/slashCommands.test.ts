import { describe, expect, it } from "vitest";
import {
  getSlashCommandContext,
  parseCompactSlashCommand,
} from "./slashCommands";

describe("slash command capability", () => {
  it("does not offer slash command completion when disabled", () => {
    expect(getSlashCommandContext("/comp", 5, false)).toBeNull();
  });

  it("keeps slash command completion available when enabled", () => {
    expect(getSlashCommandContext("/comp", 5, true)).toEqual({
      query: "comp",
      start: 0,
      end: 5,
    });
  });

  it("treats compact syntax as ordinary text when disabled", () => {
    expect(parseCompactSlashCommand("/compact keep decisions", false)).toBeNull();
  });

  it("parses compact syntax when enabled", () => {
    expect(parseCompactSlashCommand("/compact keep decisions", true)).toEqual({
      customInstructions: "keep decisions",
    });
  });

  it("keeps non-command slash text ordinary when enabled", () => {
    expect(parseCompactSlashCommand("/docs/reference", true)).toBeNull();
  });
});
