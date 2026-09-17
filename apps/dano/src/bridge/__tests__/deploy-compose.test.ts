import { execFileSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const deployScript = new URL(
  "../../../../../scripts/deploy-compose.mjs",
  import.meta.url,
).pathname;
const localContainerScript = new URL(
  "../../../../../scripts/local-container.mjs",
  import.meta.url,
).pathname;
const releaseScript = new URL(
  "../../../../../scripts/deploy-release.mjs",
  import.meta.url,
).pathname;
const bashAcceptanceScript = new URL(
  "../../../../../scripts/check-bash-acceptance.mjs",
  import.meta.url,
).pathname;
const bashAcceptanceContainerScript = new URL(
  "../../../../../scripts/check-bash-acceptance-container.sh",
  import.meta.url,
).pathname;
const composeFile = new URL(
  "../../../../../docker-compose.yml",
  import.meta.url,
).pathname;
const envExampleFile = new URL(
  "../../../../../.env.example",
  import.meta.url,
).pathname;
const danoConfigFile = new URL(
  "../../../../../dano.config.json",
  import.meta.url,
).pathname;
const webIndexFile = new URL(
  "../../../web/index.html",
  import.meta.url,
).pathname;
const assistantIconFile = new URL(
  "../../../web/public/dano-assistant.svg",
  import.meta.url,
).pathname;
const deployRoot = new URL("../../../../../deploy/", import.meta.url).pathname;
const dockerfile = new URL("../../../../../Dockerfile", import.meta.url).pathname;
const dockerignoreFile = new URL(
  "../../../../../.dockerignore",
  import.meta.url,
).pathname;
const entrypointFile = new URL(
  "../../../../../deploy/docker-entrypoint.sh",
  import.meta.url,
).pathname;
const renderSystemPromptScript = new URL(
  "../../../../../deploy/render-system-prompt.mjs",
  import.meta.url,
).pathname;
const tempDirs: string[] = [];
let lastComposeEnv: Record<string, string | undefined> = {};

function nodeOnlyPath(root: string): string {
  const binDir = join(root, "node-bin");
  mkdirSync(binDir);
  symlinkSync(process.execPath, join(binDir, "node"));
  return `${binDir}:/usr/bin:/bin`;
}

function run(
  command: string,
  options: {
    env?: NodeJS.ProcessEnv;
    envFile?: boolean;
    envFileContent?: string;
    image?: string;
  } = {},
) {
  const cwd = mkdtempSync(join(tmpdir(), "dano-deploy-compose-"));
  tempDirs.push(cwd);
  const logPath = join(cwd, "compose.log");
  const composeEnvPath = join(cwd, "compose-env.json");
  const composePath = join(cwd, "compose");
  writeFileSync(
    composePath,
    `#!/usr/bin/env node
import { appendFileSync } from "node:fs";
appendFileSync(process.env.DANO_COMPOSE_LOG, JSON.stringify(process.argv.slice(2)) + "\\n");
import { writeFileSync } from "node:fs";
writeFileSync(process.env.DANO_COMPOSE_ENV_LOG, JSON.stringify({
  DANO_NGINX_CONF: process.env.DANO_NGINX_CONF,
  DANO_NGINX_SHARED_DIR: process.env.DANO_NGINX_SHARED_DIR,
}));
`,
  );
  chmodSync(composePath, 0o755);
  if (options.envFile) {
    writeFileSync(
      join(cwd, ".env"),
      options.envFileContent || "DANO_TEST=1\n",
    );
  }

  const env: NodeJS.ProcessEnv = {
    ...process.env,
    DANO_COMPOSE: composePath,
    DANO_COMPOSE_LOG: logPath,
    DANO_COMPOSE_ENV_LOG: composeEnvPath,
  };
  delete env.DANO_IMAGE;
  delete env.DANO_EXPOSURE_MODE;
  delete env.DANO_TLS_CERT_PATH;
  delete env.DANO_TLS_KEY_PATH;
  if (options.image) env.DANO_IMAGE = options.image;
  Object.assign(env, options.env);
  execFileSync(process.execPath, [deployScript, command], { cwd, env });
  lastComposeEnv = JSON.parse(readFileSync(composeEnvPath, "utf8"));

  return readFileSync(logPath, "utf8")
    .trim()
    .split("\n")
    .map(line => JSON.parse(line));
}

function runRelease(
  options: {
    env?: NodeJS.ProcessEnv;
    exposureMode?: "http" | "https" | "both" | "both-no-redirect-http";
    provideTlsInputs?: boolean;
    tlsInputsAreDirectories?: boolean;
    tlsPathStyle?: "absolute" | "relative";
    deployDir?: string;
  } = {},
) {
  const cwd = mkdtempSync(join(tmpdir(), "dano-release-test-"));
  tempDirs.push(cwd);
  const fakeRepo = join(cwd, "repo");
  const fakeBin = join(cwd, "bin");
  const buildParent = join(cwd, "tmp");
  const deployDir = options.deployDir ?? join(cwd, "deploy");
  const runtimeDir = join(cwd, "runtime");
  const secretsDir = join(deployDir, ".secrets");
  const nginxConf = join(deployDir, "nginx/default.conf");
  const logPath = join(cwd, "commands.log");
  const tlsDir =
    options.tlsPathStyle === "relative" ? join(deployDir, "operator-tls") : cwd;
  const tlsCertPath = join(tlsDir, "operator \\$certificate#1.crt");
  const tlsKeyPath = join(tlsDir, "operator \\$private-key#1.key");
  const tlsCertInput =
    options.tlsPathStyle === "relative"
      ? "operator-tls/operator \\$certificate#1.crt"
      : tlsCertPath;
  const tlsKeyInput =
    options.tlsPathStyle === "relative"
      ? "operator-tls/operator \\$private-key#1.key"
      : tlsKeyPath;

  mkdirSync(join(fakeRepo, "deploy/compose"), { recursive: true });
  mkdirSync(join(fakeRepo, "deploy/nginx/shared"), { recursive: true });
  mkdirSync(join(fakeRepo, "scripts"), { recursive: true });
  mkdirSync(fakeBin);
  mkdirSync(buildParent);
  writeFileSync(join(fakeRepo, "docker-compose.yml"), "services:\n  app:\n");
  writeFileSync(
    join(fakeRepo, "deploy/compose/http.yml"),
    "services:\n  nginx:\n    ports:\n      - 80:80\n",
  );
  writeFileSync(
    join(fakeRepo, "deploy/compose/https.yml"),
    "services:\n  nginx:\n    ports:\n      - 443:443\n",
  );
  writeFileSync(
    join(fakeRepo, "deploy/compose/both.yml"),
    "services:\n  nginx:\n    ports:\n      - 80:80\n      - 443:443\n",
  );
  writeFileSync(
    join(fakeRepo, "deploy/compose/both-no-redirect-http.yml"),
    "services:\n  nginx:\n    ports:\n      - 80:80\n      - 443:443\n",
  );
  writeFileSync(
    join(fakeRepo, "deploy/nginx/http.conf.template"),
    "server { listen 80; }\n",
  );
  writeFileSync(
    join(fakeRepo, "deploy/nginx/https.conf.template"),
    "server { listen 443 ssl; }\n",
  );
  writeFileSync(
    join(fakeRepo, "deploy/nginx/both.conf.template"),
    "server { listen 80; return 308 https://host; }\nserver { listen 443 ssl; }\n",
  );
  writeFileSync(
    join(fakeRepo, "deploy/nginx/both-no-redirect-http.conf.template"),
    "server { listen 80; }\nserver { listen 443 ssl; }\n",
  );
  writeFileSync(
    join(fakeRepo, "deploy/nginx/shared/proxy-server.conf"),
    "location / {}\n",
  );
  writeFileSync(
    join(fakeRepo, "scripts/smoke-dano-deploy.mjs"),
    `import { appendFileSync } from "node:fs";
appendFileSync(process.env.DANO_COMMAND_LOG, "smoke\\n");
`,
  );
  writeFileSync(
    join(fakeBin, "git"),
    `#!/usr/bin/env node
import { cpSync } from "node:fs";
const args = process.argv.slice(2);
if (process.env.DANO_FAKE_GIT_REJECT_FILTER && args.includes("--filter=blob:none")) process.exit(129);
if (args[0] === "clone") cpSync(process.env.DANO_FAKE_REPO, args.at(-1), { recursive: true });
`,
  );
  writeFileSync(
    join(fakeBin, "compose"),
    `#!/usr/bin/env node
import { appendFileSync, readFileSync } from "node:fs";
const args = process.argv.slice(2);
appendFileSync(process.env.DANO_COMMAND_LOG, JSON.stringify(args) + "\\n");
if (process.env.DANO_FAKE_SWITCH_MARKER && args.includes("up")) {
  appendFileSync(process.env.DANO_FAKE_SWITCH_MARKER, "up\\n");
}
if (process.env.DANO_FAKE_REQUIRE_OAUTH_CREDENTIAL_PAIR && args.includes("--validate-config")) {
  const env = readFileSync(".env", "utf8");
  const key = env.match(/^DANO_OAUTH_CREDENTIAL_KEY=([A-Za-z0-9_-]+)$/m)?.[1];
  const version = env.match(/^DANO_OAUTH_CREDENTIAL_KEY_VERSION=([^\\r\\n]+)$/m)?.[1];
  if (!key || Buffer.from(key, "base64url").byteLength !== 32 || !version) process.exit(2);
}
if (process.env.DANO_FAKE_SYSTEM_FAIL && args.includes("./deploy/system-prompt.mjs") && args.includes(process.env.DANO_FAKE_SYSTEM_FAIL)) process.exit(1);
if (process.env.DANO_FAKE_CONFIG_INVALID && args.includes("--validate-config")) {
  process.exit(1);
}
`,
  );
  writeFileSync(
    join(fakeBin, "chown"),
    `#!/usr/bin/env node
import { appendFileSync } from "node:fs";
appendFileSync(process.env.DANO_COMMAND_LOG, JSON.stringify(["chown", ...process.argv.slice(2)]) + "\\n");
`,
  );
  chmodSync(join(fakeBin, "git"), 0o755);
  chmodSync(join(fakeBin, "compose"), 0o755);
  chmodSync(join(fakeBin, "chown"), 0o755);

  if (
    options.exposureMode &&
    options.exposureMode !== "http" &&
    options.provideTlsInputs !== false
  ) {
    mkdirSync(tlsDir, { recursive: true });
    if (options.tlsInputsAreDirectories) {
      mkdirSync(tlsCertPath);
      mkdirSync(tlsKeyPath);
    } else {
      writeFileSync(tlsCertPath, "test certificate\n");
      writeFileSync(tlsKeyPath, "test private key\n");
    }
  }

  const output = execFileSync(process.execPath, [releaseScript], {
    cwd,
    encoding: "utf8",
    env: {
      ...process.env,
      ...options.env,
      PATH: `${fakeBin}:${process.env.PATH}`,
      DANO_DEPLOY_LOCK_PATH: join(cwd, "deploy.lock"),
      DANO_COMMAND_LOG: logPath,
      DANO_COMPOSE: "compose",
      DANO_FAKE_REPO: fakeRepo,
      DANO_REPO_URL: "fake",
      DANO_GIT_REF: "abc123",
      DANO_BUILD_PARENT_DIR: buildParent,
      DANO_DEPLOY_DIR: deployDir,
      DANO_RUNTIME_DIR: runtimeDir,
      DANO_SECRETS_DIR: secretsDir,
      DANO_NGINX_CONF: nginxConf,
      DANO_EXPOSURE_MODE: options.exposureMode,
      DANO_TLS_CERT_PATH:
        options.exposureMode &&
        options.exposureMode !== "http" &&
        options.provideTlsInputs !== false
          ? tlsCertInput
          : undefined,
      DANO_TLS_KEY_PATH:
        options.exposureMode &&
        options.exposureMode !== "http" &&
        options.provideTlsInputs !== false
          ? tlsKeyInput
          : undefined,
      DANO_SMOKE_BASE_URL: "http://127.0.0.1:18082",
    },
  });

  return {
    buildParent,
    deployDir,
    runtimeDir,
    nginxConf,
    tlsCertPath,
    tlsKeyPath,
    output,
    logLines: readFileSync(logPath, "utf8").trim().split("\n"),
  };
}

afterEach(() => {
  for (const path of tempDirs.splice(0)) rmSync(path, { recursive: true });
});

describe("deploy compose wrapper", () => {
  it("passes configurable Anonymous User cleanup timing into the app", () => {
    const compose = readFileSync(composeFile, "utf8");
    const example = readFileSync(envExampleFile, "utf8");

    expect(compose).toContain(
      "DANO_ANONYMOUS_IDLE_TTL_MS: ${DANO_ANONYMOUS_IDLE_TTL_MS:-86400000}",
    );
    expect(compose).toContain(
      "DANO_ANONYMOUS_CLEANUP_INTERVAL_MS: ${DANO_ANONYMOUS_CLEANUP_INTERVAL_MS:-3600000}",
    );
    expect(example).toContain("DANO_ANONYMOUS_IDLE_TTL_MS=86400000");
    expect(example).toContain("DANO_ANONYMOUS_CLEANUP_INTERVAL_MS=3600000");
  });

  it("uses stable local Podman paths and port", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-local-container-test-"));
    tempDirs.push(cwd);
    const fakeBin = join(cwd, "bin");
    const logPath = join(cwd, "podman.json");
    mkdirSync(fakeBin);
    writeFileSync(
      join(fakeBin, "podman"),
      `#!/usr/bin/env node
import { writeFileSync } from "node:fs";
writeFileSync(process.env.DANO_LOCAL_CONTAINER_LOG, JSON.stringify({
  args: process.argv.slice(2),
  runtimeDir: process.env.DANO_RUNTIME_DIR,
  secretsDir: process.env.DANO_SECRETS_DIR,
  port: process.env.DANO_NGINX_PORT,
}));
`,
    );
    chmodSync(join(fakeBin, "podman"), 0o755);

    execFileSync(process.execPath, [localContainerScript, "up"], {
      env: {
        ...process.env,
        PATH: `${fakeBin}:${process.env.PATH}`,
        TMPDIR: cwd,
        DANO_IMAGE: "",
        DANO_LOCAL_CONTAINER_LOG: logPath,
      },
    });

    expect(JSON.parse(readFileSync(logPath, "utf8"))).toMatchObject({
      args: [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/http.yml",
        "up",
        "-d",
        "--no-build",
      ],
      runtimeDir: join(cwd, "dano-local-container/runtime"),
      secretsDir: join(cwd, "dano-local-container/secrets"),
      port: "18082",
    });
  });

  it("starts without building when no image is provided", () => {
    expect(run("up")).toEqual([
      [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/http.yml",
        "up",
        "-d",
        "--no-build",
      ],
    ]);
  });

  it("pulls and starts a prebuilt image without building", () => {
    expect(run("up", { image: "example/dano:latest" })).toEqual([
      [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/http.yml",
        "pull",
        "app",
      ],
      [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/http.yml",
        "up",
        "-d",
        "--no-build",
      ],
    ]);
  });

  it("stops without removing containers", () => {
    expect(run("stop")).toEqual([
      [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/http.yml",
        "stop",
      ],
    ]);
  });

  it("keeps down as the explicit removal command", () => {
    expect(run("down")).toEqual([
      [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/http.yml",
        "down",
      ],
    ]);
  });

  it("passes the local env file to compose", () => {
    expect(run("ps", { envFile: true })).toEqual([
      [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/http.yml",
        "--env-file",
        ".env",
        "ps",
      ],
    ]);
  });

  it("selects the exposure mode stored in the local env file", () => {
    expect(
      run("ps", {
        envFile: true,
        envFileContent: "DANO_EXPOSURE_MODE=both-no-redirect-http\n",
      }),
    ).toEqual([
      [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/both-no-redirect-http.yml",
        "--env-file",
        ".env",
        "ps",
      ],
    ]);
  });

  it("keeps the documented local .env flow on local images and source nginx assets", () => {
    expect(
      run("up", {
        envFile: true,
        envFileContent: readFileSync(envExampleFile, "utf8"),
      }),
    ).toEqual([
      [
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "deploy/compose/http.yml",
        "--env-file",
        ".env",
        "up",
        "-d",
        "--no-build",
      ],
    ]);
    expect(lastComposeEnv.DANO_NGINX_CONF).toBe(
      join(deployRoot, "nginx/http.conf.template"),
    );
    expect(lastComposeEnv.DANO_NGINX_SHARED_DIR).toBe(
      join(deployRoot, "nginx/shared"),
    );
    expect(lastComposeEnv).not.toHaveProperty("DANO_NGINX_DEMO_AUTH_CONF");
  });

  it("rejects an unknown exposure mode", () => {
    expect(() =>
      run("ps", { env: { DANO_EXPOSURE_MODE: "ftp" } }),
    ).toThrow();
  });

  it("rejects TLS startup when certificate inputs are missing", () => {
    expect(() =>
      run("up", { env: { DANO_EXPOSURE_MODE: "https" } }),
    ).toThrow();
  });

  it("keeps production compose independent from the source checkout", () => {
    const compose = readFileSync(composeFile, "utf8");
    expect(compose).toContain("name: dano");
    expect(compose).not.toContain("build:");
    expect(compose).not.toContain("./runtime-data");
    expect(compose).toContain("cap_add:");
    expect(compose).toContain("- ALL");
    expect(compose).toContain("security_opt:");
    expect(compose).toContain("- seccomp=unconfined");
    expect(compose).not.toContain("privileged: true");
    expect(compose).toContain(
      "DANO_RUNTIME_DIR: /opt/dano/runtime-data",
    );
    expect(compose).toContain(
      "DANO_PRODUCT_NAME: ${DANO_PRODUCT_NAME:-}",
    );
    expect(compose).toContain(
      "DANO_SESSIONS_ROOT: /opt/dano/runtime-data/.dano/sessions",
    );
    expect(compose).toContain(
      "DANO_UPLOAD_DIR: /opt/dano/runtime-data/.dano/uploads",
    );
    expect(compose).toContain("OPEN_WEBSEARCH_HOST: 127.0.0.1");
    expect(compose).toContain("OPEN_WEBSEARCH_PORT: 3210");
    expect(compose).toContain(
      "DEFAULT_SEARCH_ENGINE: ${OPEN_WEBSEARCH_DEFAULT_SEARCH_ENGINE:-duckduckgo}",
    );
    expect(compose).toContain(
      "USE_PROXY: ${OPEN_WEBSEARCH_USE_PROXY:-false}",
    );
    expect(compose).toContain(
      "${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}:/opt/dano/runtime-data",
    );
    expect(compose).toContain("agent-config:/opt/dano/runtime-data/.pi");
    expect(compose).toContain(
      "workspaces:/opt/dano/runtime-data/workspaces",
    );
    expect(compose).toContain("agent-config:");
    expect(compose).toContain("workspaces:");
    expect(compose).not.toContain(":/tmp/dano");
    expect(compose).toContain(
      "${DANO_NGINX_CONF:-/opt/dano/deploy/nginx/default.conf.template}",
    );
    expect(compose).not.toContain("DANO_NGINX_DEMO_AUTH_CONF");
    expect(compose).not.toContain("DANO_HTTPS_PORT");
    expect(compose).not.toContain("DANO_TLS_CERT_PATH");
    expect(compose).not.toContain("DANO_TLS_KEY_PATH");
  });

  it("keeps the product name default only in Dano config", () => {
    const envExample = readFileSync(envExampleFile, "utf8");
    const danoConfig = JSON.parse(
      readFileSync(danoConfigFile, "utf8"),
    ) as { productName?: string };
    const webIndex = readFileSync(webIndexFile, "utf8");
    const assistantIcon = readFileSync(assistantIconFile, "utf8");

    expect(danoConfig.productName).toBe("小络助手");
    expect(envExample).toContain("# DANO_PRODUCT_NAME=");
    expect(envExample).not.toMatch(/^DANO_PRODUCT_NAME=.+$/m);
    expect(webIndex).toContain("<title></title>");
    expect(webIndex).not.toContain("小络助手");
    expect(assistantIcon).toContain('aria-label="助手图标"');
    expect(assistantIcon).not.toContain("小络助手");
  });

  it("ships four exposure modes with shared proxy behavior", () => {
    const httpCompose = readFileSync(
      join(deployRoot, "compose/http.yml"),
      "utf8",
    );
    const httpsCompose = readFileSync(
      join(deployRoot, "compose/https.yml"),
      "utf8",
    );
    const bothCompose = readFileSync(
      join(deployRoot, "compose/both.yml"),
      "utf8",
    );
    const bothNoRedirectCompose = readFileSync(
      join(deployRoot, "compose/both-no-redirect-http.yml"),
      "utf8",
    );
    const bothNginx = readFileSync(
      join(deployRoot, "nginx/both.conf.template"),
      "utf8",
    );
    const bothNoRedirectNginx = readFileSync(
      join(deployRoot, "nginx/both-no-redirect-http.conf.template"),
      "utf8",
    );

    expect(httpCompose).toContain("DANO_NGINX_PORT");
    expect(httpCompose).not.toContain("DANO_HTTPS_PORT");
    expect(httpsCompose).toContain("DANO_HTTPS_PORT");
    expect(httpsCompose).not.toContain("DANO_NGINX_PORT");
    expect(httpsCompose).toContain("type: bind");
    expect(httpsCompose).toContain("source: ${DANO_TLS_CERT_PATH}");
    expect(httpsCompose).not.toContain("${DANO_TLS_CERT_PATH}:");
    expect(bothCompose).toContain("DANO_NGINX_PORT");
    expect(bothCompose).toContain("DANO_HTTPS_PORT");
    expect(bothNoRedirectCompose).toContain("DANO_NGINX_PORT");
    expect(bothNoRedirectCompose).toContain("DANO_HTTPS_PORT");
    expect(bothNginx).toContain(
      "return 308 https://$host:${DANO_HTTPS_PORT}$request_uri",
    );
    expect(bothNoRedirectNginx).not.toContain("return 308");
    expect(bothNoRedirectNginx).toContain("proxy-server.conf");
    expect(bothNoRedirectNginx).toContain("tls-server.conf");
  });

  it("rewrites Debian apt sources to Aliyun HTTP mirrors", () => {
    const dockerfileText = readFileSync(dockerfile, "utf8");

    expect(dockerfileText).toContain("https\\?://deb.debian.org/debian");
    expect(dockerfileText).toContain(
      "http://mirrors.aliyun.com/debian-security",
    );
    expect(dockerfileText).toContain("http://mirrors.aliyun.com/debian");
    expect(dockerfileText).not.toContain(
      "https://mirrors.aliyun.com/debian-security",
    );
    expect(dockerfileText).not.toContain("https://mirrors.aliyun.com/debian");
  });

  it("preinstalls Pi search tools on the system PATH", () => {
    const dockerfileText = readFileSync(dockerfile, "utf8");

    expect(dockerfileText).toMatch(/fd-find[^\n]*ripgrep/);
    expect(dockerfileText).toContain("/usr/local/bin/fd");
    expect(dockerfileText).toContain(
      "npm install --global open-websearch@2.1.11",
    );
    expect(dockerfileText).toContain(
      "npx --yes skills@1.5.9 add",
    );
    expect(dockerfileText).toContain(
      "https://github.com/Aas-ee/open-webSearch/tree/v2.1.11",
    );
    expect(dockerfileText).toContain("--skill open-websearch");
    expect(dockerfileText).toContain("--agent universal");
    expect(dockerfileText).toContain("--copy");
    expect(dockerfileText).not.toContain("pi install");
  });

  it("keeps local package stores out of the Docker build context", () => {
    const dockerignoreText = readFileSync(dockerignoreFile, "utf8");

    expect(dockerignoreText).toContain("node_modules");
    expect(dockerignoreText).toContain(".pnpm-store");
  });

  it("runs the production container as the non-root node user", () => {
    const dockerfileText = readFileSync(dockerfile, "utf8");

    expect(dockerfileText).toContain("ENV HOME=/home/node");
    expect(dockerfileText).toContain("ENV DANO_RUNTIME_DIR=/opt/dano/runtime-data");
    expect(dockerfileText).not.toContain("ENV DANO_PRODUCT_NAME=");
    expect(dockerfileText).toContain("ENV HEIMDALL_BWRAP_BIND_KERNEL_FS=1");
    expect(dockerfileText).toContain("ENV HEIMDALL_BWRAP_BIND_PROC=0");
    expect(dockerfileText).toContain(
      "ENV HEIMDALL_BWRAP_BIND_ROOT=/opt/dano/runtime-data/workspaces",
    );
    expect(dockerfileText).not.toContain("COPY patches");
    expect(dockerfileText).not.toContain("patched_heimdall_dir=");
    expect(dockerfileText).not.toContain("prod_heimdall_dir=");
    expect(dockerfileText).toContain("mkdir -p /opt/dano/runtime-data");
    expect(dockerfileText).toContain("chown -R node:node /opt/dano /home/node");
    expect(dockerfileText).toContain("chmod 4755 /usr/bin/bwrap");
    expect(dockerfileText).not.toContain("/tmp/dano");
    expect(dockerfileText).toContain("USER node");
    expect(dockerfileText.indexOf("USER node")).toBeGreaterThan(
      dockerfileText.indexOf("apt-get install"),
    );
  });

  it("runs bash acceptance in a read-only Node container", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-bash-container-"));
    tempDirs.push(cwd);
    const fakeBin = join(cwd, "bin");
    const runtimeDir = join(cwd, "runtime-data");
    const sessionPath = join(runtimeDir, "workspaces/ws/.dano/sessions/session.jsonl");
    const logPath = join(cwd, "podman.json");
    mkdirSync(fakeBin);
    mkdirSync(join(runtimeDir, "workspaces/ws/.dano/sessions"), {
      recursive: true,
    });
    writeFileSync(
      join(fakeBin, "podman"),
      `#!/usr/bin/env node
const { writeFileSync } = require("node:fs");
writeFileSync(process.env.DANO_COMMAND_LOG, JSON.stringify(process.argv.slice(2)));
`,
    );
    chmodSync(join(fakeBin, "podman"), 0o755);

    execFileSync(bashAcceptanceContainerScript, [sessionPath], {
      env: {
        ...process.env,
        PATH: `${fakeBin}:${process.env.PATH}`,
        DANO_DEPLOY_LOCK_PATH: join(cwd, "deploy.lock"),
      DANO_COMMAND_LOG: logPath,
        DANO_RUNTIME_DIR: runtimeDir,
        DANO_BASH_ACCEPTANCE_MARKER: "DANO_BASH_OK",
        DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS: "TOKEN missing",
      },
    });

    const args = JSON.parse(readFileSync(logPath, "utf8"));
    expect(args).toContain("node:22-bookworm-slim");
    expect(args).toContain("/app/scripts/check-bash-acceptance.mjs");
    expect(args).toContain("/runtime/workspaces/ws/.dano/sessions/session.jsonl");
    expect(args).toContain("-v");
    expect(args).toContain(`${runtimeDir}:/runtime:ro`);
    expect(args).toContain("-e");
    expect(args).toContain("DANO_RUNTIME_DIR=/runtime");
    expect(args).toContain("DANO_BASH_ACCEPTANCE_MARKER=DANO_BASH_OK");
    expect(args).toContain("DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS=TOKEN missing");
    expect(
      args.find((arg: string) => arg.endsWith(":/app:ro")),
    ).toBeTruthy();
  });

  it("checks real bash acceptance from session jsonl without reading secrets", () => {
    const runtimeDir = mkdtempSync(join(tmpdir(), "dano-bash-acceptance-"));
    tempDirs.push(runtimeDir);
    const sessionDir = join(runtimeDir, "workspaces/ws_test/.dano/sessions");
    mkdirSync(sessionDir, { recursive: true });
    writeFileSync(
      join(sessionDir, "session.jsonl"),
      [
        JSON.stringify({
          type: "message",
          message: {
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "tool-1",
                name: "bash",
                arguments: { command: "printf DANO_BASH_OK" },
              },
            ],
          },
        }),
        JSON.stringify({
          type: "message",
          message: {
            role: "toolResult",
            toolCallId: "tool-1",
            toolName: "bash",
            isError: false,
            content: [{ type: "text", text: "DANO_BASH_OK" }],
          },
        }),
        "",
      ].join("\n"),
    );

    expect(
      execFileSync(process.execPath, [bashAcceptanceScript, sessionDir], {
        encoding: "utf8",
      }),
    ).toContain("bwrap errors: no");
  });

  it("checks OA gateway presence markers in model-triggered bash output", () => {
    const runtimeDir = mkdtempSync(join(tmpdir(), "dano-bash-acceptance-"));
    tempDirs.push(runtimeDir);
    const sessionDir = join(runtimeDir, "workspaces/ws_test/.dano/sessions");
    mkdirSync(sessionDir, { recursive: true });
    writeFileSync(
      join(sessionDir, "session.jsonl"),
      [
        JSON.stringify({ type: "toolCall", name: "bash" }),
        JSON.stringify({
          role: "toolResult",
          toolName: "bash",
          isError: false,
          content: [
            {
              type: "text",
              text: "DANO_BASH_OK\nURL_PRESENT\nTENANT_PRESENT\nleave options listed",
            },
          ],
        }),
        "",
      ].join("\n"),
    );

    const output = execFileSync(process.execPath, [bashAcceptanceScript, sessionDir], {
      encoding: "utf8",
      env: {
        ...process.env,
        DANO_BASH_ACCEPTANCE_REQUIRED_MARKERS: "URL_PRESENT,TENANT_PRESENT",
        DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS:
          "URL_MISSING,TENANT_MISSING,DANO_URL/DANO_TENANT_KEY 未设置",
      },
    });

    expect(output).toContain("required marker URL_PRESENT: yes");
    expect(output).toContain("required marker TENANT_PRESENT: yes");
    expect(output).toContain("forbidden marker TENANT_MISSING: no");
  });

  it("fails OA gateway acceptance when tenant is missing", () => {
    const runtimeDir = mkdtempSync(join(tmpdir(), "dano-bash-acceptance-"));
    tempDirs.push(runtimeDir);
    mkdirSync(runtimeDir, { recursive: true });
    writeFileSync(
      join(runtimeDir, "session.jsonl"),
      [
        JSON.stringify({ type: "toolCall", name: "bash" }),
        JSON.stringify({
          role: "toolResult",
          toolName: "bash",
          isError: false,
          content: "DANO_BASH_OK\nURL_PRESENT\nTENANT_MISSING",
        }),
        "",
      ].join("\n"),
    );

    expect(() =>
      execFileSync(process.execPath, [bashAcceptanceScript, runtimeDir], {
        env: {
          ...process.env,
          DANO_BASH_ACCEPTANCE_REQUIRED_MARKERS: "URL_PRESENT,TENANT_PRESENT",
          DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS: "TENANT_MISSING",
        },
      }),
    ).toThrow();
  });

  it("fails OA gateway acceptance when the skill reports missing env", () => {
    const runtimeDir = mkdtempSync(join(tmpdir(), "dano-bash-acceptance-"));
    tempDirs.push(runtimeDir);
    mkdirSync(runtimeDir, { recursive: true });
    writeFileSync(
      join(runtimeDir, "session.jsonl"),
      [
        JSON.stringify({ type: "toolCall", name: "bash" }),
        JSON.stringify({
          role: "toolResult",
          toolName: "bash",
          isError: false,
          content:
            'DANO_BASH_OK\nURL_PRESENT\nTENANT_PRESENT\n{"status":"failed","reason":"DANO_URL/DANO_TENANT_KEY 未设置(部署方配置,勿写进文件)"}',
        }),
        "",
      ].join("\n"),
    );

    expect(() =>
      execFileSync(process.execPath, [bashAcceptanceScript, runtimeDir], {
        env: {
          ...process.env,
          DANO_BASH_ACCEPTANCE_REQUIRED_MARKERS: "URL_PRESENT,TENANT_PRESENT",
          DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS:
            "DANO_URL/DANO_TENANT_KEY 未设置",
        },
      }),
    ).toThrow();
  });

  it("checks only the requested bash acceptance session", () => {
    const runtimeDir = mkdtempSync(join(tmpdir(), "dano-bash-acceptance-"));
    tempDirs.push(runtimeDir);
    const oldSessionDir = join(runtimeDir, "workspaces/old/.dano/sessions");
    const newSessionDir = join(runtimeDir, "workspaces/new/.dano/sessions");
    mkdirSync(oldSessionDir, { recursive: true });
    mkdirSync(newSessionDir, { recursive: true });
    writeFileSync(
      join(oldSessionDir, "session.jsonl"),
      [
        JSON.stringify({ type: "toolCall", name: "bash" }),
        JSON.stringify({
          role: "toolResult",
          toolName: "bash",
          isError: true,
          content: "bwrap must be installed setuid",
        }),
        "",
      ].join("\n"),
    );
    writeFileSync(
      join(newSessionDir, "session.jsonl"),
      [
        JSON.stringify({ type: "toolCall", name: "bash" }),
        JSON.stringify({
          role: "toolResult",
          toolName: "bash",
          isError: false,
          content: [{ type: "text", text: "DANO_BASH_OK" }],
        }),
        "",
      ].join("\n"),
    );

    expect(
      execFileSync(process.execPath, [bashAcceptanceScript, newSessionDir], {
        encoding: "utf8",
      }),
    ).toContain("bwrap errors: no");
  });

  it("requires an explicit bash acceptance scope by default", () => {
    const runtimeDir = mkdtempSync(join(tmpdir(), "dano-bash-acceptance-"));
    tempDirs.push(runtimeDir);

    expect(() =>
      execFileSync(process.execPath, [bashAcceptanceScript], {
        env: { ...process.env, DANO_RUNTIME_DIR: runtimeDir },
      }),
    ).toThrow();
  });

  it("does not treat the runtime root as a bash acceptance session directory", () => {
    const runtimeDir = mkdtempSync(join(tmpdir(), "dano-bash-acceptance-"));
    tempDirs.push(runtimeDir);
    const sessionDir = join(runtimeDir, "workspaces/ws_test/.dano/sessions");
    mkdirSync(sessionDir, { recursive: true });
    writeFileSync(
      join(sessionDir, "session.jsonl"),
      [
        JSON.stringify({ type: "toolCall", name: "bash" }),
        JSON.stringify({
          role: "toolResult",
          toolName: "bash",
          isError: false,
          content: [{ type: "text", text: "DANO_BASH_OK" }],
        }),
        "",
      ].join("\n"),
    );

    expect(() =>
      execFileSync(process.execPath, [bashAcceptanceScript, runtimeDir]),
    ).toThrow();
  });

  it("fails bash acceptance when Bubblewrap errors appear", () => {
    const runtimeDir = mkdtempSync(join(tmpdir(), "dano-bash-acceptance-"));
    tempDirs.push(runtimeDir);
    mkdirSync(runtimeDir, { recursive: true });
    writeFileSync(
      join(runtimeDir, "session.jsonl"),
      [
        JSON.stringify({
          type: "message",
          message: {
            role: "assistant",
            content: [
              {
                type: "toolCall",
                id: "tool-1",
                name: "bash",
                arguments: { command: "printf DANO_BASH_OK" },
              },
            ],
          },
        }),
        JSON.stringify({
          type: "message",
          message: {
            role: "toolResult",
            toolCallId: "tool-1",
            toolName: "bash",
            isError: true,
            content: [{ type: "text", text: "bwrap must be installed setuid" }],
          },
        }),
        "",
      ].join("\n"),
    );

    expect(() =>
      execFileSync(process.execPath, [bashAcceptanceScript, runtimeDir]),
    ).toThrow();
  });

  it("initializes runtime defaults in the global agent config directory", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-entrypoint-test-"));
    tempDirs.push(cwd);
    const defaultsDir = join(cwd, "defaults");
    const runtimeDir = join(cwd, "runtime-data");
    const workspaceDir = join(cwd, "workspace");
    const agentDir = join(runtimeDir, ".pi/agent");
    const agentDirOut = join(cwd, "agent-dir.txt");

    mkdirSync(defaultsDir, { recursive: true });
    mkdirSync(agentDir, { recursive: true });
    writeFileSync(
      join(defaultsDir, "SYSTEM.md"),
      "你是{产品名称}，deployment default\n",
    );
    writeFileSync(join(defaultsDir, "settings.json"), "{\"default\":true}\n");
    writeFileSync(join(defaultsDir, "heimdall.json"), "{\"guard\":true}\n");
    writeFileSync(join(agentDir, "settings.json"), "{\"custom\":true}\n");

    execFileSync("sh", [
      entrypointFile,
      "/bin/sh",
      "-c",
      'printf "%s" "$PI_CODING_AGENT_DIR" > "$DANO_AGENT_DIR_OUT"',
    ], {
      env: {
        ...process.env,
        PATH: nodeOnlyPath(cwd),
        DANO_RUNTIME_DEFAULTS_DIR: defaultsDir,
        DANO_RUNTIME_DIR: runtimeDir,
        DANO_PRODUCT_NAME: " 部署助手 ",
        DANO_DEFAULT_WORKSPACE_PATH: workspaceDir,
        DANO_AGENT_DIR_OUT: agentDirOut,
      },
    });

    expect(readFileSync(agentDirOut, "utf8")).toBe(agentDir);
    expect(readFileSync(join(agentDir, "SYSTEM.md"), "utf8")).toBe(
      "你是部署助手，deployment default\n",
    );
    expect(readFileSync(join(agentDir, "settings.json"), "utf8")).toBe(
      "{\"custom\":true}\n",
    );
    expect(readFileSync(join(agentDir, "heimdall.json"), "utf8")).toBe(
      "{\"guard\":true}\n",
    );
    expect(existsSync(join(workspaceDir, ".pi"))).toBe(false);
  });

  it("runs the local open-websearch daemon for the lifetime of the app", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-open-websearch-entrypoint-"));
    tempDirs.push(cwd);
    const defaultsDir = join(cwd, "defaults");
    const runtimeDir = join(cwd, "runtime-data");
    const fakeBin = join(cwd, "node-bin");
    const appPath = join(cwd, "dist/server/main.js");
    const daemonStarted = join(cwd, "daemon-started");
    const daemonStopped = join(cwd, "daemon-stopped");
    const appStarted = join(cwd, "app-started");

    mkdirSync(defaultsDir, { recursive: true });
    mkdirSync(join(cwd, "dist/server"), { recursive: true });
    writeFileSync(join(defaultsDir, "SYSTEM.md"), "你是{产品名称}\n");
    writeFileSync(join(defaultsDir, "settings.json"), "{}\n");
    writeFileSync(join(defaultsDir, "heimdall.json"), "{}\n");
    const path = nodeOnlyPath(cwd);
    writeFileSync(
      join(fakeBin, "open-websearch"),
      `#!/bin/sh
case "$1" in
  serve)
    : > "$DANO_TEST_DAEMON_STARTED"
    trap ': > "$DANO_TEST_DAEMON_STOPPED"; exit 0' TERM INT
    while :; do sleep 1; done
    ;;
  status)
    test -f "$DANO_TEST_DAEMON_STARTED"
    ;;
  *)
    exit 2
    ;;
esac
`,
    );
    chmodSync(join(fakeBin, "open-websearch"), 0o755);
    writeFileSync(
      appPath,
      `import { writeFileSync } from "node:fs";
writeFileSync(process.env.DANO_TEST_APP_STARTED, "started");
`,
    );

    execFileSync("sh", [entrypointFile], {
      cwd,
      env: {
        ...process.env,
        PATH: path,
        DANO_RUNTIME_DEFAULTS_DIR: defaultsDir,
        DANO_RUNTIME_DIR: runtimeDir,
        DANO_PRODUCT_NAME: "测试助手",
        DANO_SKILL_SEED_DIR: join(cwd, "missing-seed"),
        DANO_TEST_DAEMON_STARTED: daemonStarted,
        DANO_TEST_DAEMON_STOPPED: daemonStopped,
        DANO_TEST_APP_STARTED: appStarted,
      },
      timeout: 10_000,
    });

    expect(readFileSync(appStarted, "utf8")).toBe("started");
    expect(existsSync(daemonStarted)).toBe(true);
    expect(existsSync(daemonStopped)).toBe(true);
  });

  it("renders a missing system prompt from the configured product name", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-entrypoint-test-"));
    tempDirs.push(cwd);
    const defaultsDir = join(cwd, "defaults");
    const runtimeDir = join(cwd, "runtime-data");
    const agentDir = join(runtimeDir, ".pi/agent");
    const configPath = join(cwd, "dano.config.json");

    mkdirSync(defaultsDir, { recursive: true });
    writeFileSync(join(defaultsDir, "SYSTEM.md"), "你是{产品名称}\n");
    writeFileSync(configPath, JSON.stringify({ productName: "配置助手" }));

    execFileSync("sh", [entrypointFile, "/usr/bin/true"], {
      env: {
        ...process.env,
        PATH: nodeOnlyPath(cwd),
        DANO_RUNTIME_DEFAULTS_DIR: defaultsDir,
        DANO_RUNTIME_DIR: runtimeDir,
        DANO_PRODUCT_NAME: "",
        DANO_CONFIG_PATH: configPath,
      },
    });

    expect(readFileSync(join(agentDir, "SYSTEM.md"), "utf8")).toBe(
      "你是配置助手\n",
    );
  });

  it("replaces an existing system prompt during an explicit deployment sync", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-system-sync-test-"));
    tempDirs.push(cwd);
    const templatePath = join(cwd, "SYSTEM.template.md");
    const targetPath = join(cwd, "SYSTEM.md");
    writeFileSync(templatePath, "你是{产品名称}，请联系{产品名称}\n");
    writeFileSync(targetPath, "宿主机保留内容\n");

    execFileSync(
      process.execPath,
      [renderSystemPromptScript, "--replace", templatePath, targetPath],
      {
        env: {
          ...process.env,
          DANO_PRODUCT_NAME: " 第二验收助手 ",
        },
      },
    );

    expect(readFileSync(targetPath, "utf8")).toBe(
      "你是第二验收助手，请联系第二验收助手\n",
    );
  });

  it("honors an existing PI_CODING_AGENT_DIR", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-entrypoint-test-"));
    tempDirs.push(cwd);
    const defaultsDir = join(cwd, "defaults");
    const runtimeDir = join(cwd, "runtime-data");
    const agentDir = join(cwd, "custom-agent");
    const agentDirOut = join(cwd, "agent-dir.txt");

    mkdirSync(defaultsDir, { recursive: true });
    writeFileSync(
      join(defaultsDir, "SYSTEM.md"),
      "你是{产品名称}，deployment default\n",
    );
    writeFileSync(join(defaultsDir, "settings.json"), "{\"default\":true}\n");
    writeFileSync(join(defaultsDir, "heimdall.json"), "{\"guard\":true}\n");
    mkdirSync(agentDir, { recursive: true });
    writeFileSync(join(agentDir, "SYSTEM.md"), "宿主机自定义 prompt\n");

    execFileSync("sh", [
      entrypointFile,
      "/bin/sh",
      "-c",
      'printf "%s" "$PI_CODING_AGENT_DIR" > "$DANO_AGENT_DIR_OUT"',
    ], {
      env: {
        ...process.env,
        PATH: nodeOnlyPath(cwd),
        DANO_RUNTIME_DEFAULTS_DIR: defaultsDir,
        DANO_RUNTIME_DIR: runtimeDir,
        DANO_PRODUCT_NAME: "部署助手",
        PI_CODING_AGENT_DIR: agentDir,
        DANO_AGENT_DIR_OUT: agentDirOut,
      },
    });

    expect(readFileSync(agentDirOut, "utf8")).toBe(agentDir);
    expect(readFileSync(join(agentDir, "SYSTEM.md"), "utf8")).toBe(
      "宿主机自定义 prompt\n",
    );
    expect(existsSync(join(runtimeDir, "default-settings"))).toBe(false);
  });

  it("builds releases from a temporary checkout and cleans it up", () => {
    const { buildParent, deployDir, runtimeDir, nginxConf, logLines } =
      runRelease();

    expect(readdirSync(buildParent)).toEqual([]);
    expect(readFileSync(join(deployDir, "docker-compose.yml"), "utf8")).toContain(
      "services:",
    );
    expect(
      readFileSync(join(deployDir, "docker-compose.exposure.yml"), "utf8"),
    ).toContain("80:80");
    expect(readFileSync(nginxConf, "utf8")).toContain("listen 80");
    expect(existsSync(join(deployDir, "nginx/demo-auth.conf.template"))).toBe(
      false,
    );
    expect(
      readFileSync(join(deployDir, "nginx/shared/proxy-server.conf"), "utf8"),
    ).toContain("location /");
    expect(readFileSync(join(deployDir, ".env"), "utf8")).toContain(
      `DANO_RUNTIME_DIR=${runtimeDir}`,
    );
    expect(readFileSync(join(deployDir, ".env"), "utf8")).not.toMatch(
      /DANO_(?:AUTH_JWT|DEMO)/,
    );
    expect(JSON.parse(logLines[0])).toEqual([
      "build",
      "--build-arg",
      "NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/",
      "-t",
      "dano-app:abc123",
      expect.stringContaining("dano-build-"),
    ]);
    expect(JSON.parse(logLines[1])).toEqual([
      "chown",
      "-R",
      "1000:1000",
      runtimeDir,
    ]);
    expect(JSON.parse(logLines[2])).toEqual([
      "compose",
      "-f",
      "docker-compose.yml",
      "-f",
      "docker-compose.exposure.yml",
      "--env-file",
      ".env",
      "run",
      "--rm",
      "--no-deps",
      "--entrypoint",
      "node",
      "app",
      "./dist/server/main.js",
      "--validate-config",
    ]);
    expect(JSON.parse(logLines[3])).toEqual([
      "compose",
      "-f",
      "docker-compose.yml",
      "-f",
      "docker-compose.exposure.yml",
      "--env-file",
      ".env",
      "run",
      "--rm",
      "--no-deps",
      "--entrypoint",
      "node",
      "app",
      "./deploy/system-prompt.mjs",
      "sync",
    ]);
    expect(JSON.parse(logLines[4]).slice(-2)).toEqual(["./deploy/system-prompt.mjs", "check"]);
    expect(JSON.parse(logLines[5])).toEqual([
      "compose",
      "-f",
      "docker-compose.yml",
      "-f",
      "docker-compose.exposure.yml",
      "--env-file",
      ".env",
      "up",
      "-d",
      "--no-build",
      "app",
      "nginx",
    ]);
    expect(logLines[6]).toBe("smoke");
  });

  it("initializes Dano-owned OAuth credential encryption before the production gate", () => {
    const markerRoot = mkdtempSync(join(tmpdir(), "dano-release-oauth-key-"));
    tempDirs.push(markerRoot);
    const deployDir = join(markerRoot, "deploy");
    mkdirSync(deployDir);
    writeFileSync(join(deployDir, ".env"), "DANO_OPERATOR_VALUE=preserve-me\n", {
      mode: 0o600,
    });

    const firstRelease = runRelease({
      deployDir,
      env: { DANO_FAKE_REQUIRE_OAUTH_CREDENTIAL_PAIR: "1" },
    });

    const firstEnv = readFileSync(join(deployDir, ".env"), "utf8");
    const key = firstEnv.match(
      /^DANO_OAUTH_CREDENTIAL_KEY=([A-Za-z0-9_-]+)$/m,
    )?.[1];
    expect(Buffer.from(key ?? "", "base64url")).toHaveLength(32);
    expect(firstEnv).toContain(
      "DANO_OAUTH_CREDENTIAL_KEY_VERSION=dano-deploy-v1",
    );
    expect(firstEnv).toContain("DANO_OPERATOR_VALUE=preserve-me");
    expect(statSync(join(deployDir, ".env")).mode & 0o777).toBe(0o600);
    expect(firstRelease.output).toContain(
      "initialized Dano-owned OAuth credential encryption material",
    );
    expect(firstRelease.output).not.toContain(key);

    const secondRelease = runRelease({
      deployDir,
      env: { DANO_FAKE_REQUIRE_OAUTH_CREDENTIAL_PAIR: "1" },
    });
    const secondEnv = readFileSync(join(deployDir, ".env"), "utf8");
    const credentialLines = (env: string) =>
      env
        .split("\n")
        .filter(line => line.startsWith("DANO_OAUTH_CREDENTIAL_KEY"));
    expect(credentialLines(secondEnv)).toEqual(credentialLines(firstEnv));
    expect(secondEnv).toContain(`DANO_OAUTH_CREDENTIAL_KEY=${key}`);
    expect(secondEnv).toContain(
      "DANO_OAUTH_CREDENTIAL_KEY_VERSION=dano-deploy-v1",
    );
    expect(secondRelease.output).toContain(
      "preserved Dano-owned OAuth credential encryption material",
    );
    expect(secondRelease.output).not.toContain(key);
  });

  it.each([
    [
      "key",
      `DANO_OAUTH_CREDENTIAL_KEY=${Buffer.alloc(32, 7).toString("base64url")}\n`,
    ],
    ["version", "DANO_OAUTH_CREDENTIAL_KEY_VERSION=operator-v7\n"],
  ])("fails closed when only the OAuth credential %s exists", (_field, content) => {
    const markerRoot = mkdtempSync(join(tmpdir(), "dano-release-oauth-partial-"));
    tempDirs.push(markerRoot);
    const deployDir = join(markerRoot, "deploy");
    mkdirSync(deployDir);
    writeFileSync(join(deployDir, ".env"), content, { mode: 0o600 });

    expect(() => runRelease({ deployDir })).toThrow(/must be configured together/);
    expect(readFileSync(join(deployDir, ".env"), "utf8")).toContain(content.trim());
    expect(statSync(join(deployDir, ".env")).mode & 0o777).toBe(0o600);
  });

  it("stops a release before switching containers when configuration validation fails", () => {
    const markerRoot = mkdtempSync(join(tmpdir(), "dano-release-gate-"));
    tempDirs.push(markerRoot);
    const switchMarker = join(markerRoot, "switched");
    const deployDir = join(markerRoot, "deploy");
    mkdirSync(deployDir);
    writeFileSync(
      join(deployDir, ".env"),
      "DANO_DEMO_JWT=legacy-token\nDANO_OPERATOR_VALUE=preserve-me\n",
    );
    expect(() =>
      runRelease({
        deployDir,
        env: {
          DANO_FAKE_CONFIG_INVALID: "1",
          DANO_FAKE_SWITCH_MARKER: switchMarker,
        },
      }),
    ).toThrow();
    expect(existsSync(switchMarker)).toBe(false);
    expect(readFileSync(join(deployDir, ".env"), "utf8")).toContain(
      "DANO_DEMO_JWT=legacy-token",
    );
  });

  it("refuses a release while SYSTEM browser acceptance is pending", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-pending-system-"));
    tempDirs.push(root);
    mkdirSync(join(root, ".system-rollback-pending"));
    expect(() => runRelease({ deployDir: root })).toThrow("Resolve pending SYSTEM browser acceptance");
  });

  it.each(["sync", "check"])("stops release before Compose switch when SYSTEM %s fails", phase => {
    const root = mkdtempSync(join(tmpdir(), "dano-system-gate-"));
    tempDirs.push(root);
    const marker = join(root, "switched");
    expect(() => runRelease({ env: { DANO_FAKE_SYSTEM_FAIL: phase, DANO_FAKE_SWITCH_MARKER: marker } })).toThrow();
    expect(existsSync(marker)).toBe(false);
  });

  it("removes only fixed Demo values after a successful production gate", () => {
    const markerRoot = mkdtempSync(join(tmpdir(), "dano-release-cleanup-"));
    tempDirs.push(markerRoot);
    const deployDir = join(markerRoot, "deploy");
    mkdirSync(deployDir);
    writeFileSync(
      join(deployDir, ".env"),
      [
        "DANO_DEMO_JWT=legacy-token",
        "DANO_AUTH_JWT_SECRET=legacy-secret",
        "DANO_OPERATOR_VALUE=preserve-me",
        "",
      ].join("\n"),
    );

    runRelease({ deployDir });

    const env = readFileSync(join(deployDir, ".env"), "utf8");
    expect(env).not.toMatch(/DANO_(?:DEMO_JWT|AUTH_JWT_SECRET)=/);
    expect(env).toContain("DANO_OPERATOR_VALUE=preserve-me");
  });

  it("does not expose a production node-binary override", () => {
    expect(readFileSync(entrypointFile, "utf8")).not.toContain(
      "DANO_NODE_BINARY",
    );
  });

  it("deploys HTTPS-only with arbitrary host certificate filenames", () => {
    const { deployDir, nginxConf, tlsCertPath, tlsKeyPath } = runRelease({
      exposureMode: "https",
    });

    expect(
      readFileSync(join(deployDir, "docker-compose.exposure.yml"), "utf8"),
    ).toContain("443:443");
    expect(readFileSync(nginxConf, "utf8")).toContain("listen 443 ssl");
    const env = readFileSync(join(deployDir, ".env"), "utf8");
    expect(env).toContain("DANO_EXPOSURE_MODE=https");
    expect(env).toContain(
      `DANO_TLS_CERT_PATH='${tlsCertPath.replaceAll("'", "\\\\'")}'`,
    );
    expect(env).toContain(
      `DANO_TLS_KEY_PATH='${tlsKeyPath.replaceAll("'", "\\\\'")}'`,
    );
  });

  it("resolves relative TLS paths from the Deploy Control Directory", () => {
    const { deployDir, tlsCertPath, tlsKeyPath } = runRelease({
      exposureMode: "https",
      tlsPathStyle: "relative",
    });

    const env = readFileSync(join(deployDir, ".env"), "utf8");
    expect(env).toContain(
      `DANO_TLS_CERT_PATH='${tlsCertPath.replaceAll("'", "\\\\'")}'`,
    );
    expect(env).toContain(
      `DANO_TLS_KEY_PATH='${tlsKeyPath.replaceAll("'", "\\\\'")}'`,
    );
  });

  it.each([
    ["both", "return 308"],
    ["both-no-redirect-http", "listen 80"],
  ] as const)("deploys the %s exposure mode", (exposureMode, nginxMarker) => {
    const { deployDir, nginxConf } = runRelease({ exposureMode });

    const compose = readFileSync(
      join(deployDir, "docker-compose.exposure.yml"),
      "utf8",
    );
    expect(compose).toContain("80:80");
    expect(compose).toContain("443:443");
    expect(readFileSync(nginxConf, "utf8")).toContain(nginxMarker);
  });

  it("rejects TLS exposure before deployment when certificate inputs are missing", () => {
    expect(() =>
      runRelease({ exposureMode: "https", provideTlsInputs: false }),
    ).toThrow();
  });

  it("rejects TLS inputs that are not files", () => {
    expect(() =>
      runRelease({ exposureMode: "https", tlsInputsAreDirectories: true }),
    ).toThrow();
  });

  it("lets NPM_REGISTRY override the default release build registry", () => {
    const { logLines } = runRelease({
      env: { NPM_REGISTRY: "https://registry.npmjs.org/" },
    });

    expect(JSON.parse(logLines[0])).toContain(
      "NPM_REGISTRY=https://registry.npmjs.org/",
    );
  });

  it("falls back to full clone when partial clone is unavailable", () => {
    const { logLines } = runRelease({
      env: { DANO_FAKE_GIT_REJECT_FILTER: "1" },
    });

    expect(JSON.parse(logLines[0])[0]).toBe("build");
  });

  it("keeps NPM_CONFIG_REGISTRY as a release build compatibility override", () => {
    const { logLines } = runRelease({
      env: { NPM_CONFIG_REGISTRY: "https://registry.example.test/" },
    });

    expect(JSON.parse(logLines[0])).toContain(
      "NPM_REGISTRY=https://registry.example.test/",
    );
  });
});
