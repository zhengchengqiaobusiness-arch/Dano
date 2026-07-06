import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  createDanoDevReloadController,
  resolveDanoDevWatchPath,
} from "../dev-reload.js";
import {
  initializeDanoAgentSettings,
  parseDanoServerOptions,
  readDanoPackageInfo,
  resolveDefaultStaticDir,
} from "../main.js";

describe("Dano main", () => {
  it("ships bash with pinned Heimdall guards", () => {
    const runtimeDefaultsDir = resolve("deploy/runtime-defaults");
    const appPackage = JSON.parse(
      readFileSync(resolve("apps/dano/package.json"), "utf8"),
    ) as { dependencies?: Record<string, string> };
    const appRequire = createRequire(resolve("apps/dano/package.json"));
    const heimdallPackage = JSON.parse(
      readFileSync(
        appRequire.resolve("@josephyoung/pi-heimdall/package.json"),
        "utf8",
      ),
    ) as { peerDependencies?: Record<string, string> };
    const sandboxGuard = readFileSync(
      appRequire.resolve("@josephyoung/pi-heimdall/guards/sandbox-guard.ts"),
      "utf8",
    );
    const heimdall = JSON.parse(
      readFileSync(join(runtimeDefaultsDir, "heimdall.json"), "utf8"),
    ) as {
      sandbox?: {
        enabled?: boolean;
        userNamespace?: boolean;
        env?: { allow?: string[]; deny?: string[] };
        paths?: Record<string, { mode?: string }>;
      };
      commandPolicies?: Array<{ blocked?: string[] }>;
    };

    expect(appPackage.dependencies?.["@josephyoung/pi-heimdall"]).toBe(
      "0.2.15",
    );
    expect(appPackage.dependencies?.["@earendil-works/pi-coding-agent"]).toBe(
      "0.80.2",
    );
    expect(
      appPackage.dependencies?.["@mariozechner/pi-coding-agent"],
    ).toBeUndefined();
    expect(heimdallPackage.peerDependencies).toEqual({
      "@earendil-works/pi-coding-agent": "*",
    });
    expect(heimdall.sandbox?.enabled).toBe(true);
    expect(heimdall.sandbox?.userNamespace).toBe(false);
    expect(heimdall.sandbox?.paths?.[".pi"]).toEqual({ mode: "deny" });
    expect(heimdall.sandbox?.paths?.["~/.pi"]).toEqual({ mode: "deny" });
    expect(heimdall.sandbox?.env?.allow).toEqual(
      expect.arrayContaining(["DANO_URL", "DANO_TENANT_KEY"]),
    );
    expect(heimdall.sandbox?.env?.deny).toEqual([]);
    expect(sandboxGuard).toContain(
      'if (config.userNamespace) args.push("--unshare-user");',
    );
    expect(sandboxGuard).toContain("HEIMDALL_BWRAP_BIND_KERNEL_FS");
    expect(sandboxGuard).toContain("HEIMDALL_BWRAP_BIND_ROOT");
    expect(sandboxGuard).toContain("function bwrapBindRoot()");
    expect(sandboxGuard).toContain("writeMounts.push(bindRoot");
    expect(sandboxGuard).toContain("function protectedConfigBashBlockReason");
    expect(sandboxGuard).toContain("protectHeimdallConfigPaths");
    expect(sandboxGuard).toContain("Blocked: bash cannot run because Heimdall Protected Configuration cannot be hidden without an active sandbox");
    expect(sandboxGuard).toContain("Blocked: ${event.toolName} attempted to ${operation} Heimdall Protected Configuration.");
    expect(sandboxGuard).toContain('args.push("--dev-bind", "/dev", "/dev");');
    expect(sandboxGuard).toContain('args.push("--ro-bind", "/proc", "/proc");');
    expect(heimdall.commandPolicies).toContainEqual(
      expect.objectContaining({ blocked: ["rm", "-rf", "/"] }),
    );
  });

  it("reloads source runs from the app src without treating builds as dev", () => {
    expect(
      resolveDanoDevWatchPath(
        join("/tmp", "repo", "apps", "dano", "src", "main.ts"),
      ),
    ).toBe(resolve("/tmp/repo/apps/dano/src"));
    expect(
      resolveDanoDevWatchPath(
        join("/tmp", "repo", "apps", "dano", "dist", "server", "main.js"),
      ),
    ).toBeUndefined();
  });

  it("watches only Dano server source and type directories for dev reload", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-watch-"));
    try {
      const srcDir = join(root, "apps", "dano", "src");
      const typesDir = join(root, "apps", "dano", "types");
      mkdirSync(srcDir, { recursive: true });
      mkdirSync(typesDir, { recursive: true });
      mkdirSync(join(root, "apps", "dano", "web"), { recursive: true });
      mkdirSync(join(root, "apps", "dano", "dist"), { recursive: true });
      mkdirSync(join(root, "packages", "bridge"), { recursive: true });

      const controller = createDanoDevReloadController({
        entryFile: join(srcDir, "main.ts"),
        stop: () => {},
        logger: { log: () => {}, error: () => {} },
      });

      expect(controller?.watchPaths).toEqual([srcDir, typesDir]);
      controller?.dispose();
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("polls source files so dev reload works when OS watchers are unavailable", async () => {
    const root = mkdtempSync(join(tmpdir(), "dano-watch-poll-"));
    let timeout: NodeJS.Timeout | undefined;

    try {
      const srcDir = join(root, "apps", "dano", "src");
      const typesDir = join(root, "apps", "dano", "types");
      mkdirSync(srcDir, { recursive: true });
      mkdirSync(typesDir, { recursive: true });
      const sourceFile = join(srcDir, "main.ts");
      writeFileSync(sourceFile, "export const value = 1;\n");
      writeFileSync(join(typesDir, "protocol.ts"), "export type Value = 1;\n");

      const reloaded = new Promise<void>((resolve, reject) => {
        timeout = setTimeout(
          () => reject(new Error("Timed out waiting for dev reload")),
          1000,
        );
        const controller = createDanoDevReloadController({
          entryFile: sourceFile,
          stop: () => {
            clearTimeout(timeout);
            controller?.dispose();
            resolve();
          },
          debounceMs: 0,
          pollIntervalMs: 10,
          logger: { log: () => {}, error: () => {} },
        });
      });

      writeFileSync(sourceFile, "export const value = 2;\n");

      await reloaded;
    } finally {
      clearTimeout(timeout);
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("reads the Dano product version from a dev checkout", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-package-dev-"));
    try {
      const appRoot = join(root, "apps", "dano");
      mkdirSync(appRoot, { recursive: true });
      writeFileSync(
        join(root, "package.json"),
        '{"name":"@dano/dano","version":"0.1.0"}\n',
      );
      writeFileSync(
        join(appRoot, "package.json"),
        '{"name":"@dano/app","version":"0.3.4"}\n',
      );

      expect(readDanoPackageInfo(appRoot)).toEqual({
        name: "@dano/dano",
        version: "0.1.0",
      });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("reads the Dano product version from the packaged runtime", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-package-prod-"));
    try {
      mkdirSync(join(root, "package-versions"), { recursive: true });
      writeFileSync(
        join(root, "package-versions", "package.json"),
        '{"name":"@dano/dano","version":"0.1.0"}\n',
      );
      writeFileSync(
        join(root, "package.json"),
        '{"name":"@dano/app","version":"0.3.4"}\n',
      );

      expect(readDanoPackageInfo(root)).toEqual({
        name: "@dano/dano",
        version: "0.1.0",
      });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("parses the optional port override", () => {
    const options = parseDanoServerOptions(["--port", "8123"], {});

    expect(options.cwd).toBe(process.cwd());
    expect(options.host).toBe("0.0.0.0");
    expect(options.port).toBe(8123);
    expect(options.defaultWorkspacePath).toMatch(
      /^\/opt\/dano\/runtime-data\/workspaces\/ws_[0-9a-f-]{36}$/,
    );
    expect(options.sessionsRootPath).toBe(
      `${options.defaultWorkspacePath}/.dano/sessions`,
    );
    expect(options.staticDir).toBe(
      resolveDefaultStaticDir(resolve("apps/dano/src/main.ts")),
    );
    expect(options.help).toBe(false);
  });

  it("resolves source entry static files to the app web build", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-static-source-"));
    try {
      const webDir = join(root, "apps", "dano", "dist", "web");
      mkdirSync(webDir, { recursive: true });
      writeFileSync(join(webDir, "index.html"), "");

      expect(
        resolveDefaultStaticDir(
          join(root, "apps", "dano", "src", "main.ts"),
        ),
      ).toBe(webDir);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("resolves built entry static files to the app web build", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-static-built-"));
    try {
      const webDir = join(root, "apps", "dano", "dist", "web");
      mkdirSync(webDir, { recursive: true });
      writeFileSync(join(webDir, "index.html"), "");

      expect(
        resolveDefaultStaticDir(
          join(root, "apps", "dano", "dist", "server", "main.js"),
        ),
      ).toBe(webDir);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("resolves Docker-style built entry static files beside dist/server", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-static-docker-"));
    try {
      const webDir = join(root, "app", "dist", "web");
      mkdirSync(webDir, { recursive: true });
      writeFileSync(join(webDir, "index.html"), "");

      expect(
        resolveDefaultStaticDir(join(root, "app", "dist", "server", "main.js")),
      ).toBe(webDir);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("uses DANO_STATIC_DIR instead of the default web build", () => {
    const options = parseDanoServerOptions([], {
      DANO_STATIC_DIR: " custom-web ",
    });

    expect(options.staticDir).toBe(resolve(process.cwd(), "custom-web"));
  });

  it("uses default upload configuration", () => {
    const options = parseDanoServerOptions([], {});

    expect(options.upload).toEqual({
      uploadDir: "/opt/dano/runtime-data/.dano/uploads",
      maxTotalBytes: 10 * 1024 * 1024 * 1024,
      draftTtlMs: 2 * 60 * 60 * 1000,
      referencedTtlMs: 24 * 60 * 60 * 1000,
      orphanedTtlMs: 5 * 60 * 1000,
      cleanupIntervalMs: 60 * 60 * 1000,
    });
  });

  it("resolves the global agent directory from environment or runtime root", () => {
    expect(
      parseDanoServerOptions([], {
        DANO_RUNTIME_DIR: "/tmp/dano-runtime",
        PI_CODING_AGENT_DIR: " /tmp/pi-agent ",
      }).agentConfigDir,
    ).toBe("/tmp/pi-agent");
    expect(
      parseDanoServerOptions([], {
        DANO_RUNTIME_DIR: "/tmp/dano-runtime",
      }).agentConfigDir,
    ).toBe("/tmp/dano-runtime/default-settings/.pi/agent");
  });

  it("uses upload configuration from environment", () => {
    const options = parseDanoServerOptions([], {
      DANO_UPLOAD_DIR: " custom-uploads ",
      DANO_UPLOAD_MAX_TOTAL_BYTES: "123",
      DANO_UPLOAD_DRAFT_TTL_MS: "234",
      DANO_UPLOAD_REFERENCED_TTL_MS: "345",
      DANO_UPLOAD_ORPHANED_TTL_MS: "456",
      DANO_UPLOAD_CLEANUP_INTERVAL_MS: "567",
    });

    expect(options.upload).toEqual({
      uploadDir: resolve(process.cwd(), "custom-uploads"),
      maxTotalBytes: 123,
      draftTtlMs: 234,
      referencedTtlMs: 345,
      orphanedTtlMs: 456,
      cleanupIntervalMs: 567,
    });
  });

  it("falls back to upload defaults for invalid numeric environment values", () => {
    const options = parseDanoServerOptions([], {
      DANO_UPLOAD_MAX_TOTAL_BYTES: "0",
      DANO_UPLOAD_DRAFT_TTL_MS: "-1",
      DANO_UPLOAD_REFERENCED_TTL_MS: "not-a-number",
      DANO_UPLOAD_ORPHANED_TTL_MS: "",
      DANO_UPLOAD_CLEANUP_INTERVAL_MS: "0",
    });

    expect(options.upload).toEqual({
      uploadDir: "/opt/dano/runtime-data/.dano/uploads",
      maxTotalBytes: 10 * 1024 * 1024 * 1024,
      draftTtlMs: 2 * 60 * 60 * 1000,
      referencedTtlMs: 24 * 60 * 60 * 1000,
      orphanedTtlMs: 5 * 60 * 1000,
      cleanupIntervalMs: 60 * 60 * 1000,
    });
  });

  it("keeps the no-static-dir fallback when index.html is missing", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-static-missing-"));
    try {
      mkdirSync(join(root, "apps", "dano", "dist", "web"), { recursive: true });

      expect(
        resolveDefaultStaticDir(
          join(root, "apps", "dano", "src", "main.ts"),
        ),
      ).toBeUndefined();
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("parses host and port overrides", () => {
    const options = parseDanoServerOptions(
      ["--host", "127.0.0.1", "--port", "8123"],
      {},
    );

    expect(options.host).toBe("127.0.0.1");
    expect(options.port).toBe(8123);
  });

  it("uses host and port from environment", () => {
    const options = parseDanoServerOptions([], {
      DANO_HOST: " 127.0.0.1 ",
      DANO_PORT: "8123",
    });

    expect(options.host).toBe("127.0.0.1");
    expect(options.port).toBe(8123);
  });

  it("keeps HOST and PORT compatibility when Dano bind settings are absent", () => {
    const options = parseDanoServerOptions([], {
      HOST: "localhost",
      PORT: "7070",
    });

    expect(options.host).toBe("localhost");
    expect(options.port).toBe(7070);
  });

  it("lets command line host and port override environment", () => {
    const options = parseDanoServerOptions(
      ["--host", "0.0.0.0", "--port", "8088"],
      {
        DANO_HOST: "127.0.0.1",
        DANO_PORT: "8123",
      },
    );

    expect(options.host).toBe("0.0.0.0");
    expect(options.port).toBe(8088);
  });

  it("ignores deprecated default workspace environment when selecting the Runtime Workspace", () => {
    const options = parseDanoServerOptions([], {
      DANO_RUNTIME_DIR: "/tmp/dano-runtime",
      DANO_DEFAULT_WORKSPACE_PATH: "/tmp/custom-dano",
      DANO_DEFAULT_WORKSPACE: "/tmp/legacy-dano",
    });

    expect(options.defaultWorkspacePath).toMatch(
      /^\/tmp\/dano-runtime\/workspaces\/ws_[0-9a-f-]{36}$/,
    );
    expect(options.defaultWorkspacePath).not.toBe("/tmp/custom-dano");
    expect(options.defaultWorkspacePath).not.toBe("/tmp/legacy-dano");
    expect(options.sessionsRootPath).toBe(
      `${options.defaultWorkspacePath}/.dano/sessions`,
    );
  });

  it("accepts deprecated default workspace flags without selecting the Runtime Workspace", () => {
    const options = parseDanoServerOptions(
      ["--default-workspace", "/tmp/cli-dano"],
      { DANO_DEFAULT_WORKSPACE_PATH: "/tmp/env-dano" },
    );

    expect(options.defaultWorkspacePath).toMatch(
      /^\/opt\/dano\/runtime-data\/workspaces\/ws_[0-9a-f-]{36}$/,
    );
    expect(options.defaultWorkspacePath).not.toBe("/tmp/cli-dano");
    expect(options.sessionsRootPath).toBe(
      `${options.defaultWorkspacePath}/.dano/sessions`,
    );
  });

  it("uses Dano sessions root from environment", () => {
    const options = parseDanoServerOptions([], {
      DANO_DEFAULT_WORKSPACE_PATH: "/tmp/custom-dano",
      DANO_SESSIONS_ROOT: "/tmp/custom-dano-sessions",
    });

    expect(options.defaultWorkspacePath).toMatch(
      /^\/opt\/dano\/runtime-data\/workspaces\/ws_[0-9a-f-]{36}$/,
    );
    expect(options.sessionsRootPath).toBe("/tmp/custom-dano-sessions");
  });

  it("keeps PI_WEB_SESSIONS_ROOT compatibility when Dano sessions root is absent", () => {
    const options = parseDanoServerOptions([], {
      DANO_DEFAULT_WORKSPACE_PATH: "/tmp/custom-dano",
      PI_WEB_SESSIONS_ROOT: "/tmp/pi-web-sessions",
    });

    expect(options.sessionsRootPath).toBe("/tmp/pi-web-sessions");
  });

  it("lets command line sessions root override environment", () => {
    const options = parseDanoServerOptions(
      ["--sessions-root", "/tmp/cli-sessions"],
      { DANO_SESSIONS_ROOT: "/tmp/env-sessions" },
    );

    expect(options.sessionsRootPath).toBe("/tmp/cli-sessions");
  });

  it("uses product name and empty text from environment", () => {
    const options = parseDanoServerOptions([], {
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
    const options = parseDanoServerOptions([], {
      DANO_EMPTY_STATE_TEXT: "ignored",
      DANO_EMPTY_STATE_HTML: "<strong>给 {产品名称} 发消息</strong>",
    });

    expect(options.emptyState).toEqual({
      mode: "html",
      content: "<strong>给 {产品名称} 发消息</strong>",
    });
  });

  it("lets command line empty state override environment", () => {
    const options = parseDanoServerOptions(
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
    const options = parseDanoServerOptions(["--help"], {});
    expect(options.help).toBe(true);
  });

  it("throws on missing option value", () => {
    expect(() => parseDanoServerOptions(["--port"], {})).toThrow(
      "Missing value for --port",
    );
  });

  it("throws on missing host value", () => {
    expect(() => parseDanoServerOptions(["--host"], {})).toThrow(
      "Missing value for --host",
    );
  });

  it("throws on missing default workspace value", () => {
    expect(() => parseDanoServerOptions(["--default-workspace"], {})).toThrow(
      "Missing value for --default-workspace",
    );
  });

  it("throws on missing sessions root value", () => {
    expect(() => parseDanoServerOptions(["--sessions-root"], {})).toThrow(
      "Missing value for --sessions-root",
    );
  });

  it("throws on missing empty state values", () => {
    expect(() => parseDanoServerOptions(["--empty-state-text"], {})).toThrow(
      "Missing value for --empty-state-text",
    );
    expect(() => parseDanoServerOptions(["--empty-state-html"], {})).toThrow(
      "Missing value for --empty-state-html",
    );
  });

  it("throws on unknown options", () => {
    expect(() =>
      parseDanoServerOptions(["--cwd", "/tmp/project"], {}),
    ).toThrow(
      "Unknown option: --cwd",
    );
  });

  it("initializes runtime settings in the global agent directory", () => {
    const sourceRoot = mkdtempSync(join(tmpdir(), "dano-main-source-"));
    const workspaceRoot = mkdtempSync(join(tmpdir(), "dano-main-workspace-"));
    const agentRoot = mkdtempSync(join(tmpdir(), "dano-main-agent-"));

    try {
      const nestedSourceDir = join(sourceRoot, "apps", "dano");
      const runtimeDefaultsDir = join(sourceRoot, "deploy", "runtime-defaults");
      mkdirSync(nestedSourceDir, { recursive: true });
      mkdirSync(runtimeDefaultsDir, { recursive: true });
      writeFileSync(join(runtimeDefaultsDir, "SYSTEM.md"), "system prompt");
      writeFileSync(join(runtimeDefaultsDir, "settings.json"), "{}");
      writeFileSync(join(runtimeDefaultsDir, "heimdall.json"), "{}");

      initializeDanoAgentSettings(agentRoot, nestedSourceDir);

      expect(readFileSync(join(agentRoot, "SYSTEM.md"), "utf8")).toBe(
        "system prompt",
      );
      expect(readFileSync(join(agentRoot, "settings.json"), "utf8")).toBe(
        "{}",
      );
      expect(readFileSync(join(agentRoot, "heimdall.json"), "utf8")).toBe(
        '{\n  "sandbox": {\n    "userNamespace": false,\n    "env": {\n      "allow": [\n        "PATH",\n        "HOME",\n        "SHELL",\n        "USER",\n        "LOGNAME",\n        "LANG",\n        "LC_*",\n        "TMPDIR",\n        "DANO_URL",\n        "DANO_TENANT_KEY"\n      ],\n      "deny": []\n    }\n  }\n}\n',
      );
      expect(existsSync(join(workspaceRoot, ".pi"))).toBe(false);
    } finally {
      rmSync(sourceRoot, { recursive: true, force: true });
      rmSync(workspaceRoot, { recursive: true, force: true });
      rmSync(agentRoot, { recursive: true, force: true });
    }
  });

  it("keeps existing global agent settings", () => {
    const sourceRoot = mkdtempSync(join(tmpdir(), "dano-main-source-"));
    const agentRoot = mkdtempSync(join(tmpdir(), "dano-main-agent-"));

    try {
      mkdirSync(join(sourceRoot, "deploy", "runtime-defaults"), {
        recursive: true,
      });
      writeFileSync(
        join(sourceRoot, "deploy/runtime-defaults/SYSTEM.md"),
        "source prompt",
      );
      writeFileSync(
        join(sourceRoot, "deploy/runtime-defaults/settings.json"),
        "{}",
      );
      writeFileSync(
        join(sourceRoot, "deploy/runtime-defaults/heimdall.json"),
        "{}",
      );
      writeFileSync(join(agentRoot, "SYSTEM.md"), "agent prompt");

      initializeDanoAgentSettings(agentRoot, sourceRoot);

      expect(readFileSync(join(agentRoot, "SYSTEM.md"), "utf8")).toBe(
        "agent prompt",
      );
      expect(readFileSync(join(agentRoot, "settings.json"), "utf8")).toBe(
        "{}",
      );
      expect(readFileSync(join(agentRoot, "heimdall.json"), "utf8")).toBe(
        '{\n  "sandbox": {\n    "userNamespace": false,\n    "env": {\n      "allow": [\n        "PATH",\n        "HOME",\n        "SHELL",\n        "USER",\n        "LOGNAME",\n        "LANG",\n        "LC_*",\n        "TMPDIR",\n        "DANO_URL",\n        "DANO_TENANT_KEY"\n      ],\n      "deny": []\n    }\n  }\n}\n',
      );
    } finally {
      rmSync(sourceRoot, { recursive: true, force: true });
      rmSync(agentRoot, { recursive: true, force: true });
    }
  });

  it("preserves Heimdall runtime settings while disabling user namespaces by default", () => {
    const sourceRoot = mkdtempSync(join(tmpdir(), "dano-main-source-"));
    const agentRoot = mkdtempSync(join(tmpdir(), "dano-main-agent-"));

    try {
      mkdirSync(join(sourceRoot, "deploy", "runtime-defaults"), {
        recursive: true,
      });
      writeFileSync(
        join(sourceRoot, "deploy/runtime-defaults/heimdall.json"),
        "{}",
      );
      writeFileSync(
        join(agentRoot, "heimdall.json"),
        JSON.stringify({
          sandbox: {
            enabled: true,
            env: { allow: ["CUSTOM_ENV"], deny: ["*_KEY", "BLOCKED_ENV"] },
          },
          commandPolicies: [{ name: "keep", blocked: ["x"], message: "y" }],
        }),
      );

      initializeDanoAgentSettings(agentRoot, sourceRoot);

      expect(
        JSON.parse(readFileSync(join(agentRoot, "heimdall.json"), "utf8")),
      ).toEqual({
        sandbox: {
          enabled: true,
          userNamespace: false,
          env: {
            allow: expect.arrayContaining([
              "CUSTOM_ENV",
              "DANO_URL",
              "DANO_TENANT_KEY",
            ]),
            deny: ["BLOCKED_ENV"],
          },
        },
        commandPolicies: [{ name: "keep", blocked: ["x"], message: "y" }],
      });
    } finally {
      rmSync(sourceRoot, { recursive: true, force: true });
      rmSync(agentRoot, { recursive: true, force: true });
    }
  });

});
