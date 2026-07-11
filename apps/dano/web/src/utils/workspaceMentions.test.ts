import { describe, expect, it, vi } from "vitest";
import {
  applyWorkspaceMentionCompletion,
  getWorkspaceMentionContext,
  getWorkspaceMentionSuggestions,
  requestWorkspaceMentionEntries,
} from "./workspaceMentions";

describe("Runtime Workspace mention capability", () => {
  it("creates no mention context when disabled", () => {
    expect(getWorkspaceMentionContext("Review @src", 11, false)).toBeNull();
  });

  it("creates a mention context when enabled", () => {
    expect(getWorkspaceMentionContext("Review @src", 11, true)).toEqual({
      prefix: "@src",
      rawQuery: "src",
      isQuotedPrefix: false,
      start: 7,
      end: 11,
    });
  });

  it("keeps email text outside workspace mention completion", () => {
    const text = "Email user@example.com";
    expect(getWorkspaceMentionContext(text, text.length, true)).toBeNull();
  });

  it("does not load hidden entries without an enabled mention context", () => {
    const ensureWorkspaceEntries = vi.fn(async () => []);
    const context = getWorkspaceMentionContext("Review @src", 11, false);

    expect(
      requestWorkspaceMentionEntries(
        context,
        "workspace-a",
        null,
        ensureWorkspaceEntries,
      ),
    ).toBeNull();
    expect(ensureWorkspaceEntries).not.toHaveBeenCalled();
  });

  it("loads entries once for an enabled mention interaction", () => {
    const ensureWorkspaceEntries = vi.fn(async () => []);
    const context = getWorkspaceMentionContext("Review @src", 11, true);
    if (!context) throw new Error("expected a mention context");

    const interactionKey = requestWorkspaceMentionEntries(
      context,
      "workspace-a",
      null,
      ensureWorkspaceEntries,
    );
    expect(interactionKey).toBe("workspace-a:7");
    expect(ensureWorkspaceEntries).toHaveBeenCalledOnce();

    requestWorkspaceMentionEntries(
      context,
      "workspace-a",
      interactionKey,
      ensureWorkspaceEntries,
    );
    expect(ensureWorkspaceEntries).toHaveBeenCalledOnce();
  });

  it("limits nested suggestions to the selected directory", () => {
    const context = getWorkspaceMentionContext("Open @src/", 10, true);
    if (!context) throw new Error("expected a mention context");

    expect(
      getWorkspaceMentionSuggestions(
        [
          { path: "src/components", kind: "directory" },
          { path: "src/main.ts", kind: "file" },
          { path: "docs/guide.md", kind: "file" },
        ],
        context,
      ).map(item => item.path),
    ).toEqual(["src/components", "src/main.ts"]);
  });

  it("quotes completed file paths that contain spaces", () => {
    const text = "Open @release";
    const context = getWorkspaceMentionContext(text, text.length, true);
    if (!context) throw new Error("expected a mention context");
    const [suggestion] = getWorkspaceMentionSuggestions(
      [{ path: "release notes.md", kind: "file" }],
      context,
    );
    if (!suggestion) throw new Error("expected a mention suggestion");

    expect(
      applyWorkspaceMentionCompletion(
        text,
        text.length,
        context,
        suggestion,
      ),
    ).toEqual({
      text: 'Open @"release notes.md" ',
      cursor: 25,
    });
  });
});
