#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { X509Certificate } from "node:crypto";

const sourceRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const releaseScript = join(sourceRoot, "scripts/deploy-release.mjs");
const runtimeBin = process.env.DANO_COMPOSE || "docker";
const image = process.env.DANO_IMAGE?.trim();
const workDir = mkdtempSync(join(tmpdir(), "dano-exposure-acceptance-"));
const fakeBin = join(workDir, "bin");
const buildParent = join(workDir, "builds");
const project = `dano-exposure-${process.pid}`;
const skillmannerNetwork = "skillmanner_default";
let createdSkillmannerNetwork = false;

if (!image) {
  throw new Error(
    "DANO_IMAGE is required; build the current Dano image before exposure acceptance",
  );
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env || process.env,
    encoding: options.capture ? "utf8" : undefined,
    stdio: options.capture ? "pipe" : "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0 && !options.allowFailure) {
    throw new Error(`${command} ${args.join(" ")} exited with ${result.status}`);
  }
  return result;
}

async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise((resolve, reject) =>
    server.close(error => (error ? reject(error) : resolve())),
  );
  return port;
}

function probe(url, { rejectUnauthorized = true } = {}) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const request = (parsed.protocol === "https:" ? httpsRequest : httpRequest)(
      parsed,
      { rejectUnauthorized, timeout: 2_000 },
      response => {
        let body = "";
        let peerFingerprint;
        try {
          peerFingerprint =
            parsed.protocol === "https:"
              ? response.socket.getPeerCertificate().fingerprint256
              : undefined;
        } catch (error) {
          response.resume();
          reject(error);
          return;
        }
        response.setEncoding("utf8");
        response.on("data", chunk => {
          body += chunk;
        });
        response.on("end", () =>
          resolve({
            status: response.statusCode,
            location: response.headers.location,
            body,
            peerFingerprint,
          }),
        );
      },
    );
    request.once("timeout", () => request.destroy(new Error("request timeout")));
    request.once("error", reject);
    request.end();
  });
}

