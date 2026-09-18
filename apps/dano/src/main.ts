import {
  constants,
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, join, resolve, sep } from "node:path";
import { isIP } from "node:net";
import {
  checkServerIdentity,
  connect as connectTls,
} from "node:tls";
import { fileURLToPath } from "node:url";
import {
  resolveProductName,
  syncSystemPrompt,
} from "../runtime/system-prompt.mjs";
import {
  loadDanoConfig,
  type DanoConfig,
} from "./bridge/dano-config.js";
import { createAnonymousUserContextResolver } from "./bridge/anonymous-user-context.js";
import { CredentialBroker } from "./bridge/credential-broker.js";
import { createOAuthAuthentication } from "./bridge/oauth-authentication.js";
import {
  createOAuth2ProviderAdapter,
  type OAuth2ProviderAdapterOptions,
} from "./bridge/oauth-provider.js";
import { createJwtUserContextResolver } from "./bridge/user-context.js";
import type { BridgeConfig, UploadConfig } from "./bridge/types.js";
import { createDanoDevReloadController } from "./dev-reload.js";
import { loadDanoRuntime, type DanoRuntime } from "./runtime.js";
import type { StartDanoServerOptions } from "./server.js";
import type { BridgeEmptyStateConfig } from "../types/protocol.js";

const DEFAULT_DANO_PORT = 8080;
const DEFAULT_DANO_HOST = "0.0.0.0";
const DEFAULT_DANO_RUNTIME_DIR = "/opt/dano/runtime-data";
const DEFAULT_DANO_SESSIONS_DIR = ".dano/sessions";
const DEFAULT_DANO_UPLOAD_DIR = ".dano/uploads";
const DEFAULT_DANO_UPLOAD_MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024;
const DEFAULT_DANO_UPLOAD_DRAFT_TTL_MS = 2 * 60 * 60 * 1000;
const DEFAULT_DANO_UPLOAD_REFERENCED_TTL_MS = 24 * 60 * 60 * 1000;
const DEFAULT_DANO_UPLOAD_ORPHANED_TTL_MS = 5 * 60 * 1000;
const DEFAULT_DANO_UPLOAD_CLEANUP_INTERVAL_MS = 60 * 60 * 1000;
const DEFAULT_DANO_ANONYMOUS_IDLE_TTL_MS = 24 * 60 * 60 * 1000;
const DEFAULT_DANO_ANONYMOUS_CLEANUP_INTERVAL_MS = 60 * 60 * 1000;
const DEFAULT_DANO_LOGIN_SESSION_IDLE_TTL_MS = 8 * 60 * 60 * 1000;
const DEFAULT_DANO_LOGIN_SESSION_ABSOLUTE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const DEFAULT_DANO_LOGIN_SESSION_CLEANUP_INTERVAL_MS = 60 * 60 * 1000;
const DEFAULT_RUNTIME_SETTINGS_FILES = [
  "SYSTEM.md",
  "settings.json",
  "heimdall.json",
] as const;
const DANO_HEIMDALL_SANDBOX_ENV_ALLOW = [
  "PATH",
  "HOME",
  "SHELL",
  "USER",
  "LOGNAME",
  "LANG",
  "LC_*",
  "TMPDIR",
  "DANO_URL",
  "DANO_TENANT_KEY",
] as const;
const DEFAULT_EMPTY_STATE: BridgeEmptyStateConfig = {
  mode: "text",
  content: "给{产品名称}发消息",
};

interface DanoPackageInfo {
  name: string;
  version: string;
}

export interface DanoServerOptions {
  cwd: string;
  runtimeRootPath: string;
  host: string;
  port: number;
  defaultWorkspacePath: string;
  agentConfigDir: string;
  sessionsRootPath: string;
  productName: string;
  emptyState: BridgeEmptyStateConfig;
  upload: UploadConfig;
  anonymousUserCleanup: {
    idleTtlMs: number;
    intervalMs: number;
  };
  guestCookieSecure: boolean;
  staticDir?: string;
  help: boolean;
  validateConfig: boolean;
  userAuthentication?: {
    secret: string;
    issuer?: string;
    audience?: string;
    cookieName?: string;
  };
  oauthAuthentication?: {
    appOrigin: string;
    redirectUri: string;
    providerApiOrigin: string;
    allowInsecureProviderApiOrigin?: boolean;
    provider: Omit<
      OAuth2ProviderAdapterOptions,
      "allowInsecureRequests" | "timeoutMs"
    >;
    credentialEncryptionKey: {
      version: string;
      key: Uint8Array;
    };
    session: {
      idleTtlMs: number;
      absoluteTtlMs: number;
      cleanupIntervalMs: number;
    };
  };
}

type OAuthAuthenticationConfig = NonNullable<
  DanoServerOptions["oauthAuthentication"]
>;
export type OAuthProviderTlsProbe = (endpoint: URL) => Promise<void>;

function printHelp(): void {
  console.log(`Dano server

Usage:
  node dist/server/main.js [--host <host>] [--port <number>] [--default-workspace <path>]

Options:
  --host <host>              Host to bind (default: ${DEFAULT_DANO_HOST})
  --port <number>            Port to bind (default: ${DEFAULT_DANO_PORT})
  --default-workspace <path> Workspace path checked before startup (default: DANO_RUNTIME_DIR/workspaces/ws_<random>)
  --sessions-root <path>     Base directory for per-User session roots (env: DANO_SESSIONS_ROOT, default: DANO_RUNTIME_DIR/${DEFAULT_DANO_SESSIONS_DIR})
  --empty-state-text <text>  Empty transcript text (env: DANO_EMPTY_STATE_TEXT, default: ${DEFAULT_EMPTY_STATE.content})
  --empty-state-html <html>  Empty transcript HTML (env: DANO_EMPTY_STATE_HTML)
  --validate-config          Validate production configuration without listening
  --help                     Show this help
`);
}

