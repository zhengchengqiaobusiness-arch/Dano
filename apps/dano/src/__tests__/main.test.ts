import { spawnSync } from "node:child_process";
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
  assertRuntimePersistenceWritable,
  initializeDanoAgentSettings,
  parseDanoServerOptions,
  readDanoPackageInfo,
  resolveDefaultStaticDir,
  validateOAuthProviderTls,
} from "../main.js";

function oauthEnvironment(
  overrides: Record<string, string | undefined> = {},
): Record<string, string | undefined> {
  return {
    DANO_OAUTH_ISSUER: "https://provider.example.test",
    DANO_OAUTH_AUTHORIZATION_ENDPOINT:
      "https://provider.example.test/authorize",
    DANO_OAUTH_TOKEN_ENDPOINT: "https://provider.example.test/token",
    DANO_OAUTH_IDENTITY_ENDPOINT: "https://provider.example.test/identity",
    DANO_OAUTH_API_ORIGIN: "https://provider-api.example.test",
    DANO_OAUTH_CLIENT_ID: "dano-client",
    DANO_OAUTH_CLIENT_SECRET: "test-client-secret",
    DANO_OAUTH_SCOPE: "profile offline_access",
    DANO_OAUTH_REDIRECT_URI: "https://dano.example.test/api/auth/callback",
    DANO_OAUTH_CREDENTIAL_KEY: Buffer.alloc(32, 5).toString("base64url"),
    DANO_OAUTH_CREDENTIAL_KEY_VERSION: "key-v1",
    ...overrides,
  };
}

