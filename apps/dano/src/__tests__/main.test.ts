import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  createDanoDevReloadController,
  resolveDanoDevWatchPath,
} from "../dev-reload.js";
import {
  initializeDanoWorkspaceSettings,
  parseDanoServerOptions,
  readDanoPackageInfo,
  resolveDefaultStaticDir,
} from "../main.js";

describe("Dano main", () => {
  it("ships bash behind the pinned Heimdall guards", () => {
    const runtimeDefaultsDir = resolve("deploy/runtime-defaults");
    const appPackage = JSON.parse(
      readFileSync(resolve("apps/dano/package.json"), "utf8"),
    ) as { dependencies?: Record<string, string> };
    const workspaceConfig = readFileSync(resolve("pnpm-workspace.yaml"), "utf8");
    const heimdall = JSON.parse(
      readFileSync(join(runtimeDefaultsDir, "heimdall.json"), "utf8"),
    ) as {
      sandbox?: { enabled?: boolean; userNamespace?: boolean };
      commandPolicies?: Array<{ blocked?: string[] }>;
    };
    const heimdallPatchPath = workspaceConfig.match(
      /'@casualjim\/pi-heimdall@0\.2\.10':\s*(\S+)/,
    )?.[1];
    const heimdallPatch = readFileSync(resolve(heimdallPatchPath ?? ""), "utf8");

    expect(appPackage.dependencies?.["@casualjim/pi-heimdall"]).toBe("0.2.10");
    expect(appPackage.dependencies?.["@earendil-works/pi-coding-agent"]).toBe(
      "0.80.2",
    );
    expect(appPackage.dependencies?.["@mariozechner/pi-coding-agent"]).toBe(
      "npm:@earendil-works/pi-coding-agent@0.80.2",
    );
    expect(heimdall.sandbox?.enabled).toBe(true);
    expect(heimdall.sandbox?.userNamespace).toBe(false);
    expect(heimdallPatchPath).toBe("patches/@casualjim__pi-heimdall@0.2.10.patch");
    expect(heimdallPatch).toContain(
      'if (config.userNamespace) args.push("--unshare-user");',
    );
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
    expect(options.defaultWorkspacePath).toBe("/tmp/dano");
    expect(options.sessionsRootPath).toBe("/tmp/dano/.dano/sessions");
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

  it("uses the default workspace from environment", () => {
    const options = parseDanoServerOptions([], {
      DANO_DEFAULT_WORKSPACE_PATH: "/tmp/custom-dano",
    });

    expect(options.defaultWorkspacePath).toBe("/tmp/custom-dano");
    expect(options.sessionsRootPath).toBe("/tmp/custom-dano/.dano/sessions");
  });

  it("lets command line default workspace override environment", () => {
    const options = parseDanoServerOptions(
      ["--default-workspace", "/tmp/cli-dano"],
      { DANO_DEFAULT_WORKSPACE_PATH: "/tmp/env-dano" },
    );

    expect(options.defaultWorkspacePath).toBe("/tmp/cli-dano");
    expect(options.sessionsRootPath).toBe("/tmp/cli-dano/.dano/sessions");
  });

  it("uses Dano sessions root from environment", () => {
    const options = parseDanoServerOptions([], {
      DANO_DEFAULT_WORKSPACE_PATH: "/tmp/custom-dano",
      DANO_SESSIONS_ROOT: "/tmp/custom-dano-sessions",
    });

    expect(options.defaultWorkspacePath).toBe("/tmp/custom-dano");
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

  it("initializes runtime settings in the Dano workspace", () => {
    const sourceRoot = mkdtempSync(join(tmpdir(), "dano-main-source-"));
    const workspaceRoot = mkdtempSync(join(tmpdir(), "dano-main-workspace-"));

    try {
      const nestedSourceDir = join(sourceRoot, "apps", "dano");
      const runtimeDefaultsDir = join(sourceRoot, "deploy", "runtime-defaults");
      mkdirSync(nestedSourceDir, { recursive: true });
      mkdirSync(runtimeDefaultsDir, { recursive: true });
      writeFileSync(join(runtimeDefaultsDir, "SYSTEM.md"), "system prompt");
      writeFileSync(join(runtimeDefaultsDir, "settings.json"), "{}");
      writeFileSync(join(runtimeDefaultsDir, "heimdall.json"), "{}");

      initializeDanoWorkspaceSettings(workspaceRoot, nestedSourceDir);

      expect(readFileSync(join(workspaceRoot, ".pi/SYSTEM.md"), "utf8")).toBe(
        "system prompt",
      );
      expect(readFileSync(join(workspaceRoot, ".pi/settings.json"), "utf8")).toBe(
        "{}",
      );
      expect(readFileSync(join(workspaceRoot, ".pi/heimdall.json"), "utf8")).toBe(
        '{\n  "sandbox": {\n    "userNamespace": false\n  }\n}\n',
      );
    } finally {
      rmSync(sourceRoot, { recursive: true, force: true });
      rmSync(workspaceRoot, { recursive: true, force: true });
    }
  });

  it("keeps existing Dano workspace settings", () => {
    const sourceRoot = mkdtempSync(join(tmpdir(), "dano-main-source-"));
    const workspaceRoot = mkdtempSync(join(tmpdir(), "dano-main-workspace-"));

    try {
      mkdirSync(join(sourceRoot, "deploy", "runtime-defaults"), {
        recursive: true,
      });
      mkdirSync(join(workspaceRoot, ".pi"), { recursive: true });
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
      writeFileSync(join(workspaceRoot, ".pi/SYSTEM.md"), "workspace prompt");

      initializeDanoWorkspaceSettings(workspaceRoot, sourceRoot);

      expect(readFileSync(join(workspaceRoot, ".pi/SYSTEM.md"), "utf8")).toBe(
        "workspace prompt",
      );
      expect(readFileSync(join(workspaceRoot, ".pi/settings.json"), "utf8")).toBe(
        "{}",
      );
      expect(readFileSync(join(workspaceRoot, ".pi/heimdall.json"), "utf8")).toBe(
        '{\n  "sandbox": {\n    "userNamespace": false\n  }\n}\n',
      );
    } finally {
      rmSync(sourceRoot, { recursive: true, force: true });
      rmSync(workspaceRoot, { recursive: true, force: true });
    }
  });

  it("preserves Heimdall runtime settings while disabling user namespaces by default", () => {
    const sourceRoot = mkdtempSync(join(tmpdir(), "dano-main-source-"));
    const workspaceRoot = mkdtempSync(join(tmpdir(), "dano-main-workspace-"));

    try {
      mkdirSync(join(sourceRoot, "deploy", "runtime-defaults"), {
        recursive: true,
      });
      mkdirSync(join(workspaceRoot, ".pi"), { recursive: true });
      writeFileSync(
        join(sourceRoot, "deploy/runtime-defaults/heimdall.json"),
        "{}",
      );
      writeFileSync(
        join(workspaceRoot, ".pi/heimdall.json"),
        JSON.stringify({
          sandbox: { enabled: true },
          commandPolicies: [{ name: "keep", blocked: ["x"], message: "y" }],
        }),
      );

      initializeDanoWorkspaceSettings(workspaceRoot, sourceRoot);

      expect(
        JSON.parse(readFileSync(join(workspaceRoot, ".pi/heimdall.json"), "utf8")),
      ).toEqual({
        sandbox: { enabled: true, userNamespace: false },
        commandPolicies: [{ name: "keep", blocked: ["x"], message: "y" }],
      });
    } finally {
      rmSync(sourceRoot, { recursive: true, force: true });
      rmSync(workspaceRoot, { recursive: true, force: true });
    }
  });
});