function parseInteger(value: string | undefined, fallback: number): number {
  if (!value) {
    return fallback;
  }

  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parsePositiveInteger(
  value: string | undefined,
  fallback: number,
): number {
  const parsed = parseInteger(value, fallback);
  return parsed > 0 ? parsed : fallback;
}

function parseConfiguredPositiveInteger(
  env: Record<string, string | undefined>,
  name: string,
  fallback: number,
): number {
  const configured = env[name]?.trim();
  if (!configured) return fallback;
  if (!/^\d+$/.test(configured)) {
    throw new Error(`${name} must be a positive integer`);
  }
  const value = Number(configured);
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

function readHost(env: Record<string, string | undefined>): string {
  return env.DANO_HOST?.trim() || env.HOST?.trim() || DEFAULT_DANO_HOST;
}

function readPort(env: Record<string, string | undefined>): number {
  return parseInteger(
    env.DANO_PORT?.trim() || env.PORT?.trim(),
    DEFAULT_DANO_PORT,
  );
}

function readRuntimeRootPath(env: Record<string, string | undefined>): string {
  return env.DANO_RUNTIME_DIR?.trim() || DEFAULT_DANO_RUNTIME_DIR;
}

function readDefaultWorkspacePath(runtimeRootPath: string): string {
  return join(runtimeRootPath, "workspaces", `ws_${randomUUID()}`);
}

function assertWritableDirectory(directoryPath: string): void {
  const probePath = join(directoryPath, `.dano-write-test-${randomUUID()}`);
  let probeCreated = false;
  try {
    mkdirSync(directoryPath, { recursive: true, mode: 0o700 });
    writeFileSync(probePath, "dano", { flag: "wx", mode: 0o600 });
    probeCreated = true;
    unlinkSync(probePath);
    probeCreated = false;
  } catch (error) {
    if (probeCreated) {
      try {
        unlinkSync(probePath);
      } catch {
        // Preserve the original persistence failure.
      }
    }
    throw new Error(
      `Dano needs write access for workspace session files at ${directoryPath}`,
      { cause: error },
    );
  }
}

export function assertRuntimePersistenceWritable(options: {
  defaultWorkspacePath: string;
  runtimeRootPath: string;
  sessionsRootPath: string;
}): void {
  assertWritableDirectory(join(options.runtimeRootPath, "users"));
  assertWritableDirectory(options.defaultWorkspacePath);
  assertWritableDirectory(options.sessionsRootPath);
}

function readAgentConfigDir(
  env: Record<string, string | undefined>,
  runtimeRootPath: string,
): string {
  return (
    env.PI_CODING_AGENT_DIR?.trim() ||
    join(runtimeRootPath, ".pi", "agent")
  );
}

function readSessionsRootPath(
  env: Record<string, string | undefined>,
  runtimeRootPath: string,
): string {
  return (
    env.DANO_SESSIONS_ROOT?.trim() ||
    env.PI_WEB_SESSIONS_ROOT?.trim() ||
    join(runtimeRootPath, DEFAULT_DANO_SESSIONS_DIR)
  );
}

function readEmptyStateConfig(
  env: Record<string, string | undefined>,
): BridgeEmptyStateConfig {
  const html = env.DANO_EMPTY_STATE_HTML;
  if (html?.trim()) {
    return { mode: "html", content: html };
  }

  const text = env.DANO_EMPTY_STATE_TEXT;
  if (text?.trim()) {
    return { mode: "text", content: text };
  }

  return DEFAULT_EMPTY_STATE;
}

function readUserAuthentication(
  env: Record<string, string | undefined>,
): DanoServerOptions["userAuthentication"] {
  const secret = env.DANO_AUTH_JWT_SECRET?.trim();
  if (!secret) return undefined;
  const optional = (value: string | undefined): string | undefined =>
    value?.trim() || undefined;
  return {
    secret,
    issuer: optional(env.DANO_AUTH_JWT_ISSUER),
    audience: optional(env.DANO_AUTH_JWT_AUDIENCE),
    cookieName: optional(env.DANO_AUTH_COOKIE_NAME),
  };
}

function readOAuthAuthentication(
  env: Record<string, string | undefined>,
): DanoServerOptions["oauthAuthentication"] {
  const production = env.NODE_ENV === "production";
  const names = [
    "DANO_OAUTH_ISSUER",
    "DANO_OAUTH_AUTHORIZATION_ENDPOINT",
    "DANO_OAUTH_TOKEN_ENDPOINT",
    "DANO_OAUTH_IDENTITY_ENDPOINT",
    "DANO_OAUTH_API_ORIGIN",
    "DANO_OAUTH_CLIENT_ID",
    "DANO_OAUTH_CLIENT_SECRET",
    "DANO_OAUTH_SCOPE",
    "DANO_OAUTH_REDIRECT_URI",
    "DANO_OAUTH_CREDENTIAL_KEY",
    "DANO_OAUTH_CREDENTIAL_KEY_VERSION",
  ] as const;
  const values = Object.fromEntries(
    names.map(name => [name, env[name]?.trim() || undefined]),
  ) as Record<(typeof names)[number], string | undefined>;
  if (names.every(name => values[name] === undefined)) {
    if (production) throw new Error("OAuth configuration is required in production");
    return undefined;
  }
  if (names.some(name => values[name] === undefined)) {
    throw new Error("OAuth configuration is incomplete");
  }
  const redirectUri = new URL(values.DANO_OAUTH_REDIRECT_URI!);
  const revocation = readOAuthRevocation(env, values.DANO_OAUTH_TOKEN_ENDPOINT!);
  const allowInsecureAuthorizationEndpoint =
    readOptionalBoolean(
      env.DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT,
      "DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT",
    ) ?? false;
  const allowInsecureServerEndpoints =
    readOptionalBoolean(
      env.DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS,
      "DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS",
    ) ?? false;
  const authorizationEndpoint = new URL(
    values.DANO_OAUTH_AUTHORIZATION_ENDPOINT!,
  );
  const optedInHttpAuthorizationEndpoint =
    authorizationEndpoint.protocol === "http:" &&
    allowInsecureAuthorizationEndpoint;
  if (
    redirectUri.pathname !== "/api/auth/callback" ||
    redirectUri.search ||
    redirectUri.hash ||
    redirectUri.username ||
    redirectUri.password
  ) {
    throw new Error("OAuth redirect URI must use /api/auth/callback");
  }
  const providerUrls = [
    ["issuer", new URL(values.DANO_OAUTH_ISSUER!)],
    ["authorization endpoint", authorizationEndpoint],
    ["token endpoint", new URL(values.DANO_OAUTH_TOKEN_ENDPOINT!)],
    ["identity endpoint", new URL(values.DANO_OAUTH_IDENTITY_ENDPOINT!)],
    ...(env.DANO_OAUTH_PROFILE_ENDPOINT?.trim()
      ? [["profile endpoint", new URL(env.DANO_OAUTH_PROFILE_ENDPOINT.trim())] as const]
      : []),
    ["API origin", new URL(values.DANO_OAUTH_API_ORIGIN!)],
    ...(revocation?.endpoint
      ? [["revocation endpoint", new URL(revocation.endpoint)] as const]
      : []),
  ] as const;
  const optedInHttpServerEndpoints = providerUrls.some(
    ([name, url]) =>
      name !== "authorization endpoint" &&
      url.protocol === "http:" &&
      allowInsecureServerEndpoints,
  );
  for (const [name, url] of providerUrls) {
    if (
      url.username ||
      url.password ||
      (name !== "authorization endpoint" && url.hash)
    ) {
      throw new Error(`OAuth ${name} is not trusted`);
    }
    const allowedProtocol =
      url.protocol === "https:" ||
      (url.protocol === "http:" &&
        ((name === "authorization endpoint" &&
          optedInHttpAuthorizationEndpoint) ||
          (name !== "authorization endpoint" &&
            allowInsecureServerEndpoints)));
    if (!allowedProtocol) {
      throw new Error(`OAuth ${name} must use HTTPS`);
    }
  }
  const localHttpCallback =
    !production &&
    redirectUri.protocol === "http:" &&
    (redirectUri.hostname === "localhost" ||
      redirectUri.hostname === "127.0.0.1" ||
      redirectUri.hostname === "[::1]");
  if (redirectUri.protocol !== "https:" && !localHttpCallback) {
    throw new Error("OAuth redirect URI must use trusted HTTPS");
  }
  const providerApiUrl = new URL(values.DANO_OAUTH_API_ORIGIN!);
  if (providerApiUrl.pathname !== "/" || providerApiUrl.search) {
    throw new Error("OAuth API origin must not include a path or query");
  }
  const encodedKey = values.DANO_OAUTH_CREDENTIAL_KEY!;
  if (!/^[A-Za-z0-9_-]+$/.test(encodedKey)) {
    throw new Error("OAuth credential key must be base64url");
  }
  const key = Buffer.from(encodedKey, "base64url");
  if (key.byteLength !== 32) {
    throw new Error("OAuth credential key must decode to 32 bytes");
  }
  const session = {
    idleTtlMs: parseConfiguredPositiveInteger(
      env,
      "DANO_LOGIN_SESSION_IDLE_TTL_MS",
      DEFAULT_DANO_LOGIN_SESSION_IDLE_TTL_MS,
    ),
    absoluteTtlMs: parseConfiguredPositiveInteger(
      env,
      "DANO_LOGIN_SESSION_ABSOLUTE_TTL_MS",
      DEFAULT_DANO_LOGIN_SESSION_ABSOLUTE_TTL_MS,
    ),
    cleanupIntervalMs: parseConfiguredPositiveInteger(
      env,
      "DANO_LOGIN_SESSION_CLEANUP_INTERVAL_MS",
      DEFAULT_DANO_LOGIN_SESSION_CLEANUP_INTERVAL_MS,
    ),
  };
  if (session.absoluteTtlMs <= session.idleTtlMs) {
    throw new Error(
      "OAuth Login Session absolute TTL must be greater than idle TTL",
    );
  }
  const requestHeaders = readOAuthProviderHeaders(
    env.DANO_OAUTH_PROVIDER_HEADERS_JSON,
  );
  const sendStateToTokenEndpoint = readOptionalBoolean(
    env.DANO_OAUTH_SEND_STATE_TO_TOKEN_ENDPOINT,
    "DANO_OAUTH_SEND_STATE_TO_TOKEN_ENDPOINT",
  );
  const clientAuthMethod = readOAuthClientAuthMethod(
    env.DANO_OAUTH_CLIENT_AUTH_METHOD,
  );
  const identityTransport = readOAuthIdentityTransport(
    env.DANO_OAUTH_IDENTITY_TRANSPORT,
  );
  return {
    appOrigin: redirectUri.origin,
    redirectUri: redirectUri.href,
    providerApiOrigin: providerApiUrl.origin,
    ...(providerApiUrl.protocol === "http:" && allowInsecureServerEndpoints
      ? { allowInsecureProviderApiOrigin: true }
      : {}),
    provider: {
      issuer: new URL(values.DANO_OAUTH_ISSUER!).href,
      authorizationEndpoint: authorizationEndpoint.href,
      tokenEndpoint: new URL(values.DANO_OAUTH_TOKEN_ENDPOINT!).href,
      identityEndpoint: new URL(values.DANO_OAUTH_IDENTITY_ENDPOINT!).href,
      ...(env.DANO_OAUTH_PROFILE_ENDPOINT?.trim()
        ? { profileEndpoint: new URL(env.DANO_OAUTH_PROFILE_ENDPOINT.trim()).href }
        : {}),
      ...(identityTransport ? { identityTransport } : {}),
      ...(revocation ? { revocation } : {}),
      clientId: values.DANO_OAUTH_CLIENT_ID!,
      clientSecret: values.DANO_OAUTH_CLIENT_SECRET!,
      ...(clientAuthMethod ? { clientAuthMethod } : {}),
      scope: values.DANO_OAUTH_SCOPE!,
      ...(requestHeaders ? { requestHeaders } : {}),
      ...(sendStateToTokenEndpoint !== undefined
        ? { sendStateToTokenEndpoint }
        : {}),
      ...(optedInHttpAuthorizationEndpoint
        ? { allowInsecureAuthorizationEndpoint: true }
        : {}),
      ...(optedInHttpServerEndpoints
        ? { allowInsecureProviderEndpoints: true }
        : {}),
    },
    credentialEncryptionKey: {
      version: values.DANO_OAUTH_CREDENTIAL_KEY_VERSION!,
      key,
    },
    session,
  };
}

function readOAuthClientAuthMethod(
  value: string | undefined,
): OAuth2ProviderAdapterOptions["clientAuthMethod"] {
  const method = value?.trim();
  if (!method) return undefined;
  if (method === "client_secret_post" || method === "client_secret_basic") {
    return method;
  }
  throw new Error("OAuth client authentication method is unsupported");
}

function readOAuthIdentityTransport(
  value: string | undefined,
): OAuth2ProviderAdapterOptions["identityTransport"] {
  const transport = value?.trim();
  if (!transport) return undefined;
  if (transport === "bearer-get" || transport === "token-introspection") {
    return transport;
  }
  throw new Error("OAuth identity transport is unsupported");
}

function readOAuthRevocation(
  env: Record<string, string | undefined>,
  tokenEndpoint: string,
): OAuth2ProviderAdapterOptions["revocation"] {
  const transport = env.DANO_OAUTH_REVOCATION_TRANSPORT?.trim();
  const endpoint = env.DANO_OAUTH_REVOCATION_ENDPOINT?.trim();
  if (!transport) {
    if (endpoint) {
      throw new Error(
        "OAuth revocation transport is required when its endpoint is configured",
      );
    }
    return undefined;
  }
  if (transport === "rfc7009") {
    if (!endpoint) {
      throw new Error("OAuth RFC 7009 revocation endpoint is required");
    }
    return { transport, endpoint: new URL(endpoint).href };
  }
  if (transport === "delete-query-basic") {
    return {
      transport,
      endpoint: new URL(endpoint || tokenEndpoint).href,
    };
  }
  throw new Error("OAuth revocation transport is unsupported");
}

function readOAuthProviderHeaders(
  value: string | undefined,
): Readonly<Record<string, string>> | undefined {
  if (!value?.trim()) return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("OAuth provider headers must be a JSON object");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("OAuth provider headers must be a JSON object");
  }
  const entries = Object.entries(parsed);
  if (
    entries.some(
      ([name, headerValue]) =>
        !name.trim() || typeof headerValue !== "string" || !headerValue.trim(),
    )
  ) {
    throw new Error("OAuth provider headers must contain string values");
  }
  const headers = new Headers(
    entries.map(([name, headerValue]) => [name, (headerValue as string).trim()]),
  );
  return Object.fromEntries(headers.entries());
}

function readOptionalBoolean(
  value: string | undefined,
  name: string,
): boolean | undefined {
  if (!value?.trim()) return undefined;
  const normalized = value.trim().toLowerCase();
  if (normalized === "1" || normalized === "true") return true;
  if (normalized === "0" || normalized === "false") return false;
  throw new Error(`${name} must be true or false`);
}

function assertProductionAuthenticationEnvironment(
  env: Record<string, string | undefined>,
): void {
  if (env.NODE_ENV !== "production") return;
  const legacyNames = [
    "DANO_AUTH_JWT_SECRET",
    "DANO_AUTH_JWT_ISSUER",
    "DANO_AUTH_JWT_AUDIENCE",
    "DANO_AUTH_COOKIE_NAME",
    "DANO_DEMO_JWT",
    "DANO_DEMO_COOKIE_EXPIRES",
  ];
  if (legacyNames.some(name => env[name]?.trim())) {
    throw new Error("Demo authentication is not allowed in production");
  }
  const tlsOverride = env.NODE_TLS_REJECT_UNAUTHORIZED?.trim();
  if (tlsOverride && tlsOverride !== "1") {
    throw new Error("TLS certificate verification cannot be disabled in production");
  }
}

export async function validateOAuthProviderTls(
  config: OAuthAuthenticationConfig,
  probe: OAuthProviderTlsProbe = probeOAuthProviderTls,
): Promise<void> {
  const endpoints = [
    config.provider.issuer,
    config.provider.authorizationEndpoint,
    config.provider.tokenEndpoint,
    config.provider.identityEndpoint,
    ...(config.provider.profileEndpoint ? [config.provider.profileEndpoint] : []),
    config.providerApiOrigin,
    ...(config.provider.revocation?.endpoint
      ? [config.provider.revocation.endpoint]
      : []),
  ];
  const origins = [
    ...new Map(
      endpoints.flatMap(value => {
        const url = new URL(value);
        return url.protocol === "https:"
          ? [[url.origin, new URL(url.origin)] as const]
          : [];
      }),
    ).values(),
  ];
  try {
    await Promise.all(origins.map(origin => probe(origin)));
  } catch {
    throw new Error("OAuth provider TLS validation failed");
  }
}

function probeOAuthProviderTls(endpoint: URL): Promise<void> {
  return new Promise((resolveProbe, rejectProbe) => {
    const hostname = endpoint.hostname.replace(/^\[|\]$/g, "");
    let settled = false;
    const socket = connectTls({
      host: hostname,
      port: endpoint.port ? Number(endpoint.port) : 443,
      rejectUnauthorized: true,
      ...(isIP(hostname) === 0 ? { servername: hostname } : {}),
      checkServerIdentity: (_servername, certificate) =>
        checkServerIdentity(hostname, certificate),
    });
    const fail = (): void => {
      if (settled) return;
      settled = true;
      socket.destroy();
      rejectProbe(new Error("TLS validation failed"));
    };
    socket.setTimeout(10_000, fail);
    socket.once("error", fail);
    socket.once("secureConnect", () => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolveProbe();
    });
  });
}

