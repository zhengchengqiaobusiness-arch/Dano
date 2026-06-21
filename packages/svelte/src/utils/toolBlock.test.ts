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

describe("curl tool block", () => {
  it("shows curl arguments in the inline title", () => {
    expect(buildToolInlineModel(curlBlock()).title).toBe(
      "-L 'https://www.baidu.com/s?wd=蛋仔派对'",
    );
  });

  it("shows the curl exit code as inline metadata", () => {
    expect(buildToolInlineModel(curlBlock()).meta).toBe("exit 0");
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

  it("shows non-zero curl exit metadata", () => {
    expect(
      buildToolInlineModel(
        curlBlock({
          toolStatus: "error",
          resultDetails: { stderr: "failed", exitCode: 77 },
        }),
      ).meta,
    ).toBe("exit 77");
  });
});
