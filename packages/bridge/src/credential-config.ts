import * as fs from "node:fs";
import * as path from "node:path";

const SECRET_KEY_PATTERN = /(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)/i;

export interface ServerCredentialConfig {
  loadedEnvFile?: string;
  loadedSecretFiles: string[];
  credentialKeys: string[];
}

export interface LoadServerCredentialConfigOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  envFile?: string;
}

function parseEnvValue(rawValue: string): string {
  const trimmed = rawValue.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function loadEnvFile(
  envFile: string,
  env: NodeJS.ProcessEnv,
): string | undefined {
  if (!fs.existsSync(envFile)) {
    return undefined;
  }

  const contents = fs.readFileSync(envFile, "utf8");
  for (const rawLine of contents.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }

    const match = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line);
    if (!match) {
      continue;
    }

    const [, key, rawValue] = match;
    if (!key || env[key] !== undefined) {
      continue;
    }

    env[key] = parseEnvValue(rawValue ?? "");
  }

  return envFile;
}

function loadDockerSecretFiles(env: NodeJS.ProcessEnv): string[] {
  const loaded: string[] = [];

  for (const [key, value] of Object.entries(env)) {
    if (!key.endsWith("_FILE") || !value) {
      continue;
    }

    const targetKey = key.slice(0, -"_FILE".length);
    if (!targetKey || env[targetKey]) {
      continue;
    }

    const filePath = value.trim();
    if (!filePath) {
      continue;
    }

    try {
      env[targetKey] = fs.readFileSync(filePath, "utf8").trim();
      loaded.push(filePath);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[dano] Could not read Docker secret file for ${targetKey}: ${message}`);
    }
  }

  return loaded;
}

export function loadServerCredentialConfig(
  options: LoadServerCredentialConfigOptions = {},
): ServerCredentialConfig {
  const cwd = options.cwd ?? process.cwd();
  const env = options.env ?? process.env;
  const envFile = options.envFile ?? path.join(cwd, ".env");
  const loadedEnvFile = loadEnvFile(envFile, env);
  const loadedSecretFiles = loadDockerSecretFiles(env);
  const credentialKeys = Object.keys(env)
    .filter(key => SECRET_KEY_PATTERN.test(key))
    .filter(key => !key.endsWith("_FILE"))
    .filter(key => Boolean(env[key]))
    .sort();

  return {
    ...(loadedEnvFile ? { loadedEnvFile } : {}),
    loadedSecretFiles,
    credentialKeys,
  };
}
