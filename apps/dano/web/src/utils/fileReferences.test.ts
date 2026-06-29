import { describe, expect, it } from "vitest";
import { parseInlineFileReference } from "./fileReferences";

describe("parseInlineFileReference", () => {
  it("keeps real file references clickable without treating addresses as files", () => {
    expect(parseInlineFileReference("apps/dano/web/src/App.svelte:519")).toEqual({
      path: "apps/dano/web/src/App.svelte",
      lineNumber: 519,
      columnNumber: undefined,
    });
    expect(parseInlineFileReference("package.json:1")).toEqual({
      path: "package.json",
      lineNumber: 1,
      columnNumber: undefined,
    });

    expect(parseInlineFileReference("127.0.0.1:8080")).toBeNull();
    expect(parseInlineFileReference("localhost:5173")).toBeNull();
    expect(parseInlineFileReference("https://example.com:443")).toBeNull();
    expect(parseInlineFileReference("example.com:3000")).toBeNull();
  });
});