function readGuestCookieSecure(
  env: Record<string, string | undefined>,
): boolean {
  if (env.NODE_ENV === "production") return true;
  const configured = env.DANO_GUEST_COOKIE_SECURE?.trim().toLowerCase();
  if (configured === "true") return true;
  if (configured === "false") return false;
  return false;
}

function readUploadConfig(
  env: Record<string, string | undefined>,
  runtimeRootPath: string,
): UploadConfig {
  return {
    uploadDir:
      env.DANO_UPLOAD_DIR?.trim() || join(runtimeRootPath, DEFAULT_DANO_UPLOAD_DIR),
    maxTotalBytes: parsePositiveInteger(
      env.DANO_UPLOAD_MAX_TOTAL_BYTES?.trim(),
      DEFAULT_DANO_UPLOAD_MAX_TOTAL_BYTES,
    ),
    draftTtlMs: parsePositiveInteger(
      env.DANO_UPLOAD_DRAFT_TTL_MS?.trim(),
      DEFAULT_DANO_UPLOAD_DRAFT_TTL_MS,
    ),
    referencedTtlMs: parsePositiveInteger(
      env.DANO_UPLOAD_REFERENCED_TTL_MS?.trim(),
      DEFAULT_DANO_UPLOAD_REFERENCED_TTL_MS,
    ),
    orphanedTtlMs: parsePositiveInteger(
      env.DANO_UPLOAD_ORPHANED_TTL_MS?.trim(),
      DEFAULT_DANO_UPLOAD_ORPHANED_TTL_MS,
    ),
    cleanupIntervalMs: parsePositiveInteger(
      env.DANO_UPLOAD_CLEANUP_INTERVAL_MS?.trim(),
      DEFAULT_DANO_UPLOAD_CLEANUP_INTERVAL_MS,
    ),
  };
}