describe("Dano main", () => {
  it("prepares writable Runtime Workspace and session roots", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-persistence-"));
    const runtimeRootPath = join(root, "runtime");
    const defaultWorkspacePath = join(root, "workspace");
    const sessionsRootPath = join(root, "sessions");

    try {
      assertRuntimePersistenceWritable({
        defaultWorkspacePath,
        runtimeRootPath,
        sessionsRootPath,
      });

      expect(existsSync(join(runtimeRootPath, "users"))).toBe(true);
      expect(existsSync(defaultWorkspacePath)).toBe(true);
      expect(existsSync(sessionsRootPath)).toBe(true);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("rejects unusable session persistence before server startup", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-persistence-blocked-"));
    const blockedPath = join(root, "blocked");
    writeFileSync(blockedPath, "not a directory");

    try {
      expect(() =>
        assertRuntimePersistenceWritable({
          defaultWorkspacePath: join(blockedPath, "workspace"),
          runtimeRootPath: join(root, "runtime"),
          sessionsRootPath: join(root, "sessions"),
        }),
      ).toThrow("Dano needs write access for workspace session files");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("exits before listening when the configured default workspace is unusable", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-startup-blocked-"));
    const blockedWorkspacePath = join(root, "blocked-workspace");
    writeFileSync(blockedWorkspacePath, "not a directory");

    try {
      const result = spawnSync(
        process.execPath,
        [
          "--import",
          "jiti/register",
          "./src/main.ts",
          "--port",
          "0",
          "--default-workspace",
          blockedWorkspacePath,
        ],
        {
          cwd: resolve("apps/dano"),
          encoding: "utf8",
          env: {
            ...process.env,
            DANO_PRODUCT_NAME: "Dano test",
            DANO_RUNTIME_DIR: join(root, "runtime"),
            NODE_ENV: "test",
            PI_CODING_AGENT_DIR: join(root, "agent"),
          },
          timeout: 10_000,
        },
      );

      expect(result.status).toBe(1);
      expect(`${result.stdout}${result.stderr}`).toContain(
        `Dano needs write access for workspace session files at ${blockedWorkspacePath}`,
      );
      expect(`${result.stdout}${result.stderr}`).not.toContain("Server URL:");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

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
        paths?: Record<
          string,
          { mode?: string; path?: string } | Array<{ mode?: string; path?: string }>
        >;
      };
      commandPolicies?: Array<{ blocked?: string[] }>;
    };

    expect(appPackage.dependencies?.["@josephyoung/pi-heimdall"]).toBe(
      "0.2.17",
    );
    expect(appPackage.dependencies?.["@earendil-works/pi-coding-agent"]).toBe(
      "0.82.1",
    );
    expect(appPackage.dependencies?.["@earendil-works/pi-ai"]).toBe(
      "0.82.1",
    );
    expect(
      appPackage.dependencies?.["@mariozechner/pi-coding-agent"],
    ).toBeUndefined();
    expect(heimdallPackage.peerDependencies).toEqual({
      "@earendil-works/pi-coding-agent": "*",
    });
    expect(heimdall.sandbox?.enabled).toBe(true);
    expect(heimdall.sandbox?.userNamespace).toBe(false);
    expect(heimdall.sandbox?.paths?.["."]).toEqual([
      { mode: "write" },
      { path: ".pi" },
    ]);
    expect(heimdall.sandbox?.paths?.["/opt"]).toEqual([
      { path: "$DANO_RUNTIME_DIR/.agents/skills" },
    ]);
    expect(
      heimdall.sandbox?.paths?.["$PI_CODING_AGENT_DIR/skills"],
    ).toEqual({});
    expect(heimdall.sandbox?.paths?.["$DANO_RUNTIME_DIR/.pi"]).toEqual({
      mode: "deny",
    });
    expect(heimdall.sandbox?.paths?.["$PI_CODING_AGENT_DIR"]).toEqual({
      mode: "deny",
    });
    expect(heimdall.sandbox?.paths?.["~/.pi"]).toEqual({});
    expect(heimdall.sandbox?.env?.allow).toEqual(
      expect.arrayContaining(["DANO_URL", "DANO_TENANT_KEY"]),
    );
    expect(heimdall.sandbox?.env?.deny).toEqual([]);
    expect(sandboxGuard).toContain(
      'if (config.userNamespace) args.push("--unshare-user");',
    );
    expect(sandboxGuard).toContain("HEIMDALL_BWRAP_BIND_KERNEL_FS");
    expect(sandboxGuard).toContain("HEIMDALL_BWRAP_BIND_PROC");
    expect(sandboxGuard).toContain("HEIMDALL_BWRAP_BIND_ROOT");
    expect(sandboxGuard).toContain("function bwrapBindRoot()");
    expect(sandboxGuard).toContain("writeMounts.push(bindRoot");
    expect(sandboxGuard).toContain("function expandPath(path: string)");
    expect(sandboxGuard).toContain("process.env[name] ?? \"\"");
    expect(sandboxGuard).toContain(
      "const matches = entry.path ? target === entryTarget : pathMatchesPrefix",
    );
    expect(sandboxGuard).toContain(
      "overlayReadMounts.push({ source: target, target });",
    );
    expect(sandboxGuard).toContain('entry.mode === "write" ? "write"');
    expect(sandboxGuard).toContain("function protectedConfigBashBlockReason");
    expect(sandboxGuard).toContain("protectHeimdallConfigPaths");
    expect(sandboxGuard).toContain(
      'getSandboxPathAccess(config, cwd, path).access === "none"',
    );
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
      "/opt/dano/runtime-data/.dano/sessions",
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

  it("uses safe Anonymous User cleanup defaults and explicit deployment overrides", () => {
    expect(parseDanoServerOptions([], {}).anonymousUserCleanup).toEqual({
      idleTtlMs: 24 * 60 * 60 * 1000,
      intervalMs: 60 * 60 * 1000,
    });
    expect(
      parseDanoServerOptions([], {
        DANO_ANONYMOUS_IDLE_TTL_MS: "7200000",
        DANO_ANONYMOUS_CLEANUP_INTERVAL_MS: "300000",
      }).anonymousUserCleanup,
    ).toEqual({ idleTtlMs: 7_200_000, intervalMs: 300_000 });
  });

  it("rejects unsafe Anonymous User cleanup configuration", () => {
    expect(() =>
      parseDanoServerOptions([], { DANO_ANONYMOUS_IDLE_TTL_MS: "0" }),
    ).toThrow("DANO_ANONYMOUS_IDLE_TTL_MS must be a positive integer");
    expect(() =>
      parseDanoServerOptions([], {
        DANO_ANONYMOUS_CLEANUP_INTERVAL_MS: "not-a-number",
      }),
    ).toThrow("DANO_ANONYMOUS_CLEANUP_INTERVAL_MS must be a positive integer");
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
    ).toBe("/tmp/dano-runtime/.pi/agent");
  });

  it("configures trusted JWT User authentication from server environment", () => {
    const options = parseDanoServerOptions([], {
      DANO_RUNTIME_DIR: "/tmp/dano-runtime",
      DANO_AUTH_JWT_SECRET: " signing-secret ",
      DANO_AUTH_JWT_ISSUER: " https://auth.example.test ",
      DANO_AUTH_JWT_AUDIENCE: " dano-web ",
      DANO_AUTH_COOKIE_NAME: " company_session ",
    });

    expect(options.runtimeRootPath).toBe("/tmp/dano-runtime");
    expect(options.userAuthentication).toEqual({
      secret: "signing-secret",
      issuer: "https://auth.example.test",
      audience: "dano-web",
      cookieName: "company_session",
    });
  });

  it("does not invent an authentication identity when JWT verification is unconfigured", () => {
    const options = parseDanoServerOptions([], {
      DANO_RUNTIME_DIR: "/tmp/dano-runtime",
    });

    expect(options.userAuthentication).toBeUndefined();
  });

  it("configures one OAuth confidential client from server environment", () => {
    const key = Buffer.alloc(32, 5).toString("base64url");
    const options = parseDanoServerOptions([], {
      DANO_RUNTIME_DIR: "/tmp/dano-runtime",
      DANO_OAUTH_ISSUER: "https://provider.example.test",
      DANO_OAUTH_AUTHORIZATION_ENDPOINT:
        "https://provider.example.test/authorize",
      DANO_OAUTH_TOKEN_ENDPOINT: "https://provider.example.test/token",
      DANO_OAUTH_IDENTITY_ENDPOINT:
        "https://provider.example.test/identity",
      DANO_OAUTH_IDENTITY_TRANSPORT: "token-introspection",
      DANO_OAUTH_PROFILE_ENDPOINT: "https://provider.example.test/profile",
      DANO_OAUTH_API_ORIGIN: "https://provider-api.example.test",
      DANO_OAUTH_CLIENT_ID: "dano-client",
      DANO_OAUTH_CLIENT_SECRET: "client-secret",
      DANO_OAUTH_CLIENT_AUTH_METHOD: "client_secret_basic",
      DANO_OAUTH_SCOPE: "profile offline_access",
      DANO_OAUTH_PROVIDER_HEADERS_JSON:
        '{"X-Provider-Context":"fixed-context"}',
      DANO_OAUTH_SEND_STATE_TO_TOKEN_ENDPOINT: "true",
      DANO_OAUTH_REVOCATION_TRANSPORT: "delete-query-basic",
      DANO_OAUTH_REVOCATION_ENDPOINT:
        "https://provider.example.test/revoke",
      DANO_OAUTH_REDIRECT_URI:
        "https://dano.example.test/api/auth/callback",
      DANO_OAUTH_CREDENTIAL_KEY: key,
      DANO_OAUTH_CREDENTIAL_KEY_VERSION: "key-v1",
    });

    expect(options.oauthAuthentication).toEqual({
      appOrigin: "https://dano.example.test",
      redirectUri: "https://dano.example.test/api/auth/callback",
      providerApiOrigin: "https://provider-api.example.test",
      provider: {
        issuer: "https://provider.example.test/",
        authorizationEndpoint: "https://provider.example.test/authorize",
        tokenEndpoint: "https://provider.example.test/token",
        identityEndpoint: "https://provider.example.test/identity",
        identityTransport: "token-introspection",
        profileEndpoint: "https://provider.example.test/profile",
        revocation: {
          transport: "delete-query-basic",
          endpoint: "https://provider.example.test/revoke",
        },
        clientId: "dano-client",
        clientSecret: "client-secret",
        clientAuthMethod: "client_secret_basic",
        scope: "profile offline_access",
        requestHeaders: { "x-provider-context": "fixed-context" },
        sendStateToTokenEndpoint: true,
      },
      credentialEncryptionKey: {
        version: "key-v1",
        key: Buffer.alloc(32, 5),
      },
      session: {
        idleTtlMs: 8 * 60 * 60 * 1000,
        absoluteTtlMs: 7 * 24 * 60 * 60 * 1000,
        cleanupIntervalMs: 60 * 60 * 1000,
      },
    });
  });

  it("rejects an unsupported OAuth identity transport", () => {
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_IDENTITY_TRANSPORT: "provider-specific-mode",
      }),
    ).toThrow("OAuth identity transport is unsupported");
  });

  it("requires an explicit opt-in for an HTTP authorization endpoint", () => {
    expect(() =>
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment({
          DANO_OAUTH_AUTHORIZATION_ENDPOINT:
            "http://provider.example.test/authorize",
        }),
      }),
    ).toThrow("OAuth authorization endpoint must use HTTPS");

    const options = parseDanoServerOptions([], {
      NODE_ENV: "production",
      ...oauthEnvironment({
        DANO_OAUTH_AUTHORIZATION_ENDPOINT:
          "http://provider.example.test/authorize",
        DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT: "true",
      }),
    });

    expect(options.oauthAuthentication?.provider).toMatchObject({
      authorizationEndpoint: "http://provider.example.test/authorize",
      allowInsecureAuthorizationEndpoint: true,
      tokenEndpoint: "https://provider.example.test/token",
      identityEndpoint: "https://provider.example.test/identity",
    });
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT: "sometimes",
      }),
    ).toThrow(
      "DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT must be true or false",
    );
  });

  it("allows a SPA Hash route only for the browser authorization endpoint", () => {
    const options = parseDanoServerOptions([], {
      NODE_ENV: "production",
      ...oauthEnvironment({
        DANO_OAUTH_AUTHORIZATION_ENDPOINT:
          "https://provider.example.test/web/#/auth/sso-login",
      }),
    });

    expect(options.oauthAuthentication?.provider.authorizationEndpoint).toBe(
      "https://provider.example.test/web/#/auth/sso-login",
    );
    expect(() =>
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment({
          DANO_OAUTH_TOKEN_ENDPOINT:
            "https://provider.example.test/token#fragment",
        }),
      }),
    ).toThrow("OAuth token endpoint is not trusted");
  });

  it.each(["http://provider.example.test/profile", "https://provider.example.test/profile#fragment"])("validates the optional profile endpoint %s", profileEndpoint => {
    expect(() => parseDanoServerOptions([], oauthEnvironment({
      DANO_OAUTH_PROFILE_ENDPOINT: profileEndpoint,
    }))).toThrow(/OAuth profile endpoint/);
  });

  it("requires an explicit opt-in for plaintext HTTP server endpoints", () => {
    const insecureProvider = {
      DANO_OAUTH_ISSUER: "http://provider.example.test",
      DANO_OAUTH_TOKEN_ENDPOINT: "http://provider.example.test/token",
      DANO_OAUTH_IDENTITY_ENDPOINT: "http://provider.example.test/identity",
      DANO_OAUTH_API_ORIGIN: "http://provider.example.test",
      DANO_OAUTH_REVOCATION_TRANSPORT: "rfc7009",
      DANO_OAUTH_REVOCATION_ENDPOINT: "http://provider.example.test/revoke",
    };

    expect(() =>
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment(insecureProvider),
      }),
    ).toThrow("OAuth issuer must use HTTPS");

    const options = parseDanoServerOptions([], {
      NODE_ENV: "production",
      ...oauthEnvironment({
        ...insecureProvider,
        DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS: "true",
      }),
    });

    expect(options.oauthAuthentication).toMatchObject({
      providerApiOrigin: "http://provider.example.test",
      allowInsecureProviderApiOrigin: true,
      provider: {
        issuer: "http://provider.example.test/",
        tokenEndpoint: "http://provider.example.test/token",
        identityEndpoint: "http://provider.example.test/identity",
        allowInsecureProviderEndpoints: true,
        revocation: {
          transport: "rfc7009",
          endpoint: "http://provider.example.test/revoke",
        },
      },
    });
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS: "sometimes",
      }),
    ).toThrow(
      "DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS must be true or false",
    );
    expect(() =>
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment({
          ...insecureProvider,
          DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS: "true",
          DANO_OAUTH_REDIRECT_URI:
            "http://dano.example.test/api/auth/callback",
        }),
      }),
    ).toThrow("OAuth redirect URI must use trusted HTTPS");
  });

  it("rejects a partially configured OAuth client", () => {
    expect(() =>
      parseDanoServerOptions([], {
        DANO_OAUTH_CLIENT_ID: "only-one-value",
      }),
    ).toThrow("OAuth configuration is incomplete");
  });

  it("validates the OAuth client authentication method", () => {
    expect(
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_CLIENT_AUTH_METHOD: " client_secret_post ",
      }).oauthAuthentication?.provider.clientAuthMethod,
    ).toBe("client_secret_post");
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_CLIENT_AUTH_METHOD: "private_key_jwt",
      }),
    ).toThrow("OAuth client authentication method is unsupported");
  });

  it("rejects malformed provider request headers", () => {
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_PROVIDER_HEADERS_JSON: '["not", "an", "object"]',
      }),
    ).toThrow("OAuth provider headers must be a JSON object");
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_PROVIDER_HEADERS_JSON: '{"x-provider-context":42}',
      }),
    ).toThrow("OAuth provider headers must contain string values");
  });

  it("validates optional OAuth revocation transport configuration", () => {
    const configured = parseDanoServerOptions([], {
      ...oauthEnvironment(),
      DANO_OAUTH_REVOCATION_TRANSPORT: "delete-query-basic",
    });
    expect(configured.oauthAuthentication?.provider.revocation).toEqual({
      transport: "delete-query-basic",
      endpoint: "https://provider.example.test/token",
    });
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_REVOCATION_TRANSPORT: "invented",
      }),
    ).toThrow("OAuth revocation transport is unsupported");
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_REVOCATION_TRANSPORT: "rfc7009",
      }),
    ).toThrow("OAuth RFC 7009 revocation endpoint is required");
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_REVOCATION_ENDPOINT:
          "https://provider.example.test/revoke",
      }),
    ).toThrow(
      "OAuth revocation transport is required when its endpoint is configured",
    );
  });

  it("rejects an invalid token state compatibility flag", () => {
    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_OAUTH_SEND_STATE_TO_TOKEN_ENDPOINT: "sometimes",
      }),
    ).toThrow(
      "DANO_OAUTH_SEND_STATE_TO_TOKEN_ENDPOINT must be true or false",
    );
  });

  it("requires the complete OAuth configuration in production", () => {
    expect(() =>
      parseDanoServerOptions([], { NODE_ENV: "production" }),
    ).toThrow("OAuth configuration is required in production");

    expect(
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment(),
      }).oauthAuthentication,
    ).toBeDefined();
  });

  it("supports a configuration-only production startup gate", () => {
    const options = parseDanoServerOptions(["--validate-config"], {
      NODE_ENV: "production",
      ...oauthEnvironment(),
    });

    expect(options.validateConfig).toBe(true);
    expect(options.help).toBe(false);
  });

  it("actively validates each unique provider TLS origin with sanitized failures", async () => {
    const configuration = parseDanoServerOptions(["--validate-config"], {
      NODE_ENV: "production",
      ...oauthEnvironment({
        DANO_OAUTH_AUTHORIZATION_ENDPOINT:
          "http://provider-browser.example.test/authorize",
        DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT: "true",
        DANO_OAUTH_REVOCATION_TRANSPORT: "rfc7009",
        DANO_OAUTH_REVOCATION_ENDPOINT:
          "https://provider-revoke.example.test/revoke",
        DANO_OAUTH_PROFILE_ENDPOINT: "https://provider-profile.example.test/profile",
      }),
    }).oauthAuthentication!;
    const probes: string[] = [];

    await validateOAuthProviderTls(configuration, async endpoint => {
      probes.push(endpoint.href);
    });

    expect(probes).toEqual([
      "https://provider.example.test/",
      "https://provider-profile.example.test/",
      "https://provider-api.example.test/",
      "https://provider-revoke.example.test/",
    ]);
    await expect(
      validateOAuthProviderTls(configuration, async endpoint => {
        throw new Error(`private TLS detail for ${endpoint.href}`);
      }),
    ).rejects.toThrow("OAuth provider TLS validation failed");
    await expect(
      validateOAuthProviderTls(configuration, async endpoint => {
        if (endpoint.hostname === "provider-profile.example.test") throw new Error("untrusted profile certificate");
      }),
    ).rejects.toThrow("OAuth provider TLS validation failed");
  });

  it("rejects residual Demo authentication in production", () => {
    expect(() =>
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment(),
        DANO_DEMO_JWT: "must-not-be-used",
      }),
    ).toThrow("Demo authentication is not allowed in production");

    expect(() =>
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment(),
        DANO_AUTH_JWT_SECRET: "must-not-be-used",
      }),
    ).toThrow("Demo authentication is not allowed in production");
  });

  it("rejects untrusted production OAuth transport and callback configuration", () => {
    for (const override of [
      {
        DANO_OAUTH_AUTHORIZATION_ENDPOINT:
          "http://provider.example.test/authorize",
      },
      { DANO_OAUTH_TOKEN_ENDPOINT: "http://provider.example.test/token" },
      { DANO_OAUTH_API_ORIGIN: "http://provider.example.test" },
      { DANO_OAUTH_REDIRECT_URI: "http://dano.example.test/api/auth/callback" },
      {
        DANO_OAUTH_REDIRECT_URI:
          "https://dano.example.test/api/auth/callback?next=/",
      },
      { NODE_TLS_REJECT_UNAUTHORIZED: "0" },
    ]) {
      expect(() =>
        parseDanoServerOptions([], {
          NODE_ENV: "production",
          ...oauthEnvironment(override),
        }),
      ).toThrow();
    }
  });

  it("configures Login Session lifetime and cleanup from deployment values", () => {
    expect(
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_LOGIN_SESSION_IDLE_TTL_MS: "60000",
        DANO_LOGIN_SESSION_ABSOLUTE_TTL_MS: "120000",
        DANO_LOGIN_SESSION_CLEANUP_INTERVAL_MS: "30000",
      }).oauthAuthentication?.session,
    ).toEqual({
      idleTtlMs: 60_000,
      absoluteTtlMs: 120_000,
      cleanupIntervalMs: 30_000,
    });

    expect(() =>
      parseDanoServerOptions([], {
        ...oauthEnvironment(),
        DANO_LOGIN_SESSION_IDLE_TTL_MS: "120000",
        DANO_LOGIN_SESSION_ABSOLUTE_TTL_MS: "60000",
      }),
    ).toThrow("absolute TTL must be greater than idle TTL");
  });

  it("requires Secure guest Cookies in production and allows a local override", () => {
    expect(
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment(),
      }).guestCookieSecure,
    ).toBe(true);
    expect(
      parseDanoServerOptions([], { NODE_ENV: "development" }).guestCookieSecure,
    ).toBe(false);
    expect(
      parseDanoServerOptions([], {
        NODE_ENV: "production",
        ...oauthEnvironment(),
        DANO_GUEST_COOKIE_SECURE: "false",
      }).guestCookieSecure,
    ).toBe(true);
    expect(
      parseDanoServerOptions([], {
        NODE_ENV: "development",
        DANO_GUEST_COOKIE_SECURE: "true",
      }).guestCookieSecure,
    ).toBe(true);
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
      "/tmp/dano-runtime/.dano/sessions",
    );
  });

  it("lets the command line select the default workspace", () => {
    const options = parseDanoServerOptions(
      ["--default-workspace", "/tmp/cli-dano"],
      { DANO_DEFAULT_WORKSPACE_PATH: "/tmp/env-dano" },
    );

    expect(options.defaultWorkspacePath).toBe("/tmp/cli-dano");
    expect(options.sessionsRootPath).toBe(
      "/opt/dano/runtime-data/.dano/sessions",
    );
  });

  it("uses Dano sessions root as the base for per-User session roots", () => {
    const options = parseDanoServerOptions([], {
      DANO_RUNTIME_DIR: "/tmp/dano-runtime",
      DANO_SESSIONS_ROOT: "/tmp/custom-dano-sessions",
    });

    expect(options.sessionsRootPath).toBe("/tmp/custom-dano-sessions");
  });

  it("keeps PI_WEB_SESSIONS_ROOT compatibility when Dano sessions root is absent", () => {
    const options = parseDanoServerOptions([], {
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
      DANO_EMPTY_STATE_TEXT: "给{产品名称}发消息",
    });

    expect(options.productName).toBe("My Agent");
    expect(options.emptyState).toEqual({
      mode: "text",
      content: "给{产品名称}发消息",
    });
  });

  it("uses the assistant name from dano.config.json by default", () => {
    const options = parseDanoServerOptions([], {});

    expect(options.productName).toBe("小络助手");
  });

  it("uses empty html from environment when provided", () => {
    const options = parseDanoServerOptions([], {
      DANO_EMPTY_STATE_TEXT: "ignored",
      DANO_EMPTY_STATE_HTML: "<strong>给{产品名称}发消息</strong>",
    });

    expect(options.emptyState).toEqual({
      mode: "html",
      content: "<strong>给{产品名称}发消息</strong>",
    });
  });

  it("lets command line empty state override environment", () => {
    const options = parseDanoServerOptions(
      [
        "--empty-state-html",
        "<em>HTML only</em>",
      ],
      {
        DANO_PRODUCT_NAME: "Env Agent",
        DANO_EMPTY_STATE_TEXT: "env text",
      },
    );

    expect(options.productName).toBe("Env Agent");
    expect(options.emptyState).toEqual({
      mode: "html",
      content: "<em>HTML only</em>",
    });
  });

  it("requires a product name from config or environment", () => {
    expect(() => parseDanoServerOptions([], {}, {})).toThrow(
      "Set productName in dano.config.json or provide DANO_PRODUCT_NAME",
    );
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

  it("initializes runtime settings in the global agent directory", async () => {
    const sourceRoot = mkdtempSync(join(tmpdir(), "dano-main-source-"));
    const workspaceRoot = mkdtempSync(join(tmpdir(), "dano-main-workspace-"));
    const agentRoot = mkdtempSync(join(tmpdir(), "dano-main-agent-"));

    try {
      const nestedSourceDir = join(sourceRoot, "apps", "dano");
      const runtimeDefaultsDir = join(sourceRoot, "deploy", "runtime-defaults");
      mkdirSync(nestedSourceDir, { recursive: true });
      mkdirSync(runtimeDefaultsDir, { recursive: true });
      writeFileSync(
        join(runtimeDefaultsDir, "SYSTEM.md"),
        "你是{产品名称}，system prompt",
      );
      writeFileSync(join(runtimeDefaultsDir, "settings.json"), "{}");
      writeFileSync(join(runtimeDefaultsDir, "heimdall.json"), "{}");

      await initializeDanoAgentSettings(agentRoot, nestedSourceDir, "My Agent");

      expect(readFileSync(join(agentRoot, "SYSTEM.md"), "utf8")).toBe(
        "你是My Agent，system prompt",
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

  it("renders the configured name in every shipped system prompt placeholder", async () => {
    const agentRoot = mkdtempSync(join(tmpdir(), "dano-main-agent-"));

    try {
      await initializeDanoAgentSettings(
        agentRoot,
        resolve("apps/dano/src"),
        "测试助手",
      );

      const systemPrompt = readFileSync(join(agentRoot, "SYSTEM.md"), "utf8");
      expect(systemPrompt).not.toContain("{产品名称}");
      expect(systemPrompt).toContain("你是测试助手，公司内部 OA 智能助手。");
      expect(systemPrompt).toContain(
        "你唯一可以向用户声明的身份是“测试助手，公司内部 OA 智能助手”",
      );
      expect(systemPrompt).toContain("是测试助手当前可以查询");
      expect(systemPrompt).toContain("当前测试助手暂未配置");
      expect(systemPrompt).toContain("推测测试助手使用的模型名称");
      expect(systemPrompt).toContain("只说明你是测试助手，公司内部");
    } finally {
      rmSync(agentRoot, { recursive: true, force: true });
    }
  });

  it("preserves a host-managed system prompt and other agent settings", async () => {
    const sourceRoot = mkdtempSync(join(tmpdir(), "dano-main-source-"));
    const agentRoot = mkdtempSync(join(tmpdir(), "dano-main-agent-"));

    try {
      mkdirSync(join(sourceRoot, "deploy", "runtime-defaults"), {
        recursive: true,
      });
      writeFileSync(
        join(sourceRoot, "deploy/runtime-defaults/SYSTEM.md"),
        "你是{产品名称}，source prompt",
      );
      writeFileSync(
        join(sourceRoot, "deploy/runtime-defaults/settings.json"),
        "{}",
      );
      writeFileSync(
        join(sourceRoot, "deploy/runtime-defaults/heimdall.json"),
        "{}",
      );
      writeFileSync(join(agentRoot, "SYSTEM.md"), "宿主机自定义 prompt");
      writeFileSync(join(agentRoot, "settings.json"), '{"custom":true}');

      await initializeDanoAgentSettings(agentRoot, sourceRoot, "测试助手");

      expect(readFileSync(join(agentRoot, "SYSTEM.md"), "utf8")).toBe(
        "宿主机自定义 prompt",
      );
      expect(readFileSync(join(agentRoot, "settings.json"), "utf8")).toBe(
        '{"custom":true}',
      );
      expect(readFileSync(join(agentRoot, "heimdall.json"), "utf8")).toBe(
        '{\n  "sandbox": {\n    "userNamespace": false,\n    "env": {\n      "allow": [\n        "PATH",\n        "HOME",\n        "SHELL",\n        "USER",\n        "LOGNAME",\n        "LANG",\n        "LC_*",\n        "TMPDIR",\n        "DANO_URL",\n        "DANO_TENANT_KEY"\n      ],\n      "deny": []\n    }\n  }\n}\n',
      );

      await initializeDanoAgentSettings(agentRoot, sourceRoot, "第二助手");
      expect(readFileSync(join(agentRoot, "SYSTEM.md"), "utf8")).toBe(
        "宿主机自定义 prompt",
      );
    } finally {
      rmSync(sourceRoot, { recursive: true, force: true });
      rmSync(agentRoot, { recursive: true, force: true });
    }
  });

  it("preserves Heimdall runtime settings while disabling user namespaces by default", async () => {
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

      await initializeDanoAgentSettings(agentRoot, sourceRoot, "测试助手");

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
