import { describe, expect, it } from "vitest";
import {
  applySlashCommandCompletion,
  getSlashCommandContext,
  mergeSlashCommandOptions,
  parseCompactSlashCommand,
  slashCommandOptionsFromRpc,
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

  it("offers the complete Pi catalog with one Dano compact command", () => {
    const commands = mergeSlashCommandOptions(
      slashCommandOptionsFromRpc([
        {
          name: "deploy:2",
          description: "Second deploy extension",
        },
        {
          name: "review",
          description: "Review prompt template",
        },
        {
          name: "skill:audit",
          description: "Audit skill",
        },
        {
          name: "compact",
          description: "Session-provided duplicate",
        },
      ]),
    );

    expect(commands).toEqual([
      { name: "deploy:2", description: "Second deploy extension" },
      { name: "review", description: "Review prompt template" },
      { name: "skill:audit", description: "Audit skill" },
      {
        name: "compact",
        description: "Compact context now, optionally with custom instructions",
      },
    ]);
  });

  it.each(["deploy:2", "review", "skill:audit"])(
    "selects the %s callable name without rewriting it",
    name => {
      const context = getSlashCommandContext("/", 1, true);
      if (!context) throw new Error("expected slash command context");

      expect(
        applySlashCommandCompletion("/", context, { name }),
      ).toEqual({
        text: `/${name} `,
        cursor: name.length + 2,
      });
    },
  );
});
