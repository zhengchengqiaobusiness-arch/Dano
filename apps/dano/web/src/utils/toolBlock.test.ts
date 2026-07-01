import { describe, expect, it } from "vitest";
import {
  buildToolDetailModel,
  buildToolInlineModel,
  classifyReadToolBlock,
} from "./toolBlock";
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

function readBlock(
  overrides: Partial<ToolContentBlock> = {},
): ToolContentBlock {
  return {
    kind: "tool",
    toolName: "read",
    toolArgs: {
      path: "/tmp/dano/.agents/skills/dano-a-oa-submit-form-vvvv/SKILL.md",
    },
    argumentsText: "",
    toolStatus: "success",
    resultText: "---\nname: 请假流程\ndescription: 请假提交\n---\nbody",
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

describe("read skill block", () => {
  it("uses the skill frontmatter name for SKILL.md cards", () => {
    expect(classifyReadToolBlock(readBlock())).toEqual({
      kind: "skill",
      label: "请假流程",
    });
  });

  it("accepts quoted skill frontmatter names", () => {
    expect(
      classifyReadToolBlock(
        readBlock({
          resultText: "---\nname: \"请假流程\"\n---\nbody",
        }),
      )?.label,
    ).toBe("请假流程");
  });

  it("falls back to the skill directory when frontmatter has no name", () => {
    expect(
      classifyReadToolBlock(
        readBlock({
          resultText: "---\ndescription: 请假提交\n---\nbody",
        }),
      )?.label,
    ).toBe("dano-a-oa-submit-form-vvvv");
  });

  it("supports read tools that use file_path", () => {
    expect(
      classifyReadToolBlock(
        readBlock({
          toolArgs: {
            file_path:
              "/tmp/dano/.agents/skills/dano-a-oa-submit-form-vvvv/SKILL.md",
          },
        }),
      )?.label,
    ).toBe("请假流程");
  });

  it("does not classify non-skill reads", () => {
    expect(
      classifyReadToolBlock(
        readBlock({
          toolArgs: { path: "/tmp/dano/.agents/skills/README.md" },
        }),
      ),
    ).toBeNull();
  });
});
