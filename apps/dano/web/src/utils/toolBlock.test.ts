import { describe, expect, it } from "vitest";
import { buildToolDetailModel, buildToolInlineModel } from "./toolBlock";
import type { ToolContentBlock } from "./transcript";

function curlBlock(
  overrides: Partial<ToolContentBlock> = {},
): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "curl",
    toolArgs: {
      args: ["-L", "https://www.baidu.com/s?wd=蛋仔派对"],
    },
    argumentsText: "",
    toolStatus: "success",
    resultDetails: { stderr: "", exitCode: 0 },
    ...overrides,
  };
}

function bashBlock(
  overrides: Partial<ToolContentBlock> = {},
): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "bash",
    toolArgs: {
      command: "echo ok",
    },
    argumentsText: "",
    toolStatus: "success",
    resultText: "ok",
    ...overrides,
  };
}

describe("curl tool block", () => {
  it("shows curl arguments in the inline title", () => {
    expect(buildToolInlineModel(curlBlock()).title).toBe(
      "-L 'https://www.baidu.com/s?wd=蛋仔派对'",
    );
  });

  it("hides the curl exit code inline", () => {
    expect(buildToolInlineModel(curlBlock()).meta).toBeUndefined();
  });

  it("formats the expanded curl command", () => {
    expect(buildToolDetailModel(curlBlock()).command).toBe(
      "$ curl -L 'https://www.baidu.com/s?wd=蛋仔派对'",
    );
  });

  it("shows stderr from details when historical stdout is empty", () => {
    expect(
      buildToolDetailModel(
        curlBlock({
          toolStatus: "error",
          resultDetails: { stderr: "curl: (77) missing CA", exitCode: 77 },
        }),
      ).text,
    ).toBe("curl: (77) missing CA");
  });

  it("hides non-zero curl exit metadata", () => {
    expect(
      buildToolInlineModel(
        curlBlock({
          toolStatus: "error",
          resultDetails: { stderr: "failed", exitCode: 77 },
        }),
      ).meta,
    ).toBeUndefined();
  });
});

describe("bash tool block", () => {
  it("hides successful exit metadata because exit 0 is not useful inline", () => {
    expect(buildToolInlineModel(bashBlock()).meta).toBeUndefined();
  });

  it("hides explicit zero exit text from historical bash results", () => {
    expect(
      buildToolInlineModel(
        bashBlock({
          resultText: "ok\nCommand exited with code 0",
        }),
      ).meta,
    ).toBeUndefined();
  });

  it("hides non-zero exit metadata for failed bash calls", () => {
    expect(
      buildToolInlineModel(
        bashBlock({
          toolStatus: "error",
          resultText: "failed\nCommand exited with code 2",
        }),
      ).meta,
    ).toBeUndefined();
  });

  it("keeps bash timeout metadata without adding exit 0", () => {
    expect(
      buildToolInlineModel(
        bashBlock({
          toolArgs: { command: "sleep 1", timeout: 30 },
        }),
      ).meta,
    ).toBe("timeout 30s");
  });

  it("does not infer exit metadata for pending bash calls", () => {
    expect(
      buildToolInlineModel(
        bashBlock({
          toolStatus: "pending",
          resultText: undefined,
        }),
      ).meta,
    ).toBeUndefined();
  });
});
