import { createHmac } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const initScript = new URL(
  "../../../../../scripts/init-demo-auth.mjs",
  import.meta.url,
).pathname;
const composeFile = new URL(
  "../../../../../docker-compose.yml",
  import.meta.url,
).pathname;
const nginxDemoAuthTemplate = new URL(
  "../../../../../deploy/nginx/demo-auth.conf.template",
  import.meta.url,
).pathname;
const nginxProxyConfig = new URL(
  "../../../../../deploy/nginx/shared/proxy-server.conf",
  import.meta.url,
).pathname;
const tempDirs: string[] = [];

function createEnv(content = "DANO_PRODUCT_NAME=Dano\n") {
  const dir = mkdtempSync(join(tmpdir(), "dano-demo-auth-test-"));
  tempDirs.push(dir);
  const envFile = join(dir, ".env");
  writeFileSync(envFile, content, { mode: 0o600 });
  return envFile;
}

function initialize(envFile: string) {
  execFileSync(process.execPath, [initScript, "--file", envFile]);
  return readEnv(envFile);
}

function readEnv(envFile: string) {
  return Object.fromEntries(
    readFileSync(envFile, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .map(line => {
        const separator = line.indexOf("=");
        const value = line.slice(separator + 1);
        return [
          line.slice(0, separator),
          value.startsWith("'") && value.endsWith("'")
            ? value.slice(1, -1)
            : value,
        ];
      }),
  );
}

function signJwt(
  claims: Record<string, unknown>,
  secret: string,
  header: Record<string, unknown> = { alg: "HS256", typ: "JWT" },
) {
  const encodedHeader = Buffer.from(JSON.stringify(header)).toString("base64url");
  const encodedClaims = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const input = `${encodedHeader}.${encodedClaims}`;
  const signature = createHmac("sha256", secret).update(input).digest("base64url");
  return `${input}.${signature}`;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true });
});

describe("Demo authentication deployment initialization", () => {
  it("creates one fixed Demo secret and JWT on first initialization", () => {
    const envFile = createEnv();
    const env = initialize(envFile);
    const claims = JSON.parse(
      Buffer.from(env.DANO_DEMO_JWT.split(".")[1], "base64url").toString("utf8"),
    );

    expect(env.DANO_AUTH_JWT_SECRET).toMatch(/^[A-Za-z0-9_-]{40,}$/);
    expect(claims).toMatchObject({ sub: "demo-user", name: "演示用户" });
    expect(claims.exp).toBeGreaterThan(Math.floor(Date.now() / 1000));
    expect(Date.parse(env.DANO_DEMO_COOKIE_EXPIRES) / 1000).toBe(claims.exp);
  });

  it("verifies and reuses the exact existing secret and JWT", () => {
    const envFile = createEnv();
    const first = initialize(envFile);
    const firstFile = readFileSync(envFile, "utf8");
    const second = initialize(envFile);

    expect(second.DANO_AUTH_JWT_SECRET).toBe(first.DANO_AUTH_JWT_SECRET);
    expect(second.DANO_DEMO_JWT).toBe(first.DANO_DEMO_JWT);
    expect(readFileSync(envFile, "utf8")).toBe(firstFile);
  });

  it.each(["DANO_AUTH_JWT_SECRET=only-secret\n", "DANO_DEMO_JWT=only-token\n"])(
    "fails when only one credential exists",
    content => {
      const result = spawnSync(
        process.execPath,
        [initScript, "--file", createEnv(content)],
        { encoding: "utf8" },
      );
      expect(result.status).toBe(1);
      expect(result.stderr).toContain("must both be set or both be absent");
    },
  );

  it.each([
    {
      label: "signature",
      token: (secret: string) =>
        `${signJwt({ sub: "demo-user", name: "演示用户", exp: 4_102_444_800 }, secret)}x`,
      error: "signature is invalid",
    },
    {
      label: "subject claim",
      token: (secret: string) =>
        signJwt(
          { sub: "other-user", name: "演示用户", exp: 4_102_444_800 },
          secret,
        ),
      error: "identity claims are invalid",
    },
    {
      label: "name claim",
      token: (secret: string) =>
        signJwt({ sub: "demo-user", name: "Other", exp: 4_102_444_800 }, secret),
      error: "identity claims are invalid",
    },
    {
      label: "expiration",
      token: (secret: string) =>
        signJwt({ sub: "demo-user", name: "演示用户", exp: 1 }, secret),
      error: "has expired",
    },
    {
      label: "algorithm",
      token: (secret: string) =>
        signJwt(
          { sub: "demo-user", name: "演示用户", exp: 4_102_444_800 },
          secret,
          { alg: "HS512", typ: "JWT" },
        ),
      error: "must use HS256",
    },
  ])("rejects an invalid $label", ({ token, error }) => {
    const secret = "existing-secret";
    const envFile = createEnv(
      `DANO_AUTH_JWT_SECRET=${secret}\nDANO_DEMO_JWT=${token(secret)}\n`,
    );
    const result = spawnSync(process.execPath, [initScript, "--file", envFile], {
      encoding: "utf8",
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain(error);
    expect(readFileSync(envFile, "utf8")).toContain(
      `DANO_DEMO_JWT=${token(secret)}`,
    );
  });
});

describe("Demo authentication nginx contract", () => {
  it("passes only the fixed JWT and cookie metadata to nginx", () => {
    const compose = readFileSync(composeFile, "utf8");

    expect(compose).toContain("DANO_DEMO_JWT: ${DANO_DEMO_JWT:-}");
    expect(compose).toContain(
      "DANO_DEMO_COOKIE_EXPIRES: ${DANO_DEMO_COOKIE_EXPIRES:-}",
    );
    expect(compose).toContain(
      "DANO_AUTH_COOKIE_NAME: ${DANO_AUTH_COOKIE_NAME:-dano_auth}",
    );
    expect(compose).not.toMatch(/nginx:[\s\S]*DANO_AUTH_JWT_SECRET:/);
  });

  it("sets the persistent cookie only on HTML and derives Secure from the request scheme", () => {
    const template = readFileSync(nginxDemoAuthTemplate, "utf8");
    const proxy = readFileSync(nginxProxyConfig, "utf8");

    expect(template).toContain("${DANO_DEMO_JWT}");
    expect(template).toContain("Expires=${DANO_DEMO_COOKIE_EXPIRES}");
    expect(template).toContain("Path=/; HttpOnly; SameSite=Lax");
    expect(template).toContain("map $scheme $dano_demo_cookie_secure");
    expect(template).toContain('https "; Secure"');
    expect(template).toContain("$upstream_http_content_type");
    expect(proxy).toContain("add_header Set-Cookie $dano_demo_set_cookie always;");
  });
});
