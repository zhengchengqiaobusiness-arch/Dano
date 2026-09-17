import { readFileSync, writeFileSync } from "node:fs";
import { chmod, lstat, mkdtemp, readFile, realpath, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  checkDeployedSystemPrompt,
  syncDeployedSystemPrompt,
  renderSystemPrompt,
  resolveProductName,
  writeSystemPromptFile,
} from "../../runtime/system-prompt.mjs";

const tempDirs: string[] = [];

afterEach(async () => {
  await Promise.all(
    tempDirs.splice(0).map(path => rm(path, { recursive: true, force: true })),
  );
});

describe("system prompt runtime", () => {
  it("uses the environment override before the configured product name", () => {
    expect(resolveProductName(" 环境助手 ", "配置助手")).toBe("环境助手");
    expect(resolveProductName("", " 配置助手 ")).toBe("配置助手");
    expect(() => resolveProductName("", "")).toThrow(
      "Set productName in dano.config.json or provide DANO_PRODUCT_NAME",
    );
  });

  it("renders every product-name placeholder", () => {
    expect(
      renderSystemPrompt("你是{产品名称}，请联系{产品名称}", "测试助手"),
    ).toBe("你是测试助手，请联系测试助手");
  });

  it("initializes without replacing an existing host-managed file", async () => {
    const root = await mkdtemp(join(tmpdir(), "dano-system-prompt-"));
    tempDirs.push(root);
    const targetPath = join(root, "SYSTEM.md");

    await expect(
      writeSystemPromptFile(targetPath, "首次内容", { mode: "if-missing" }),
    ).resolves.toBe("written");
    writeFileSync(targetPath, "宿主机内容");
    await expect(
      writeSystemPromptFile(targetPath, "第二次内容", { mode: "if-missing" }),
    ).resolves.toBe("preserved");

    expect(await readFile(targetPath, "utf8")).toBe("宿主机内容");
  });

  it("publishes exactly one complete file when initializers race", async () => {
    const root = await mkdtemp(join(tmpdir(), "dano-system-prompt-"));
    tempDirs.push(root);
    const targetPath = join(root, "SYSTEM.md");
    const candidates = Array.from(
      { length: 12 },
      (_, index) => `并发内容-${index}-结束`,
    );

    const results = await Promise.all(
      candidates.map(content =>
        writeSystemPromptFile(targetPath, content, { mode: "if-missing" }),
      ),
    );
    const finalContent = await readFile(targetPath, "utf8");

    expect(results.filter(result => result === "written")).toHaveLength(1);
    expect(results.filter(result => result === "preserved")).toHaveLength(11);
    expect(candidates).toContain(finalContent);
    expect(finalContent).toMatch(/^并发内容-\d+-结束$/);
  });

  it("atomically replaces a system prompt during an explicit deployment sync", async () => {
    const root = await mkdtemp(join(tmpdir(), "dano-system-prompt-"));
    tempDirs.push(root);
    const targetPath = join(root, "SYSTEM.md");
    writeFileSync(targetPath, "旧名称");

    await expect(
      writeSystemPromptFile(targetPath, "新名称", { mode: "replace" }),
    ).resolves.toBe("written");

    expect(readFileSync(targetPath, "utf8")).toBe("新名称");
  });
});


describe("deployment SYSTEM gate", () => {
  async function fixture() {
    const root = await realpath(await mkdtemp(join(tmpdir(), "dano-system-gate-")));
    tempDirs.push(root);
    const templatePath = join(root, "template.md");
    writeFileSync(templatePath, "你是{产品名称}。再次：{产品名称}。\n");
    return { templatePath, targetPath: join(root, "SYSTEM.md"), agentDir: root,
      productName: "有效助手", owner: { uid: process.getuid!(), gid: process.getgid!() } };
  }
  it("repairs old placeholders atomically and enforces private permissions", async () => {
    const options = await fixture();
    writeFileSync(options.targetPath, "旧{产品名称}", { mode: 0o644 });
    const before = await lstat(options.targetPath);
    await syncDeployedSystemPrompt(options);
    await checkDeployedSystemPrompt(options);
    const after = await lstat(options.targetPath);
    expect(after.ino).not.toBe(before.ino);
    expect(after.mode & 0o777).toBe(0o600);
    expect(after.uid).toBe(options.owner.uid);
  });
  it.each(["残留{产品名称}", "错误助手"])("rejects inconsistent content without emitting it", async content => {
    const options = await fixture();
    writeFileSync(options.targetPath, content, { mode: 0o600 });
    await expect(checkDeployedSystemPrompt(options)).rejects.toThrow(/SYSTEM_(PLACEHOLDER|CONTENT)/);
  });
  it("rejects missing names and placeholder names before replacing a file", async () => {
    const options = await fixture();
    writeFileSync(options.targetPath, "preserve");
    for (const productName of ["", "{产品名称}"]) {
      await expect(syncDeployedSystemPrompt({ ...options, productName })).rejects.toThrow();
      expect(await readFile(options.targetPath, "utf8")).toBe("preserve");
    }
  });
  it("treats dollar replacement sequences as literal product names", () => {
    expect(renderSystemPrompt("你是{产品名称}", "$& $` $' $$")).toBe("你是$& $` $' $$");
  });
  it("rejects unexpected owner, permissions, target and symlinks", async () => {
    const options = await fixture();
    await syncDeployedSystemPrompt(options);
    await expect(checkDeployedSystemPrompt({ ...options, owner: { ...options.owner, uid: options.owner.uid + 1 } })).rejects.toThrow("SYSTEM_DIRECTORY_METADATA");
    await chmod(options.targetPath, 0o644);
    await expect(checkDeployedSystemPrompt(options)).rejects.toThrow("SYSTEM_PERMISSIONS");
    await expect(syncDeployedSystemPrompt({ ...options, targetPath: join(options.agentDir, "other") })).rejects.toThrow("SYSTEM_PATH");
    await rm(options.targetPath);
    await symlink(options.templatePath, options.targetPath);
    await expect(syncDeployedSystemPrompt(options)).rejects.toThrow("SYSTEM_PATH");
    expect(await readFile(options.templatePath, "utf8")).toContain("{产品名称}");
    await rm(options.targetPath);
    const alias = `${options.agentDir}-alias`;
    tempDirs.push(alias);
    await symlink(options.agentDir, alias);
    await expect(syncDeployedSystemPrompt({ ...options, agentDir: alias, targetPath: join(alias, "SYSTEM.md") })).rejects.toThrow("SYSTEM_PATH");
  });
});