function findNearestProductPackageJson(startDir: string): string | undefined {
  let current = resolve(startDir);

  for (;;) {
    const candidate = join(current, "package.json");
    const packageInfo = readPackageInfo(candidate);
    if (packageInfo && packageInfo.name !== "@dano/app") {
      return candidate;
    }

    const parent = dirname(current);
    if (parent === current) {
      return undefined;
    }
    current = parent;
  }
}

function findNearestRuntimeDefaultsDir(startDir: string): string | undefined {
  let current = resolve(startDir);

  for (;;) {
    const candidate = join(current, "deploy", "runtime-defaults");
    if (existsSync(candidate)) {
      return candidate;
    }

    const parent = dirname(current);
    if (parent === current) {
      return undefined;
    }
    current = parent;
  }
}

function resolveStaticDirCandidate(entryFile: string): string | undefined {
  const resolvedEntryFile = resolve(entryFile);
  const sourceMarker = `${sep}apps${sep}dano${sep}src${sep}`;
  const sourceIndex = resolvedEntryFile.lastIndexOf(sourceMarker);
  if (sourceIndex !== -1) {
    return join(
      resolvedEntryFile.slice(0, sourceIndex),
      "apps",
      "dano",
      "dist",
      "web",
    );
  }

  const serverMarker = `${sep}dist${sep}server${sep}`;
  const serverIndex = resolvedEntryFile.lastIndexOf(serverMarker);
  if (serverIndex !== -1) {
    return join(resolvedEntryFile.slice(0, serverIndex), "dist", "web");
  }

  return undefined;
}

