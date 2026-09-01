import path from "node:path";

export interface StudioConfig {
  rootDir: string;
  dataDir: string;
  recordingsDir: string;
  catalogDir: string;
  profileDir: string;
  maxResponseBytes: number;
  headless: boolean;
  openaiModel: string;
}

function boolEnv(name: string, fallback: boolean) {
  const raw = process.env[name];
  if (raw == null) return fallback;
  return ["1", "true", "yes", "on"].includes(raw.toLowerCase());
}

export function loadConfig(cwd = process.cwd()): StudioConfig {
  const dataDir = path.resolve(cwd, ".business-skill-studio");
  return {
    rootDir: cwd,
    dataDir,
    recordingsDir: path.join(dataDir, "recordings"),
    catalogDir: path.join(dataDir, "catalog"),
    profileDir: path.resolve(cwd, process.env.BSS_PROFILE_DIR || ".business-skill-studio/browser-profile"),
    maxResponseBytes: Number(process.env.BSS_MAX_RESPONSE_BYTES || 262_144),
    headless: boolEnv("BSS_HEADLESS", false),
    openaiModel: process.env.OPENAI_MODEL || "gpt-5.5"
  };
}