async function waitForHealth(url) {
  const deadline = Date.now() + 75_000;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await probe(url, { rejectUnauthorized: false });
      if (response.status === 200 && response.body.includes('"status":"ok"')) {
        return response;
      }
      lastError = new Error(`unexpected health response: ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw lastError || new Error(`health check timed out: ${url}`);
}

async function expectUnavailable(url) {
  try {
    await probe(url, { rejectUnauthorized: false });
  } catch {
    return;
  }
  throw new Error(`endpoint should not be published: ${url}`);
}

function writeHelpers() {
  mkdirSync(fakeBin, { recursive: true });
  mkdirSync(buildParent, { recursive: true });
  writeFileSync(
    join(fakeBin, "git"),
    `#!/usr/bin/env node
import { cpSync, mkdirSync } from "node:fs";
import { join } from "node:path";
const args = process.argv.slice(2);
if (args[0] === "clone") {
  const target = args.at(-1);
  mkdirSync(join(target, "scripts"), { recursive: true });
  cpSync(join(process.env.DANO_ACCEPTANCE_SOURCE, "docker-compose.yml"), join(target, "docker-compose.yml"));
  cpSync(join(process.env.DANO_ACCEPTANCE_SOURCE, "deploy"), join(target, "deploy"), { recursive: true });
  cpSync(join(process.env.DANO_ACCEPTANCE_SOURCE, "scripts/smoke-dano-deploy.mjs"), join(target, "scripts/smoke-dano-deploy.mjs"));
}
`,
  );
  writeFileSync(
    join(fakeBin, "chown"),
    "#!/usr/bin/env node\n",
  );
  writeFileSync(
    join(fakeBin, "compose-runtime"),
    `#!/usr/bin/env node
import { spawnSync } from "node:child_process";
const args = process.argv.slice(2);
if (args[0] === "build") process.exit(0);
if (args[0] !== "compose") process.exit(2);
const result = spawnSync(process.env.DANO_ACCEPTANCE_RUNTIME, args, { stdio: "inherit", env: process.env });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
`,
  );
  for (const name of ["git", "chown", "compose-runtime"]) {
    chmodSync(join(fakeBin, name), 0o755);
  }
}

function compose(deployDir, args, { allowFailure = false } = {}) {
  return run(runtimeBin, [
    "compose",
    "-p",
    project,
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.exposure.yml",
    "--env-file",
    ".env",
    ...args,
  ], { cwd: deployDir, allowFailure });
}

function release(mode, deployDir, runtimeDir, secretsDir, certPath, keyPath, httpPort, httpsPort) {
  const smokeUrl =
    mode === "http"
      ? `http://127.0.0.1:${httpPort}`
      : `https://127.0.0.1:${httpsPort}`;
  run(process.execPath, [releaseScript], {
    cwd: sourceRoot,
    env: {
      ...process.env,
      PATH: `${fakeBin}:${process.env.PATH}`,
      COMPOSE_PROJECT_NAME: project,
      DANO_ACCEPTANCE_RUNTIME: runtimeBin,
      DANO_ACCEPTANCE_SOURCE: sourceRoot,
      DANO_BUILD_PARENT_DIR: buildParent,
      DANO_COMPOSE: join(fakeBin, "compose-runtime"),
      DANO_DEPLOY_DIR: deployDir,
      DANO_EXPOSURE_MODE: mode,
      DANO_GIT_REF: "acceptance",
      DANO_HTTPS_PORT: String(httpsPort),
      DANO_IMAGE: image,
      DANO_NGINX_PORT: String(httpPort),
      DANO_REPO_URL: "acceptance-source",
      DANO_RUNTIME_DIR: runtimeDir,
      DANO_SECRETS_DIR: secretsDir,
      DANO_SMOKE_BASE_URL: smokeUrl,
      DANO_TLS_CERT_PATH: certPath,
      DANO_TLS_KEY_PATH: keyPath,
      NODE_TLS_REJECT_UNAUTHORIZED: "0",
    },
  });
}

async function verifyMode(mode, certPath, keyPath, certificateFingerprint) {
  const deployDir = join(workDir, mode, "deploy");
  const runtimeDir = join(workDir, mode, "runtime-data");
  const secretsDir = join(workDir, mode, "secrets");
  const httpPort = await freePort();
  const httpsPort = await freePort();

  try {
    release(
      mode,
      deployDir,
      runtimeDir,
      secretsDir,
      certPath,
      keyPath,
      httpPort,
      httpsPort,
    );
    const httpHealth = `http://127.0.0.1:${httpPort}/api/health`;
    const httpsHealth = `https://127.0.0.1:${httpsPort}/api/health`;

    if (mode === "http") {
      await waitForHealth(httpHealth);
      await expectUnavailable(httpsHealth);
    } else if (mode === "https") {
      const response = await waitForHealth(httpsHealth);
      if (response.peerFingerprint !== certificateFingerprint) {
        throw new Error("HTTPS mode did not serve the supplied certificate");
      }
      await expectUnavailable(httpHealth);
    } else if (mode === "both") {
      await waitForHealth(httpsHealth);
      const redirect = await probe(
        `http://127.0.0.1:${httpPort}/api/health?source=acceptance`,
      );
      const expected = `https://127.0.0.1:${httpsPort}/api/health?source=acceptance`;
      if (redirect.status !== 308 || redirect.location !== expected) {
        throw new Error(
          `both mode redirect mismatch: ${redirect.status} ${redirect.location}`,
        );
      }
    } else {
      await waitForHealth(httpHealth);
      await waitForHealth(httpsHealth);
    }
    console.log(`[deploy-exposure] ${mode}: passed`);
  } finally {
    compose(deployDir, ["down", "--volumes", "--remove-orphans"], {
      allowFailure: true,
    });
  }
}

try {
  writeHelpers();
  const network = run(runtimeBin, ["network", "inspect", skillmannerNetwork], {
    capture: true,
    allowFailure: true,
  });
  if (network.status !== 0) {
    run(runtimeBin, ["network", "create", skillmannerNetwork]);
    createdSkillmannerNetwork = true;
  }

  const generatedCert = join(workDir, "generated-cert.pem");
  const generatedKey = join(workDir, "generated-key.pem");
  run(runtimeBin, [
    "run",
    "--rm",
    "--user",
    "0",
    "--entrypoint",
    "openssl",
    "-v",
    `${workDir}:/work`,
    image,
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-days",
    "1",
    "-subj",
    "/CN=localhost",
    "-addext",
    "subjectAltName=DNS:localhost,IP:127.0.0.1",
    "-keyout",
    "/work/generated-key.pem",
    "-out",
    "/work/generated-cert.pem",
  ], { capture: true });

  const delimiter = runtimeBin === "podman" ? " " : ":";
  const certPath = join(workDir, `operator${delimiter}\\$certificate#1.pem`);
  const keyPath = join(workDir, `operator${delimiter}\\$private-key#1.pem`);
  renameSync(generatedCert, certPath);
  renameSync(generatedKey, keyPath);
  const certificateFingerprint = new X509Certificate(
    readFileSync(certPath),
  ).fingerprint256;

  for (const mode of [
    "http",
    "https",
    "both",
    "both-no-redirect-http",
  ]) {
    await verifyMode(mode, certPath, keyPath, certificateFingerprint);
  }
} finally {
  if (createdSkillmannerNetwork) {
    run(runtimeBin, ["network", "rm", skillmannerNetwork], {
      allowFailure: true,
    });
  }
  rmSync(workDir, { recursive: true, force: true });
}