export function resolveDefaultStaticDir(entryFile: string): string | undefined {
  const candidate = resolveStaticDirCandidate(entryFile);
  if (!candidate || !existsSync(join(candidate, "index.html"))) {
    return undefined;
  }

  return resolve(candidate);
}

function readPackageInfo(path: string): DanoPackageInfo | undefined {
  if (!existsSync(path)) return undefined;

  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as {
      name?: unknown;
      version?: unknown;
    };
    return typeof raw.name === "string" && typeof raw.version === "string"
      ? { name: raw.name, version: raw.version }
      : undefined;
  } catch {
    return undefined;
  }
}

export function readDanoPackageInfo(cwd: string): DanoPackageInfo {
  const packagedRoot = readPackageInfo(
    join(cwd, "package-versions", "package.json"),
  );
  if (packagedRoot) return packagedRoot;

  const devRoot = findNearestProductPackageJson(cwd);
  if (devRoot) {
    return (
      readPackageInfo(devRoot) ?? { name: "@dano/dano", version: "unknown" }
    );
  }

  return { name: "@dano/dano", version: "unknown" };
}

export function parseDanoServerOptions(
  argv: string[],
  env: Record<string, string | undefined> = process.env,
  danoConfig: DanoConfig = loadDanoConfig({ cwd: process.cwd(), env }),
): DanoServerOptions {
  assertProductionAuthenticationEnvironment(env);
  let host = readHost(env);
  let port = readPort(env);
  const runtimeRootPath = readRuntimeRootPath(env);
  let defaultWorkspacePath: string | undefined;
  let sessionsRootPath = readSessionsRootPath(env, runtimeRootPath);
  const productName = resolveProductName(
    env.DANO_PRODUCT_NAME,
    danoConfig.productName,
  );
  let emptyState = readEmptyStateConfig(env);
  const upload = readUploadConfig(env, runtimeRootPath);
  const anonymousUserCleanup = {
    idleTtlMs: parseConfiguredPositiveInteger(
      env,
      "DANO_ANONYMOUS_IDLE_TTL_MS",
      DEFAULT_DANO_ANONYMOUS_IDLE_TTL_MS,
    ),
    intervalMs: parseConfiguredPositiveInteger(
      env,
      "DANO_ANONYMOUS_CLEANUP_INTERVAL_MS",
      DEFAULT_DANO_ANONYMOUS_CLEANUP_INTERVAL_MS,
    ),
  };
  const userAuthentication = readUserAuthentication(env);
  const oauthAuthentication = readOAuthAuthentication(env);
  const guestCookieSecure = readGuestCookieSecure(env);
  const staticDirOverride = env.DANO_STATIC_DIR?.trim();
  let help = false;
  let validateConfig = false;

  for (let index = 0; index < argv.length; index++) {
    const token = argv[index];
    if (!token || token === "--") {
      continue;
    }

    switch (token) {
      case "--help":
      case "-h":
        help = true;
        continue;
      case "--validate-config":
        validateConfig = true;
        continue;
      case "--host": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --host");
        }
        host = next;
        index++;
        continue;
      }
      case "--port": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --port");
        }
        port = parseInteger(next, DEFAULT_DANO_PORT);
        index++;
        continue;
      }
      case "--default-workspace": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --default-workspace");
        }
        defaultWorkspacePath = next;
        index++;
        continue;
      }
      case "--sessions-root": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --sessions-root");
        }
        sessionsRootPath = next;
        index++;
        continue;
      }
      case "--empty-state-text": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --empty-state-text");
        }
        emptyState = { mode: "text", content: next };
        index++;
        continue;
      }
      case "--empty-state-html": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --empty-state-html");
        }
        emptyState = { mode: "html", content: next };
        index++;
        continue;
      }
      default:
        throw new Error(`Unknown option: ${token}`);
    }
  }

  const cwd = process.cwd();
  const resolvedRuntimeRootPath = resolve(cwd, runtimeRootPath);
  const resolvedDefaultWorkspacePath = defaultWorkspacePath
    ? resolve(cwd, defaultWorkspacePath)
    : readDefaultWorkspacePath(resolvedRuntimeRootPath);
  return {
    cwd,
    runtimeRootPath: resolvedRuntimeRootPath,
    host,
    port,
    defaultWorkspacePath: resolvedDefaultWorkspacePath,
    agentConfigDir: resolve(
      cwd,
      readAgentConfigDir(env, resolvedRuntimeRootPath),
    ),
    sessionsRootPath: resolve(cwd, sessionsRootPath),
    productName,
    emptyState,
    upload: {
      ...upload,
      uploadDir: resolve(cwd, upload.uploadDir),
    },
    anonymousUserCleanup,
    guestCookieSecure,
    staticDir: staticDirOverride
      ? resolve(cwd, staticDirOverride)
      : resolveDefaultStaticDir(fileURLToPath(import.meta.url)),
    help,
    validateConfig,
    userAuthentication,
    oauthAuthentication,
  };
}

