import { accessSync, constants, statSync } from "node:fs";
import { resolve } from "node:path";

const exposureModes = new Set([
  "http",
  "https",
  "both",
  "both-no-redirect-http",
]);

function requireReadablePath(name, value, baseDir) {
  if (!value?.trim()) {
    throw new Error(`${name} is required for TLS exposure modes`);
  }

  const path = resolve(baseDir, value);
  try {
    accessSync(path, constants.R_OK);
    if (!statSync(path).isFile()) throw new Error("not a file");
  } catch {
    throw new Error(`${name} must point to a readable file: ${path}`);
  }
  return path;
}

export function resolveDeploymentExposure(
  env,
  { baseDir = process.cwd(), validateTls = true } = {},
) {
  const mode = env.DANO_EXPOSURE_MODE?.trim() || "http";
  if (!exposureModes.has(mode)) {
    throw new Error(
      `DANO_EXPOSURE_MODE must be one of: ${[...exposureModes].join(", ")}`,
    );
  }

  if (mode === "http") {
    return { mode, tlsEnv: {} };
  }

  if (!validateTls) {
    return { mode, tlsEnv: {} };
  }

  return {
    mode,
    tlsEnv: {
      DANO_TLS_CERT_PATH: requireReadablePath(
        "DANO_TLS_CERT_PATH",
        env.DANO_TLS_CERT_PATH,
        baseDir,
      ),
      DANO_TLS_KEY_PATH: requireReadablePath(
        "DANO_TLS_KEY_PATH",
        env.DANO_TLS_KEY_PATH,
        baseDir,
      ),
    },
  };
}
