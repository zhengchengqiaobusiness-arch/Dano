import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { loadDanoConfig, readShortcutLinks } from "../dano-config.js";

describe("Dano config", () => {
  it("keeps ordered same-origin shortcut links and rejects unsafe destinations/styles", () => {
    const valid = { label: "介绍", href: "/web", color: "#007aff" };
    expect(readShortcutLinks([
      { label: " 介绍 ", href: " /web ", color: " #007aff " },
      null,
      { ...valid, href: "javascript:alert(1)" },
      { ...valid, href: "//example.com" },
      { ...valid, href: "/\\example.com" },
      { ...valid, href: "/\n/example.com" },
      { ...valid, color: "red; background:url(https://example.com)" },
      { ...valid, label: "" },
      { label: "平台", href: "/oa", color: "#ff9500" },
    ])).toEqual([valid, { label: "平台", href: "/oa", color: "#ff9500" }]);
    expect(readShortcutLinks([])).toEqual([]);
    expect(readShortcutLinks(undefined)).toBeUndefined();
  });
  it("leaves Pi-owned session defaults out of Dano config", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-config-"));
    const configPath = path.join(tmpDir, "dano.config.json");
    fs.writeFileSync(
      configPath,
      JSON.stringify({
        productName: " 测试助手 ",
        defaultProvider: "xiaomi-token-plan-cn",
        defaultModel: "mimo-v2.6-flash",
        defaultThinkingLevel: "medium",
        defaultProjectTrust: "always",
        fieldAssist: {
          maxRetries: 10,
        },
        askUserQuestion: {
          maxRetries: 12,
          defaultTitle: " 信息收集 ",
        },
        slashCommandsAndMentionsEnabled: true,
        transcriptProcessSummaryEnabled: true,
        quickActions: [
          { label: " 请假 ", prompt: " 帮我申请请假 " },
          { label: "", prompt: "ignored" },
          { label: "ignored", prompt: " " },
        ],
        shortcutLinks: [{ label: " 介绍 ", href: " /web ", color: " #007aff ", target: "_self" }],
      }),
    );

    expect(
      loadDanoConfig({
        cwd: tmpDir,
        env: { DANO_CONFIG_PATH: configPath },
      }),
    ).toEqual({
      productName: "测试助手",
      fieldAssist: {
        maxRetries: 10,
      },
      askUserQuestion: {
        maxRetries: 12,
        defaultTitle: "信息收集",
      },
      slashCommandsAndMentionsEnabled: true,
      transcriptProcessSummaryEnabled: true,
      quickActions: [{ label: "请假", prompt: "帮我申请请假" }],
      shortcutLinks: [{ label: "介绍", href: "/web", color: "#007aff", target: "_self" }],
    });

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns an empty config when no Dano config file exists", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-no-config-"));

    expect(
      loadDanoConfig({
        cwd: tmpDir,
        env: { DANO_CONFIG_PATH: path.join(tmpDir, "missing.json") },
        startDir: tmpDir,
      }),
    ).toEqual({
      slashCommandsAndMentionsEnabled: false,
      transcriptProcessSummaryEnabled: false,
    });

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it.each([
    ["true", true],
    ["TRUE", true],
    ["1", true],
    ["false", false],
    ["FALSE", false],
    ["0", false],
  ])(
    "lets DANO_SLASH_COMMANDS_AND_MENTIONS_ENABLED=%s override the config file",
    (value, expected) => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-config-"));
      const configPath = path.join(tmpDir, "dano.config.json");
      fs.writeFileSync(
        configPath,
        JSON.stringify({ slashCommandsAndMentionsEnabled: !expected }),
      );

      expect(
        loadDanoConfig({
          cwd: tmpDir,
          env: {
            DANO_CONFIG_PATH: configPath,
            DANO_SLASH_COMMANDS_AND_MENTIONS_ENABLED: value,
          },
        }).slashCommandsAndMentionsEnabled,
      ).toBe(expected);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    },
  );

  it("rejects an invalid slash command environment value", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-config-"));

    expect(() =>
      loadDanoConfig({
        cwd: tmpDir,
        env: { DANO_SLASH_COMMANDS_AND_MENTIONS_ENABLED: "sometimes" },
        startDir: tmpDir,
      }),
    ).toThrow(
      'DANO_SLASH_COMMANDS_AND_MENTIONS_ENABLED has invalid value "sometimes"',
    );

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});
