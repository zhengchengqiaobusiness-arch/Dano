import { createHash, createHmac, hkdfSync, timingSafeEqual } from "node:crypto";
import type { StateStore, TaskFactPolicy, TaskFactProjector } from "@josephyoung/pi-openviking/host";
import type { ProviderRequest, ProviderResponse } from "./credential-broker.js";
import { oauthUserId } from "./oauth-user-id.js";

type Scalar = string | number | boolean;
export interface TaskFactContract {
  id: string;
  method: string;
  path: string;
  success: { path: string[]; equals: Scalar };
  actorPath: string[];
  fields: { label: string; path: string[]; type: "string" | "number" | "boolean" }[];
}
export interface MemoryTaskFactConfig {
  maxResponseBytes: number;
  maxFactBytes: number;
  contracts: TaskFactContract[];
}
export interface ProviderTaskFactInput {
  toolName: "bash" | "provider_request";
  toolCallId: string;
  request: ProviderRequest;
  response: ProviderResponse;
  loginSessionBound: boolean;
  signal?: AbortSignal;
}
/** Opaque signed receipt. Tool wrappers never accept a caller-supplied receipt. */
export interface ProviderTaskFactReceipt { data: string; signature: string }
export type CaptureProviderTaskFact = (input: ProviderTaskFactInput) => Promise<ProviderTaskFactReceipt | undefined>;

