import { constants } from "node:fs";
import { lstat, open, realpath } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";
import type { UserMemoryServices } from "./user-memory-runtime.js";
import type { MemoryTokenizerBinding, MemoryTokenizerLimits } from "./memory-tokenizer.js";

/** Host-private file contents. Never put this object in argv, RPC, logs or browser state. */
export interface MemoryHostConfig {
  version: 1;
  baseUrl: string;
  accountId: string;
  managementKey: string;
  encryptionKey: string;
  encryptionKeyVersion: string;
  requestTimeoutMs: number;
  shutdownTimeoutMs: number;
  maxContentBytes: number;
  policyVersion: string;
  policy: Omit<UserMemoryServices["policy"], "countTokens">;
  scheduler: Omit<UserMemoryServices["scheduler"], "onStatus" | "onError">;
  tokenizerLimits: MemoryTokenizerLimits;
  tokenizers: MemoryTokenizerBinding[];
}
const invalid = () => new Error("INVALID_MEMORY_HOST_CONFIG");
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !keys.includes(key))) throw invalid();
  return value as Record<string, unknown>;
}
function positive(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) throw invalid();
  return value;
}
function text(value: unknown): string {
  if (typeof value !== "string" || !value || value.length > 16384 || /[\x00-\x1f\x7f]/.test(value)) throw invalid();
  return value;
}
function identifier(value: unknown): string {
  const result = text(value);
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(result)) throw invalid();
  return result;
}
function asset(value: unknown) {
  const raw = object(value, ["path", "sha256"]);
  const path = text(raw.path), sha256 = text(raw.sha256);
  if (!isAbsolute(path) || resolve(path) !== path || !/^[a-f0-9]{64}$/.test(sha256)) throw invalid();
  return { path, sha256 };
}

export function parseMemoryHostConfig(input: unknown): MemoryHostConfig {
  try {
    const raw = object(input, ["version", "baseUrl", "accountId", "managementKey", "encryptionKey", "encryptionKeyVersion",
      "requestTimeoutMs", "shutdownTimeoutMs", "maxContentBytes", "policyVersion", "policy", "scheduler", "tokenizerLimits", "tokenizers"]);
    const url = new URL(text(raw.baseUrl));
    const encryptionKey = text(raw.encryptionKey), managementKey = text(raw.managementKey);
    if (raw.version !== 1 || !["http:", "https:"].includes(url.protocol) || url.username || url.password
      || url.pathname !== "/" || url.search || url.hash || !/^[a-fA-F0-9]{64}$/.test(encryptionKey) || /\s/.test(managementKey)) throw invalid();
    const policy = object(raw.policy, ["maxPayloadBytes", "recallTimeoutMs", "recallTokenBudget", "recallLimit", "minimumScore"]);
    if (typeof policy.minimumScore !== "number" || !Number.isFinite(policy.minimumScore)) throw invalid();
    const scheduler = object(raw.scheduler, ["pollIntervalMs", "initialBackoffMs", "maxBackoffMs", "maxAttemptsPerPhase", "maxOperationsPerTick"]);
    const initialBackoffMs = positive(scheduler.initialBackoffMs), maxBackoffMs = positive(scheduler.maxBackoffMs);
    if (initialBackoffMs > maxBackoffMs) throw invalid();
    const limits = object(raw.tokenizerLimits, ["maxAssetBytes", "maxInputBytes", "startupTimeoutMs"]);
    if (!Array.isArray(raw.tokenizers) || !raw.tokenizers.length) throw invalid();
    const models = new Set<string>();
    const tokenizers = raw.tokenizers.map(value => {
      const binding = object(value, ["model", "tokenizer", "config"]);
      const model = object(binding.model, ["provider", "api", "id"]);
      const boundModel = { provider: text(model.provider), api: text(model.api), id: text(model.id) };
      const key = JSON.stringify([boundModel.provider, boundModel.api, boundModel.id]);
      if (models.has(key)) throw invalid();
      models.add(key);
      return { model: boundModel, tokenizer: asset(binding.tokenizer), config: asset(binding.config) };
    });
    return { version: 1, baseUrl: url.origin, accountId: identifier(raw.accountId), managementKey, encryptionKey,
      encryptionKeyVersion: identifier(raw.encryptionKeyVersion), policyVersion: text(raw.policyVersion),
      requestTimeoutMs: positive(raw.requestTimeoutMs), shutdownTimeoutMs: positive(raw.shutdownTimeoutMs),
      maxContentBytes: positive(raw.maxContentBytes),
      policy: { maxPayloadBytes: positive(policy.maxPayloadBytes), recallTimeoutMs: positive(policy.recallTimeoutMs),
        recallTokenBudget: positive(policy.recallTokenBudget), recallLimit: positive(policy.recallLimit), minimumScore: policy.minimumScore },
      scheduler: { pollIntervalMs: positive(scheduler.pollIntervalMs), initialBackoffMs, maxBackoffMs,
        maxAttemptsPerPhase: positive(scheduler.maxAttemptsPerPhase), maxOperationsPerTick: positive(scheduler.maxOperationsPerTick) },
      tokenizerLimits: { maxAssetBytes: positive(limits.maxAssetBytes), maxInputBytes: positive(limits.maxInputBytes),
        startupTimeoutMs: positive(limits.startupTimeoutMs) }, tokenizers };
  } catch { throw invalid(); }
}

/** Separate host-owned 0700 configuration directory, outside state and tool workspaces. */
export async function readMemoryHostConfig(privateConfigDirectory: string): Promise<MemoryHostConfig | undefined> {
  try {
    if (!isAbsolute(privateConfigDirectory) || await realpath(privateConfigDirectory) !== privateConfigDirectory) throw invalid();
    const directory = await lstat(privateConfigDirectory);
    if (!directory.isDirectory() || directory.uid !== process.getuid?.() || (directory.mode & 0o077)) throw invalid();
    let file;
    try { file = await open(join(privateConfigDirectory, "memory-service.json"), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined; throw error; }
    try {
      const stat = await file.stat();
      if (!stat.isFile() || stat.uid !== directory.uid || stat.nlink !== 1 || (stat.mode & 0o077) || stat.size > 1024 * 1024) throw invalid();
      const bytes = await file.readFile();
      if (bytes.length > 1024 * 1024) throw invalid();
      return parseMemoryHostConfig(JSON.parse(bytes.toString("utf8")));
    } finally { await file.close(); }
  } catch { throw invalid(); }
}
