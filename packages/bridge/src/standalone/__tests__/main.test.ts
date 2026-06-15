import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { parseStandaloneMainOptions } from "../main.js";

function findNearestWebDist(startDir: string): string | undefined {
  let current = resolve(startDir);

  for (;;) {
    const candidate = join(current, "web-dist");
    if (existsSync(candidate)) {
      return candidate;
    }

    const parent = dirname(current);
    if (parent === current) {
      return undefined;
    }
    current = parent;
  }
}

describe("standalone main", () => {
  it("parses the optional port override", () => {
    const options = parseStandaloneMainOptions(["--port", "8123"], {});

    expect(options.cwd).toBe(process.cwd());
    expect(options.host).toBe("0.0.0.0");
    expect(options.port).toBe(8123);
    expect(options.defaultWorkspacePath).toBe("/tmp/dano");
    expect(options.staticDir).toBe(findNearestWebDist(process.cwd()));
    expect(options.help).toBe(false);
  });

  it("parses host and port overrides", () => {
    const options = parseStandaloneMainOptions(
      ["--host", "127.0.0.1", "--port", "8123"],
      {},
    );

    expect(options.host).toBe("127.0.0.1");
    expect(options.port).toBe(8123);
  });

  it("uses the default workspace from environment", () => {
    const options = parseStandaloneMainOptions([], {
      DANO_DEFAULT_WORKSPACE_PATH: "/tmp/custom-dano",
    });

    expect(options.defaultWorkspacePath).toBe("/tmp/custom-dano");
  });

  it("lets command line default workspace override environment", () => {
    const options = parseStandaloneMainOptions(
      ["--default-workspace", "/tmp/cli-dano"],
      { DANO_DEFAULT_WORKSPACE_PATH: "/tmp/env-dano" },
    );

    expect(options.defaultWorkspacePath).toBe("/tmp/cli-dano");
  });

  it("uses product name and empty text from environment", () => {
    const options = parseStandaloneMainOptions([], {
      DANO_PRODUCT_NAME: "My Agent",
      DANO_EMPTY_STATE_TEXT: "给 {产品名称} 发消息",
    });

    expect(options.productName).toBe("My Agent");
    expect(options.emptyState).toEqual({
      mode: "text",
      content: "给 {产品名称} 发消息",
    });
  });

  it("uses empty html from environment when provided", () => {
    const options = parseStandaloneMainOptions([], {
      DANO_EMPTY_STATE_TEXT: "ignored",
      DANO_EMPTY_STATE_HTML: "<strong>给 {产品名称} 发消息</strong>",
    });

    expect(options.emptyState).toEqual({
      mode: "html",
      content: "<strong>给 {产品名称} 发消息</strong>",
    });
  });

  it("lets command line empty state override environment", () => {
    const options = parseStandaloneMainOptions(
      [
        "--product-name",
        "CLI Agent",
        "--empty-state-html",
        "<em>HTML only</em>",
      ],
      {
        DANO_PRODUCT_NAME: "Env Agent",
        DANO_EMPTY_STATE_TEXT: "env text",
      },
    );

    expect(options.productName).toBe("CLI Agent");
    expect(options.emptyState).toEqual({
      mode: "html",
      content: "<em>HTML only</em>",
    });
  });

  it("accepts help flag", () => {
    const options = parseStandaloneMainOptions(["--help"], {});
    expect(options.help).toBe(true);
  });

  it("throws on missing option value", () => {
    expect(() => parseStandaloneMainOptions(["--port"], {})).toThrow(
      "Missing value for --port",
    );
  });

  it("throws on missing host value", () => {
    expect(() => parseStandaloneMainOptions(["--host"], {})).toThrow(
      "Missing value for --host",
    );
  });

  it("throws on missing default workspace value", () => {
    expect(() => parseStandaloneMainOptions(["--default-workspace"], {})).toThrow(
      "Missing value for --default-workspace",
    );
  });

  it("throws on missing empty state values", () => {
    expect(() => parseStandaloneMainOptions(["--empty-state-text"], {})).toThrow(
      "Missing value for --empty-state-text",
    );
    expect(() => parseStandaloneMainOptions(["--empty-state-html"], {})).toThrow(
      "Missing value for --empty-state-html",
    );
  });

  it("throws on unknown options", () => {
    expect(() =>
      parseStandaloneMainOptions(["--cwd", "/tmp/project"], {}),
    ).toThrow(
      "Unknown option: --cwd",
    );
  });
});