const invalid = () => new Error("INVALID_MEMORY_TASK_FACT_CONFIG");
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(k => !keys.includes(k))) throw invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== "string" || !value.trim() || value.length > 512 || /[\x00-\x1f\x7f]/.test(value)) throw invalid();
  return value;
}
function scalar(value: unknown): value is Scalar {
  return typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value)) || typeof value === "string";
}
function fieldPath(value: unknown): string[] {
  if (!Array.isArray(value) || !value.length || value.length > 16) throw invalid();
  return value.map(part => {
    const name = text(part);
    if (["__proto__", "prototype", "constructor"].includes(name)) throw invalid();
    return name;
  });
}
export function parseMemoryTaskFactConfig(value: unknown): MemoryTaskFactConfig {
  const raw = object(value, ["maxResponseBytes", "maxFactBytes", "contracts"]);
  for (const key of ["maxResponseBytes", "maxFactBytes"] as const) {
    if (typeof raw[key] !== "number" || !Number.isSafeInteger(raw[key]) || raw[key] <= 0) throw invalid();
  }
  if (!Array.isArray(raw.contracts) || !raw.contracts.length || raw.contracts.length > 128) throw invalid();
  const ids = new Set<string>(), routes = new Set<string>();
  const contracts = raw.contracts.map(value => {
    const rule = object(value, ["id", "method", "path", "success", "actorPath", "fields"]);
    const id = text(rule.id), method = text(rule.method), path = text(rule.path);
    if (!/^[A-Za-z0-9_-]+$/.test(id) || !/^[A-Z]+$/.test(method)
      || !path.startsWith("/") || path.startsWith("//") || /[?#\\%]/.test(path)
      || new URL(path, "https://contract.invalid").pathname !== path || ids.has(id) || routes.has(`${method} ${path}`)) throw invalid();
    ids.add(id); routes.add(`${method} ${path}`);
    const success = object(rule.success, ["path", "equals"]);
    if (!scalar(success.equals) || (typeof success.equals === "string" && success.equals.length > 512)) throw invalid();
    if (!Array.isArray(rule.fields) || !rule.fields.length || rule.fields.length > 32) throw invalid();
    const labels = new Set<string>();
    const fields = rule.fields.map(value => {
      const field = object(value, ["label", "path", "type"]), label = text(field.label);
      if (labels.has(label) || !["string", "number", "boolean"].includes(String(field.type))) throw invalid();
      labels.add(label);
      return { label, path: fieldPath(field.path), type: field.type as "string" | "number" | "boolean" };
    });
    return { id, method, path, success: { path: fieldPath(success.path), equals: success.equals },
      actorPath: fieldPath(rule.actorPath), fields };
  });
  return { maxResponseBytes: raw.maxResponseBytes as number, maxFactBytes: raw.maxFactBytes as number, contracts };
}
function valueAt(input: unknown, path: readonly string[]): unknown {
  let value = input;
  for (const part of path) {
    if (!value || typeof value !== "object" || !Object.hasOwn(value, part)) return undefined;
    value = (value as Record<string, unknown>)[part];
  }
  return value;
}

/** Only the authenticated host sees provider bodies. Persist a minimal receipt,
 * signed with a domain-separated host key, so worker details cannot forge it. */
export class MemoryTaskFacts {
  readonly #config: MemoryTaskFactConfig;
  readonly #key: Buffer;
  readonly #ownerDigest: string;
  #closed = false;
  constructor(private readonly options: { config: MemoryTaskFactConfig; key: Uint8Array;
    store: StateStore; userId: string; policyVersion: string; timeoutMs: number }) {
    this.#config = parseMemoryTaskFactConfig(options.config);
    if (options.key.length < 32 || !Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0) throw invalid();
    this.#ownerDigest = createHash("sha256").update(JSON.stringify([options.store.owner.accountId, options.store.owner.userId])).digest("hex");
    this.#key = Buffer.from(hkdfSync("sha256", options.key, this.#ownerDigest, "dano-provider-task-facts-v1", 32));
  }
  close(): void { this.#closed = true; this.#key.fill(0); }
  async capture(input: ProviderTaskFactInput): Promise<ProviderTaskFactReceipt | undefined> {
    try {
      const signal = AbortSignal.any([AbortSignal.timeout(this.options.timeoutMs), ...(input.signal ? [input.signal] : [])]);
      signal.throwIfAborted();
      if (this.#closed || !input.loginSessionBound || !input.response.ok || !input.toolCallId
        || !["bash", "provider_request"].includes(input.toolName)
        || input.response.status < 200 || input.response.status >= 300
        || Buffer.byteLength(input.response.body) > this.#config.maxResponseBytes) return;
      // The broker accepts only the configured provider origin. Never match an
      // absolute/model-selected origin or an ambiguous encoded route here.
      if (!input.request.path.startsWith("/") || input.request.path.startsWith("//") || /[\\%]/.test(input.request.path)) return;
      const path = new URL(input.request.path, "https://contract.invalid").pathname;
      const contract = this.#config.contracts.find(rule => rule.method === input.request.method.trim().toUpperCase() && rule.path === path);
      if (!contract) return;
      const body: unknown = JSON.parse(input.response.body);
      if (valueAt(body, contract.success.path) !== contract.success.equals) return;
      const actor = valueAt(body, contract.actorPath);
      if ((typeof actor !== "string" && !(typeof actor === "number" && Number.isSafeInteger(actor)))
        || oauthUserId(String(actor)) !== this.options.userId) return;
      const fields: { label: string; value: Scalar }[] = [];
      for (const field of contract.fields) {
        const value = valueAt(body, field.path);
        if (!scalar(value) || typeof value !== field.type || (typeof value === "string" && !value.trim())) return;
        fields.push({ label: field.label, value });
      }
      const fact = JSON.stringify({ task: contract.id, fields });
      if (Buffer.byteLength(fact) > this.#config.maxFactBytes) return;
      const state = await this.options.store.read(signal), consent = state.authorization.collectionConsent;
      signal.throwIfAborted();
      if (this.#closed || !state.authorization.enabled || !state.authorization.automaticCollection
        || consent?.policyVersion !== this.options.policyVersion) return;
      const data = JSON.stringify({ version: 1, ownerDigest: this.#ownerDigest, toolName: input.toolName,
        toolCallId: input.toolCallId, policyVersion: this.options.policyVersion,
        epoch: state.authorization.epoch, revision: consent.revision, scope: consent.scope, fact });
      return { data, signature: createHmac("sha256", this.#key).update(data).digest("hex") };
    } catch { return undefined; } // Optional memory must not fail the business request.
  }
  policy(): TaskFactPolicy {
    const project: TaskFactProjector = async (result, context) => {
      if (this.#closed || result.isError !== false) return;
      const receipts = (result.details as { danoTaskFacts?: unknown } | undefined)?.danoTaskFacts;
      if (!Array.isArray(receipts) || receipts.length > 128) return;
      const state = await this.options.store.read(context.signal), consent = state.authorization.collectionConsent;
      if (!state.authorization.enabled || !state.authorization.automaticCollection
        || consent?.policyVersion !== this.options.policyVersion || context.scope !== consent.scope
        || createHash("sha256").update(JSON.stringify([context.owner.accountId, context.owner.userId])).digest("hex") !== this.#ownerDigest) return;
      const facts = new Set<string>();
      for (const receipt of receipts) {
        context.signal?.throwIfAborted();
        if (this.#closed) return;
        if (!receipt || typeof receipt.data !== "string" || typeof receipt.signature !== "string"
          || !/^[a-f0-9]{64}$/.test(receipt.signature)
          || Buffer.byteLength(receipt.data) > this.#config.maxFactBytes + 4096) continue;
        const signature = createHmac("sha256", this.#key).update(receipt.data).digest();
        if (!timingSafeEqual(signature, Buffer.from(receipt.signature, "hex"))) continue;
        const data = JSON.parse(receipt.data);
        if (data.version !== 1 || data.ownerDigest !== this.#ownerDigest || data.toolName !== result.toolName
          || data.toolCallId !== result.toolCallId || data.policyVersion !== this.options.policyVersion
          || data.epoch !== state.authorization.epoch || data.revision !== consent.revision || data.scope !== context.scope
          || typeof data.fact !== "string") continue;
        facts.add(data.fact);
      }
      const text = [...facts].join("\n");
      return text && Buffer.byteLength(text) <= this.#config.maxFactBytes ? text : undefined;
    };
    return { policyVersion: this.options.policyVersion, tools: new Map([["bash", project], ["provider_request", project]]) };
  }
}
