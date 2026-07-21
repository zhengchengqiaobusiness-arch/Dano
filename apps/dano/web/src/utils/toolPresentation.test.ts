import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildSkillActivity,
  buildToolActivities,
  toolActivityLabel,
} from "./toolPresentation";
import type { ToolContentBlock } from "./transcript";

function toolBlock(
  toolName: string,
  toolStatus: ToolContentBlock["toolStatus"],
  overrides: Partial<ToolContentBlock> = {},
): ToolContentBlock {
  return {
    kind: "tool",
    toolName,
    toolArgs: {},
    argumentsText: "",
    toolStatus,
    ...overrides,
  };
}

describe("Activity Trail presentation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("falls back to count-free copy for invalid activity counts", () => {
    expect(toolActivityLabel("read", "success", 0)).toBe("已查阅资料");
    expect(toolActivityLabel("read", "success", Number.NaN)).toBe("已查阅资料");
    expect(toolActivityLabel("read", "success", Number.POSITIVE_INFINITY)).toBe("已查阅资料");
    expect(toolActivityLabel("read", "success", 1.5)).toBe("已查阅资料");
    expect(toolActivityLabel("external", "success", 2)).toBe("已获取 2 项外部信息");
    expect(toolActivityLabel("process", "pending", 2)).toBe("正在执行 2 条命令");
    expect(toolActivityLabel("generic", "success", 2)).toBe("已处理 2 项任务");
  });

  it("uses user-facing activity copy without exposing tool names", () => {
    const activities = buildToolActivities([
      { key: "read", block: toolBlock("read", "success") },
      { key: "update", block: toolBlock("edit", "success") },
      { key: "external", block: toolBlock("curl", "pending") },
      { key: "process", block: toolBlock("bash", "pending") },
      { key: "unknown", block: toolBlock("internal_sync_v2", "success") },
    ]);

    expect(activities.map(activity => activity.label)).toEqual([
      "已查阅资料",
      "已更新内容",
      "正在获取外部信息",
      "正在执行命令",
      "已处理任务",
    ]);
    expect(JSON.stringify(activities)).not.toContain("internal_sync_v2");
  });

  it("keeps each failed tool's action copy without appending internal names", () => {
    const activities = buildToolActivities([
      { key: "read", block: toolBlock("read", "error", { resultText: "failed" }) },
      { key: "edit", block: toolBlock("edit", "error", { resultText: "failed" }) },
      { key: "write", block: toolBlock("write", "error", { resultText: "failed" }) },
      { key: "curl", block: toolBlock("curl", "error", { resultText: "failed" }) },
      { key: "bash", block: toolBlock("bash", "error", { resultText: "failed" }) },
      {
        key: "question",
        block: toolBlock("ask_user_question", "error", { resultText: "failed" }),
      },
      {
        key: "unknown",
        block: toolBlock("internal_sync_v2", "error", { resultText: "failed" }),
      },
    ]);

    expect(activities.map(activity => activity.label)).toEqual([
      "资料查阅失败",
      "内容更新失败",
      "内容更新失败",
      "外部信息获取失败",
      "命令执行失败",
      "问题卡调用失败",
      "任务处理失败",
    ]);
    expect(JSON.stringify(activities)).not.toContain("ask_user_question");
    expect(JSON.stringify(activities)).not.toContain("internal_sync_v2");
  });

  it("consolidates consecutive work of the same kind with a live count", () => {
    const activities = buildToolActivities([
      { key: "read-1", block: toolBlock("read", "success") },
      { key: "read-2", block: toolBlock("read", "success") },
      { key: "edit", block: toolBlock("edit", "pending") },
      { key: "write", block: toolBlock("write", "pending") },
    ]);

    expect(activities.map(activity => ({
      label: activity.label,
      count: activity.count,
      sourceKeys: activity.sourceKeys,
    }))).toEqual([
      {
        label: "已查阅资料 2 次",
        count: 2,
        sourceKeys: ["read-1", "read-2"],
      },
      {
        label: "正在更新 2 项内容",
        count: 2,
        sourceKeys: ["edit", "write"],
      },
    ]);
  });

  it("exposes only safe object names in expanded details", () => {
    const activities = buildToolActivities([
      {
        key: "read",
        block: toolBlock("read", "success", {
          toolArgs: { path: "/private/company/contracts/采购合同.pdf" },
        }),
      },
      {
        key: "write",
        block: toolBlock("write", "success", {
          toolArgs: { path: "/private/company/output/修改建议.docx", content: "secret" },
        }),
      },
      {
        key: "curl",
        block: toolBlock("curl", "success", {
          toolArgs: { args: ["-L", "https://records.example.com/search?q=secret"] },
        }),
      },
      {
        key: "bash",
        block: toolBlock("bash", "success", {
          toolArgs: { command: "cat /private/company/contracts/采购合同.pdf" },
          resultText: "secret output",
        }),
      },
      {
        key: "unknown",
        block: toolBlock("internal_sync_v2", "success", {
          toolArgs: { token: "secret", target: "/private/company" },
          resultText: "secret output",
        }),
      },
    ]);

    expect(activities.map(activity => activity.details)).toEqual([
      ["采购合同.pdf"],
      ["修改建议.docx"],
      ["records.example.com"],
      ["执行了 cat 命令"],
      [],
    ]);
    expect(JSON.stringify(activities)).not.toContain("/private/company");
    expect(JSON.stringify(activities)).not.toContain("secret");
  });

  it("shows bash executable names without paths or arguments", () => {
    const activities = buildToolActivities([
      {
        key: "bash-1",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command:
              'PATH=/bin "/opt/My Tools/python3" /private/company/dano_call.py --token secret 2>&1 && /bin/ls -la /private/company\n/usr/bin/pwd & /usr/bin/whoami',
          },
          resultText: "secret output",
        }),
      },
    ]);

    expect(activities[0]?.details).toEqual([
      "执行了 python3 命令",
      "执行了 ls 命令",
      "执行了 pwd 命令",
      "执行了 whoami 命令",
    ]);
    expect(JSON.stringify(activities)).not.toContain("/usr/bin");
    expect(JSON.stringify(activities)).not.toContain("/private/company");
    expect(JSON.stringify(activities)).not.toContain("--token");
    expect(JSON.stringify(activities)).not.toContain("secret");
  });

  it("uses a generic detail for heredoc scripts without exposing their data", () => {
    const activities = buildToolActivities([
      {
        key: "bash-heredoc",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: [
              "cat <<'EOF'",
              "not-a-command; /private/company/secret.sh --token secret",
              "EOF",
              "/bin/ls -la /private/company",
            ].join("\n"),
          },
        }),
      },
    ]);

    expect(activities[0]?.details).toEqual(["执行了 Shell 脚本"]);
    expect(JSON.stringify(activities)).not.toContain("secret.sh");
    expect(JSON.stringify(activities)).not.toContain("--token");
  });

  it("ignores command-like text inside shell comments", () => {
    const activities = buildToolActivities([
      {
        key: "bash-comment",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: [
              "echo ok # note; /private/company/secret-tool --token secret",
              "/bin/ls -la /private/company",
            ].join("\n"),
          },
        }),
      },
    ]);

    expect(activities[0]?.details).toEqual([
      "执行了 echo 命令",
      "执行了 ls 命令",
    ]);
    expect(JSON.stringify(activities)).not.toContain("secret-tool");
    expect(JSON.stringify(activities)).not.toContain("--token");
  });

  it("uses a generic detail for commands with leading redirections", () => {
    const activities = buildToolActivities([
      {
        key: "bash-redirection",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: "2>/private/company/payroll.csv /bin/ls -la",
          },
        }),
      },
    ]);

    expect(activities[0]?.details).toEqual(["执行了 Shell 脚本"]);
    expect(JSON.stringify(activities)).not.toContain("payroll.csv");
  });

  it("uses a generic detail for shell control structures", () => {
    const activities = buildToolActivities([
      {
        key: "bash-control",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: "if /bin/test -f x; then /bin/ls; fi\n{ /usr/bin/pwd; }",
          },
        }),
      },
    ]);

    expect(activities[0]?.details).toEqual(["执行了 Shell 脚本"]);
  });

  it("uses a generic detail for case scripts", () => {
    const activities = buildToolActivities([
      {
        key: "bash-case",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: [
              'case "$type" in',
              "json|yaml) /usr/bin/jq file.json ;;&",
              "*) /bin/cat file.json ;;",
              "esac",
            ].join("\n"),
          },
        }),
      },
    ]);

    expect(activities[0]?.details).toEqual(["执行了 Shell 脚本"]);
    expect(JSON.stringify(activities)).not.toContain("json 命令");
    expect(JSON.stringify(activities)).not.toContain("yaml 命令");
  });

  it("does not expose quoted case alternatives containing whitespace", () => {
    const activities = buildToolActivities([
      {
        key: "bash-case-quoted",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: [
              'case "$type" in',
              'payroll|"internal data") /bin/ls ;;',
              "esac",
            ].join("\n"),
          },
        }),
      },
    ]);

    expect(activities[0]?.details).toEqual(["执行了 Shell 脚本"]);
    expect(JSON.stringify(activities)).not.toContain("payroll 命令");
    expect(JSON.stringify(activities)).not.toContain("internal data");
  });

  it("does not present function declarations or compound-assignment data as commands", () => {
    const functionActivities = buildToolActivities([
      {
        key: "bash-function",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: "internal_deploy_secret() { /bin/ls; /bin/pwd; }",
          },
        }),
      },
    ]);
    const assignmentActivities = buildToolActivities([
      {
        key: "bash-array",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: "arr=(payroll.csv); /bin/ls",
          },
        }),
      },
    ]);
    const followingCommandActivities = buildToolActivities([
      {
        key: "bash-function-then-command",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: "prepare() { /bin/pwd; }; /bin/ls -la",
          },
        }),
      },
    ]);
    const braceArgumentActivities = buildToolActivities([
      {
        key: "bash-function-brace-argument",
        block: toolBlock("bash", "success", {
          toolArgs: {
            command: "prepare() { /bin/echo value}; /private/company/secret-tool; }; /bin/ls",
          },
        }),
      },
    ]);

    expect(functionActivities[0]?.details).toEqual(["执行了 Shell 脚本"]);
    expect(assignmentActivities[0]?.details).toEqual(["执行了 Shell 脚本"]);
    expect(followingCommandActivities[0]?.details).toEqual(["执行了 Shell 脚本"]);
    expect(braceArgumentActivities[0]?.details).toEqual(["执行了 Shell 脚本"]);
    expect(JSON.stringify(functionActivities)).not.toContain("internal_deploy_secret");
    expect(JSON.stringify(assignmentActivities)).not.toContain("payroll.csv");
    expect(JSON.stringify(braceArgumentActivities)).not.toContain("secret-tool");
  });

  it("localizes bash activity details", () => {
    vi.stubGlobal("window", { __PI_WEB_CONFIG__: { locale: "en-US" } });

    const activities = buildToolActivities([
      {
        key: "bash-en",
        block: toolBlock("bash", "success", {
          toolArgs: { command: "/bin/ls -la" },
        }),
      },
    ]);

    expect(activities[0]?.details).toEqual(["Ran ls command"]);

    const scriptActivities = buildToolActivities([
      {
        key: "bash-en-script",
        block: toolBlock("bash", "success", {
          toolArgs: { command: "if /bin/test -f x; then /bin/ls; fi" },
        }),
      },
    ]);
    expect(scriptActivities[0]?.details).toEqual(["Ran a shell script"]);
  });

  it("keeps one safe detail per repeated read invocation", () => {
    const activities = buildToolActivities([
      {
        key: "read-1",
        block: toolBlock("read", "success", {
          toolArgs: { path: "/private/one/dano_call.py" },
        }),
      },
      {
        key: "read-2",
        block: toolBlock("read", "success", {
          toolArgs: { path: "/private/two/dano_call.py" },
        }),
      },
      {
        key: "read-3",
        block: toolBlock("read", "success", {
          toolArgs: { path: "/private/three/dano_call.py" },
        }),
      },
    ]);

    expect(activities[0]?.label).toBe("已查阅资料 3 次");
    expect(activities[0]?.details).toEqual([
      "dano_call.py",
      "dano_call.py",
      "dano_call.py",
    ]);
  });

  it("caps detail names at five while preserving known-tool images", () => {
    const sources = Array.from({ length: 6 }, (_, index) => ({
      key: `read-${index + 1}`,
      block: toolBlock("read", "success", {
        toolArgs: { path: `/private/docs/资料-${index + 1}.pdf` },
        ...(index === 0
          ? {
              resultBlocks: [{
                kind: "image" as const,
                src: "data:image/png;base64,preview",
                alt: "资料预览",
              }],
            }
          : {}),
      }),
    }));

    const [activity] = buildToolActivities(sources);

    expect(activity?.details).toEqual([
      "资料-1.pdf",
      "资料-2.pdf",
      "资料-3.pdf",
      "资料-4.pdf",
      "资料-5.pdf",
    ]);
    expect(activity?.overflowCount).toBe(1);
    expect(activity?.images).toEqual([{
      kind: "image",
      src: "data:image/png;base64,preview",
      alt: "图片附件",
    }]);
  });

  it("shows only Chinese skill names from SKILL.md frontmatter", () => {
    const activities = buildToolActivities([
      {
        key: "chinese-skill",
        block: toolBlock("read", "pending", {
          toolArgs: { path: "/skills/leave/SKILL.md" },
          resultText: "---\nname: OA 请假流程\n---\nbody",
        }),
      },
      {
        key: "internal-skill",
        block: toolBlock("read", "success", {
          toolArgs: { path: "/skills/internal-sync/SKILL.md" },
          resultText: "---\nname: internal-sync\n---\nbody",
        }),
      },
      {
        key: "folder-name-only",
        block: toolBlock("read", "pending", {
          toolArgs: { path: "/skills/请假流程/SKILL.md" },
        }),
      },
    ]);

    expect(activities.map(activity => activity.label)).toEqual([
      "正在调用「OA 请假流程」",
      "已调用专业能力",
      "正在调用专业能力",
    ]);
    expect(JSON.stringify(activities)).not.toContain("internal-sync");
    expect(activities.map(activity => activity.details)).toEqual([[], [], []]);
  });

  it("sanitizes standalone skill activity names with the same rule", () => {
    expect(buildSkillActivity("skill-cn", "OA 请假流程").label).toBe("已调用「OA 请假流程」");
    expect(buildSkillActivity("skill-internal", "ask-matt").label).toBe("已调用专业能力");
    expect(JSON.stringify(buildSkillActivity("skill-internal", "ask-matt"))).not.toContain("ask-matt");
  });

  it("hides recovered failures without guessing unresolved failure reasons", () => {
    const activities = buildToolActivities([
      {
        key: "failed-then-retried",
        block: toolBlock("read", "error", {
          toolArgs: { path: "/private/docs/合同.pdf" },
          resultText: "EACCES: permission denied /private/docs/合同.pdf",
        }),
      },
      {
        key: "successful-retry",
        block: toolBlock("read", "success", {
          toolArgs: { path: "/private/docs/合同.pdf" },
          resultText: "secret contract text",
        }),
      },
      {
        key: "unresolved",
        block: toolBlock("read", "error", {
          toolArgs: { path: "/private/docs/付款记录.pdf" },
          resultText: "ECONNREFUSED 10.0.0.8",
        }),
      },
    ]);

    expect(activities.map(activity => ({
      sourceKeys: activity.sourceKeys,
      label: activity.label,
      details: activity.details,
      rawDetails: activity.rawDetails,
    }))).toEqual([
      {
        sourceKeys: ["successful-retry"],
        label: "已查阅资料",
        details: ["合同.pdf"],
        rawDetails: [],
      },
      {
        sourceKeys: ["unresolved"],
        label: "资料查阅失败",
        details: [],
        rawDetails: ["ECONNREFUSED 10.0.0.8"],
      },
    ]);
    expect(JSON.stringify(activities)).not.toContain("网络连接失败");
  });
});