export async function initializeDanoAgentSettings(
  agentDir: string,
  sourceCwd: string,
  productName: string,
): Promise<void> {
  const runtimeDefaultsDir = findNearestRuntimeDefaultsDir(sourceCwd);
  if (!runtimeDefaultsDir) {
    return;
  }

  const targetSettingsDir = agentDir;
  mkdirSync(targetSettingsDir, { recursive: true });

  for (const fileName of DEFAULT_RUNTIME_SETTINGS_FILES) {
    const sourcePath = join(runtimeDefaultsDir, fileName);
    const targetPath = join(targetSettingsDir, fileName);
    if (!existsSync(sourcePath)) {
      continue;
    }

    if (fileName === "SYSTEM.md") {
      await syncSystemPrompt({
        templatePath: sourcePath,
        targetPath,
        productName,
        mode: "if-missing",
      });
    } else {
      try {
        copyFileSync(sourcePath, targetPath, constants.COPYFILE_EXCL);
      } catch (error) {
        const alreadyExists =
          error instanceof Error &&
          "code" in error &&
          error.code === "EEXIST";
        if (!alreadyExists) {
          throw error;
        }
      }
    }
  }

  migrateHeimdallRuntimeSettings(join(targetSettingsDir, "heimdall.json"));
}

function mergeStringArray(value: unknown, required: readonly string[]): string[] {
  const existing = Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
  return [...new Set([...existing, ...required])];
}

