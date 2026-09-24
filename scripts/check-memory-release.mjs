#!/usr/bin/env node
import { createReadStream } from "node:fs";
import { lstat, readFile, realpath } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const deployment = process.argv.slice(2).includes("--deployment");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function json(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function privatePath(path, directory) {
  const stat = await lstat(path);
  assert(directory ? stat.isDirectory() : stat.isFile(), "private release path has wrong type");
  assert((stat.mode & 0o077) === 0, "private release path has shared permissions");
  if (!directory) assert(stat.nlink === 1, "private release file has multiple links");
  return realpath(path);
}

async function sha256(path) {
  const hash = createHash("sha256");
  for await (const part of createReadStream(path)) hash.update(part);
  return hash.digest("hex");
}

try {
  assert(process.argv.slice(2).every(arg => arg === "--deployment"), "unknown option");
  const release = await json(join(root, "deploy/memory-release.json"));
  const product = await json(join(root, "package.json"));
  const app = await json(join(root, "apps/dano/package.json"));
  const lock = parseYaml(await readFile(join(root, "pnpm-lock.yaml"), "utf8"));
  assert(product.version === release.danoVersion, "Dano product version differs from memory release");
  assert(app.dependencies["@josephyoung/pi-openviking"] === release.piOpenVikingVersion,
    "pi-openviking dependency differs from memory release");
  const pinned = lock?.importers?.["apps/dano"]?.dependencies?.["@josephyoung/pi-openviking"];
  assert(pinned?.specifier === release.piOpenVikingVersion &&
    typeof pinned.version === "string" && pinned.version.startsWith(`${release.piOpenVikingVersion}(`),
  "lockfile differs from memory release");
  assert(new RegExp(`^ghcr\\.io/volcengine/openviking:${release.openVikingVersion.replaceAll(".", "\\.")}@sha256:[a-f0-9]{64}$`, "u")
    .test(release.openVikingImage), "OpenViking release image must be pinned to a digest");
  assert(Object.keys(release.platformDigests).sort().join(",") === "linux/amd64,linux/arm64",
    "OpenViking platform matrix is incomplete");
  assert(Object.values(release.platformDigests).every(value => /^sha256:[a-f0-9]{64}$/u.test(value)),
    "invalid OpenViking platform digest");
  assert(/^ghcr\.io\/ggml-org\/llama\.cpp:server@sha256:[a-f0-9]{64}$/u.test(release.embedding?.image),
    "embedding image must be pinned to a digest");
  assert(Object.keys(release.embedding.platformDigests).sort().join(",") === "linux/amd64,linux/arm64" &&
    Object.values(release.embedding.platformDigests).every(value => /^sha256:[a-f0-9]{64}$/u.test(value)),
  "embedding image platform matrix is incomplete");
  assert(/^[A-Za-z0-9._-]+\.gguf$/u.test(release.embedding.modelFile) &&
    /^[a-f0-9]{64}$/u.test(release.embedding.modelSha256) &&
    Number.isSafeInteger(release.embedding.dimension) && release.embedding.dimension > 0,
  "embedding model manifest is incomplete");
  assert(release.reranker?.image === release.embedding.image &&
    JSON.stringify(release.reranker.platformDigests) === JSON.stringify(release.embedding.platformDigests) &&
    /^[A-Za-z0-9._-]+\.gguf$/u.test(release.reranker.modelFile) &&
    /^[a-f0-9]{64}$/u.test(release.reranker.modelSha256) &&
    typeof release.reranker.modelName === "string" && release.reranker.modelName.length > 0,
  "reranker manifest is incomplete");

  if (deployment) {
    assert(process.env.DANO_OPENVIKING_IMAGE === release.openVikingImage,
      "deployment OpenViking image differs from memory release");
    assert(process.env.DANO_EMBEDDING_IMAGE === release.embedding.image &&
      process.env.DANO_EMBEDDING_MODEL_FILE === release.embedding.modelFile &&
      process.env.DANO_EMBEDDING_MODEL_NAME === release.embedding.modelName,
    "deployment embedding image or model differs from memory release");
    assert(process.env.DANO_RERANKER_IMAGE === release.reranker.image &&
      process.env.DANO_RERANKER_MODEL_FILE === release.reranker.modelFile &&
      process.env.DANO_RERANKER_MODEL_NAME === release.reranker.modelName,
    "deployment reranker image or model differs from memory release");
    const configDirectory = process.env.DANO_OPENVIKING_CONFIG_DIR;
    const modelsDirectory = process.env.DANO_OPENVIKING_MODELS_DIR;
    const recoveryVolume = process.env.DANO_MEMORY_RECOVERY_VOLUME;
    assert(configDirectory && modelsDirectory && process.env.DANO_OPENVIKING_DATA_VOLUME && recoveryVolume,
      "OpenViking private config, models, data and separate recovery volume are required");
    assert(recoveryVolume !== process.env.DANO_OPENVIKING_DATA_VOLUME
      && recoveryVolume !== process.env.DANO_PROTECTED_DATA_VOLUME
      && recoveryVolume !== process.env.DANO_PROTECTED_CONFIG_VOLUME,
    "memory recovery volume must be separate from restored data and config volumes");
    const canonicalConfigDirectory = await privatePath(configDirectory, true);
    const configPath = join(canonicalConfigDirectory, "ov.conf");
    await privatePath(configPath, false);
    const config = await json(configPath);
    assert(config.server?.host === "0.0.0.0" && config.server?.port === 1933 &&
      typeof config.server?.root_api_key === "string" && config.server.root_api_key.length >= 32,
    "OpenViking server config is incomplete");
    assert(config.storage?.workspace === "/app/.openviking/data",
      "OpenViking workspace must be inside the persistent data volume");
    assert(typeof config.vlm?.provider === "string" && typeof config.vlm?.model === "string" &&
      typeof config.vlm?.api_key === "string" && config.vlm.api_key.length > 0,
    "OpenViking extraction model config is incomplete");
    assert(config.embedding?.dense?.provider === "openai" &&
      config.embedding.dense.model === release.embedding.modelName &&
      config.embedding.dense.dimension === release.embedding.dimension &&
      config.embedding.dense.api_base === "http://embedding:8080/v1" &&
      config.embedding.dense.encoding_format === "float",
    "OpenViking embedding service config differs from memory release");
    const modelRoot = await realpath(modelsDirectory);
    const actual = await realpath(join(modelRoot, release.embedding.modelFile));
    assert(actual.startsWith(`${modelRoot}${sep}`) && (await lstat(actual)).isFile(),
      "embedding model is missing or outside the model mount");
    assert(await sha256(actual) === release.embedding.modelSha256,
      "embedding model hash differs from memory release");
    const reranker = await realpath(join(modelRoot, release.reranker.modelFile));
    assert(reranker.startsWith(`${modelRoot}${sep}`) && (await lstat(reranker)).isFile(),
      "reranker model is missing or outside the model mount");
    assert(await sha256(reranker) === release.reranker.modelSha256,
      "reranker model hash differs from memory release");
  }
  process.stdout.write(`memory release check passed: Dano ${release.danoVersion}, ` +
    `pi-openviking ${release.piOpenVikingVersion}, OpenViking ${release.openVikingVersion}, ` +
    `embedding ${release.embedding.imageVersion}, reranker ${release.reranker.imageVersion}\n`);
} catch (error) {
  process.stderr.write(`memory release check failed: ${error instanceof Error ? error.message : "unknown error"}\n`);
  process.exitCode = 1;
}
