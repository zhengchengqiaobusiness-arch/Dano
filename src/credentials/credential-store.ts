import crypto from "node:crypto";
import path from "node:path";
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { defaultSkillCredentialRoot } from "../config.js";
import type { CapabilityContract, EvidenceEvent } from "../domain.js";
import { isSecretBearingHeader } from "../security/redact.js";

interface OriginCredentialProfile {
  version: 1;
  origin: string;
  headers: Record<string, string>;
  updatedAt: string;
}

interface SkillCredentialProfile {
  version: 1;
  skill: string;
  origins: Record<string, Record<string, string>>;
  updatedAt: string;
}

const AUTH_CONTEXT_HEADER = /^(?:x-)?(?:tenant|organization|org)-?id$/i;
const NON_RUNTIME_HEADER = /^(?:proxy-authorization|set-cookie)$/i;
const writeQueues = new Map<string, Promise<void>>();

function isCredentialHeader(name: string) {
  return !NON_RUNTIME_HEADER.test(name) && (isSecretBearingHeader(name) || AUTH_CONTEXT_HEADER.test(name));
}

function originOf(rawUrl: string) {
  const url = new URL(rawUrl);
  if (!/^https?:$/.test(url.protocol)) throw new Error("只支持保存 HTTP(S) 业务系统凭据");
  return url.origin;
}

export function credentialHeaders(headers: Record<string, string>) {
  return Object.fromEntries(
    Object.entries(headers)
      .filter(([name, value]) => Boolean(value) && value !== "[REDACTED]" && isCredentialHeader(name))
      .map(([name, value]) => [name.toLowerCase(), value])
  );
}

function originCredentialFile(dataDir: string, origin: string) {
  const key = crypto.createHash("sha256").update(origin).digest("hex");
  return path.join(dataDir, "credentials", "origins", `${key}.json`);
}

async function readProfile<T>(file: string): Promise<T | undefined> {
  try {
    return JSON.parse(await readFile(file, "utf8")) as T;
  } catch (error: any) {
    if (error?.code === "ENOENT") return undefined;
    throw error;
  }
}

async function writePrivateJson(file: string, value: unknown) {
  const directory = path.dirname(file);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await chmod(directory, 0o700).catch(() => {});
  await writeFile(file, JSON.stringify(value, null, 2) + "\n", { encoding: "utf8", mode: 0o600 });
  await chmod(file, 0o600).catch(() => {});
}

async function serializedWrite(file: string, work: () => Promise<void>) {
  const previous = writeQueues.get(file) || Promise.resolve();
  const current = previous.catch(() => {}).then(work);
  writeQueues.set(file, current);
  try {
    await current;
  } finally {
    if (writeQueues.get(file) === current) writeQueues.delete(file);
  }
}

export async function persistOriginCredentials(dataDir: string, rawUrl: string, rawHeaders: Record<string, string>) {
  const headers = credentialHeaders(rawHeaders);
  if (!Object.keys(headers).length) return undefined;
  const origin = originOf(rawUrl);
  const file = originCredentialFile(dataDir, origin);
  await serializedWrite(file, async () => {
    const previous = await readProfile<OriginCredentialProfile>(file);
    await writePrivateJson(file, {
      version: 1,
      origin,
      headers: { ...(previous?.origin === origin ? previous.headers : {}), ...headers },
      updatedAt: new Date().toISOString()
    } satisfies OriginCredentialProfile);
  });
  return file;
}

export function skillCredentialFile(outputRoot: string, skillName: string) {
  return path.join(defaultSkillCredentialRoot(outputRoot), `${skillName}.json`);
}

export function requiredCredentialOrigins(capabilities: CapabilityContract[], events: EvidenceEvent[]) {
  const evidenceIds = new Set(capabilities.flatMap(capability => capability.evidence.map(ref => ref.eventId)));
  const origins = new Set<string>();
  for (const event of events) {
    if (event.kind !== "network" || !evidenceIds.has(event.id)) continue;
    if (!Object.keys(event.request.headers || {}).some(isCredentialHeader)) continue;
    origins.add(originOf(event.request.url));
  }
  return [...origins];
}

export async function materializeSkillCredentials(
  dataDir: string,
  outputRoot: string,
  skillName: string,
  origins: string[],
  requiredOrigins: string[] = []
) {
  const selected: SkillCredentialProfile["origins"] = {};
  for (const rawOrigin of [...new Set(origins)]) {
    const origin = originOf(rawOrigin);
    const profile = await readProfile<OriginCredentialProfile>(originCredentialFile(dataDir, origin));
    if (profile?.origin === origin && Object.keys(profile.headers).length) selected[origin] = profile.headers;
  }
  const missing = [...new Set(requiredOrigins.map(originOf))].filter(origin => !selected[origin]);
  if (missing.length) {
    throw new Error(`已录制请求使用登录凭据，但没有保存以下业务系统的运行时凭据：${missing.join("、")}。请保持登录后重新录制一次该请求。`);
  }
  if (!Object.keys(selected).length) return undefined;
  const file = skillCredentialFile(outputRoot, skillName);
  await writePrivateJson(file, {
    version: 1,
    skill: skillName,
    origins: selected,
    updatedAt: new Date().toISOString()
  } satisfies SkillCredentialProfile);
  return file;
}