function globPatternMatches(pattern: string, value: string): boolean {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`).test(value);
}

function migrateHeimdallRuntimeSettings(path: string): void {
  if (!existsSync(path)) return;

  let settings: unknown;
  try {
    settings = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return;
  }
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) return;

  const root = settings as { sandbox?: unknown };
  if (!root.sandbox || typeof root.sandbox !== "object" || Array.isArray(root.sandbox)) {
    root.sandbox = {};
  }

  const sandbox = root.sandbox as { userNamespace?: unknown; env?: unknown };
  if (sandbox.userNamespace === undefined) {
    sandbox.userNamespace = false;
  }

  if (!sandbox.env || typeof sandbox.env !== "object" || Array.isArray(sandbox.env)) {
    sandbox.env = {};
  }
  const env = sandbox.env as { allow?: unknown; deny?: unknown };
  env.allow = mergeStringArray(env.allow, DANO_HEIMDALL_SANDBOX_ENV_ALLOW);
  env.deny = Array.isArray(env.deny)
    ? env.deny.filter(
        (item): item is string =>
          typeof item === "string" &&
          !globPatternMatches(item, "DANO_URL") &&
          !globPatternMatches(item, "DANO_TENANT_KEY"),
      )
    : [];
  writeFileSync(path, `${JSON.stringify(root, null, 2)}\n`);
}

/** Installed supervisor entrypoint injection; never loaded from user config. */
export interface DanoMainServices {
  protectedToolsForUser?: StartDanoServerOptions["protectedToolsForUser"];
  signal?: AbortSignal;
}

async function runDanoServer(
  runtime: DanoRuntime,
  config: BridgeConfig,
  options: DanoServerOptions,
  entryFile: string,
  danoConfig: DanoConfig,
  services: DanoMainServices,
): Promise<boolean> {
  services.signal?.throwIfAborted();
  let resolveStopped: (() => void) | undefined;
  const stopped = new Promise<void>(resolve => {
    resolveStopped = resolve;
  });

  const oauthProvider = options.oauthAuthentication
    ? createOAuth2ProviderAdapter(options.oauthAuthentication.provider)
    : undefined;
  const oauthAuthentication = options.oauthAuthentication && oauthProvider
    ? await createOAuthAuthentication({
        runtimeRootPath: options.runtimeRootPath,
        appOrigin: options.oauthAuthentication.appOrigin,
        redirectUri: options.oauthAuthentication.redirectUri,
        provider: oauthProvider,
        credentialEncryptionKey:
          options.oauthAuthentication.credentialEncryptionKey,
        sessionIdleTtlMs: options.oauthAuthentication.session.idleTtlMs,
        sessionAbsoluteTtlMs:
          options.oauthAuthentication.session.absoluteTtlMs,
        sessionGcIntervalMs:
          options.oauthAuthentication.session.cleanupIntervalMs,
      })
    : undefined;
  const jwtAuthentication = options.userAuthentication
    ? createJwtUserContextResolver({
        runtimeRootPath: options.runtimeRootPath,
        ...options.userAuthentication,
      })
    : undefined;
  const anonymousUsers = createAnonymousUserContextResolver({
    runtimeRootPath: options.runtimeRootPath,
    secureCookie: options.guestCookieSecure,
    authenticatedResolver: oauthAuthentication ?? jwtAuthentication,
    activityWriteIntervalMs: Math.max(
      1,
      Math.min(
        60 * 1000,
        Math.floor(options.anonymousUserCleanup.idleTtlMs / 2),
      ),
    ),
  });
  let bridgeController:
    | Awaited<ReturnType<DanoRuntime["startDanoServer"]>>
    | undefined;
  const credentialBroker =
    oauthAuthentication && options.oauthAuthentication
      ? new CredentialBroker({
          providerApiOrigin: options.oauthAuthentication.providerApiOrigin,
          ...(options.oauthAuthentication.allowInsecureProviderApiOrigin
            ? { allowInsecureProviderApiOrigin: true }
            : {}),
          readCredential: loginSessionId =>
            oauthAuthentication.readProviderCredential(loginSessionId),
          refreshCredential: loginSessionId =>
            oauthAuthentication.refreshProviderCredential(loginSessionId),
          isAccessTokenInvalid: response =>
            oauthProvider?.isAccessTokenInvalid?.(response) ??
            response.status === 401,
          requireReauthentication: async loginSessionId => {
            try {
              await oauthAuthentication.requireReauthentication(loginSessionId);
            } finally {
              bridgeController?.requireReauthentication(loginSessionId);
            }
          },
        })
      : undefined;
  try {
    bridgeController = await runtime.startDanoServer(config, {
      cwd: options.cwd,
      sessionsRootPath: options.sessionsRootPath,
      danoConfig,
      userContextResolver: anonymousUsers,
      anonymousUsers,
      anonymousUserCleanup: options.anonymousUserCleanup,
      authHttpHandler: oauthAuthentication,
      credentialBroker,
      protectedToolsForUser: services.protectedToolsForUser,
      onShutdown: () => resolveStopped?.(),
    });
  } catch (error) {
    await oauthAuthentication?.dispose();
    throw error;
  }

  const bridgeUrl = bridgeController.getBridgeUrl();
  if (!bridgeUrl) {
    await bridgeController.stop();
    throw new Error("Bridge started without a reachable URL");
  }

  console.log(`[dano] Server URL: ${bridgeUrl}`);
  console.log(`[dano] HTTP API: ${bridgeUrl}/api/clients`);
  console.log(`[dano] SSE events: ${bridgeUrl}/api/clients/<clientId>/events`);
  if (options.staticDir) {
    console.log(`[dano] Static Dir: ${options.staticDir}`);
  }
  if (config.defaultWorkspacePath) {
    console.log(`[dano] Default Workspace: ${config.defaultWorkspacePath}`);
  } else {
    console.log("[dano] Runtime Workspace: isolated per User");
  }
  console.log("[dano] Session Registry: isolated per User");

  const requestStop = async (): Promise<void> => {
    await bridgeController.stop().catch(error => {
      console.error("[dano] Failed to stop Dano server:", error);
    });
  };

  const devReload = createDanoDevReloadController({
    entryFile,
    stop: requestStop,
  });

  const onSigterm = (): void => {
    void requestStop();
  };

  process.on("SIGTERM", onSigterm);
  services.signal?.addEventListener("abort", onSigterm, { once: true });
  if (services.signal?.aborted) onSigterm();

  try {
    await stopped;
  } finally {
    process.off("SIGTERM", onSigterm);
    services.signal?.removeEventListener("abort", onSigterm);
    devReload?.dispose();
    await oauthAuthentication?.dispose();
  }

  return devReload?.reloadRequested() ?? false;
}

export async function runDanoMain(services: DanoMainServices = {}): Promise<number> {
  services.signal?.throwIfAborted();
  let options: DanoServerOptions;
  let danoConfig: DanoConfig;
  try {
    danoConfig = loadDanoConfig({ cwd: process.cwd() });
    options = parseDanoServerOptions(
      process.argv.slice(2),
      process.env,
      danoConfig,
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`[dano] ${message}`);
    printHelp();
    return 1;
  }

  if (options.help) {
    printHelp();
    return 0;
  }
  if (process.env.NODE_ENV === "production" && options.oauthAuthentication) {
    await validateOAuthProviderTls(options.oauthAuthentication);
  }
  if (options.validateConfig) {
    console.log("[dano] Production configuration is valid.");
    return 0;
  }

  const thisFile = fileURLToPath(import.meta.url);
  const packageInfo = readDanoPackageInfo(options.cwd);
  process.env.DANO_PACKAGE_NAME ??= packageInfo.name;
  process.env.DANO_VERSION ??= packageInfo.version;
  assertRuntimePersistenceWritable(options);
  if (!process.env.PI_CODING_AGENT_DIR?.trim()) {
    process.env.PI_CODING_AGENT_DIR = options.agentConfigDir;
  }
  await initializeDanoAgentSettings(
    options.agentConfigDir,
    options.cwd,
    options.productName,
  );
  while (true) {
    const runtime = await loadDanoRuntime(thisFile);
    const config: BridgeConfig = {
      ...runtime.DEFAULT_BRIDGE_CONFIG,
      host: options.host,
      port: options.port,
      defaultWorkspacePath: options.defaultWorkspacePath,
      productName: options.productName,
      emptyState: options.emptyState,
      upload: options.upload,
      quickActions: danoConfig.quickActions ?? [],
      slashCommandsAndMentionsEnabled:
        danoConfig.slashCommandsAndMentionsEnabled ?? false,
      transcriptProcessSummaryEnabled:
        danoConfig.transcriptProcessSummaryEnabled ?? false,
      staticDir: options.staticDir,
    };

    const reloadRequested = await runDanoServer(
      runtime,
      config,
      options,
      thisFile,
      danoConfig,
      services,
    );

    if (!reloadRequested) {
      return 0;
    }

    console.log("[dano] Dano server runtime reloaded.");
  }
}

const invokedPath = process.argv[1];
const thisFile = fileURLToPath(import.meta.url);
if (
  invokedPath &&
  realpathSync(resolve(invokedPath)) === realpathSync(resolve(thisFile))
) {
  runDanoMain().then(
    code => {
      process.exitCode = code;
    },
    error => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    },
  );
}
