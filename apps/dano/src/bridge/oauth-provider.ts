import { AsyncLocalStorage } from "node:async_hooks";
import * as oauth from "openid-client";

export interface ExternalIdentity {
  readonly userId: string;
  readonly displayName?: string;
  readonly avatarUrl?: string;
}

export class OAuthProviderContractError extends Error {
  readonly code = "provider_identity_invalid" as const;

  constructor() {
    super("Provider identity response does not match the configured contract");
    this.name = "OAuthProviderContractError";
  }
}

export interface ProviderCredential {
  readonly accessToken: string;
  readonly refreshToken?: string;
  readonly tokenType?: string;
  readonly expiresAt?: number;
}

export interface OAuthProviderAdapter {
  authorizationUrl(input: {
    readonly state: string;
    readonly redirectUri: string;
  }): URL;
  exchangeAuthorizationCode(input: {
    readonly code: string;
    readonly state: string;
    readonly redirectUri: string;
  }): Promise<{
    readonly identity: ExternalIdentity;
    readonly credential: ProviderCredential;
  }>;
  refreshCredential?(
    credential: ProviderCredential,
  ): Promise<ProviderCredential>;
  validateCredential?(
    credential: ProviderCredential,
  ): Promise<ExternalIdentity>;
  isAccessTokenInvalid?(response: Response): boolean | Promise<boolean>;
  revokeCredential?(credential: ProviderCredential): Promise<void>;
}

export interface OAuth2ProviderAdapterOptions {
  readonly issuer: string;
  readonly authorizationEndpoint: string;
  readonly tokenEndpoint: string;
  readonly identityEndpoint: string;
  readonly identityTransport?: "bearer-get" | "token-introspection";
  /** Optional Bearer GET profile source; never establishes or changes identity. */
  readonly profileEndpoint?: string;
  readonly revocation?:
    | { readonly transport: "rfc7009"; readonly endpoint: string }
    | { readonly transport: "delete-query-basic"; readonly endpoint?: string };
  readonly clientId: string;
  readonly clientSecret: string;
  readonly clientAuthMethod?: "client_secret_post" | "client_secret_basic";
  readonly scope: string;
  readonly requestHeaders?: Readonly<Record<string, string>>;
  readonly sendStateToTokenEndpoint?: boolean;
  readonly timeoutMs?: number;
  /** Explicit deployment opt-in for a browser-facing HTTP authorization URL. */
  readonly allowInsecureAuthorizationEndpoint?: boolean;
  /** Explicit deployment opt-in for plaintext HTTP provider endpoints. */
  readonly allowInsecureProviderEndpoints?: boolean;
  /** Test-only escape hatch for a loopback fake provider. */
  readonly allowInsecureRequests?: boolean;
}

export function createOAuth2ProviderAdapter(
  options: OAuth2ProviderAdapterOptions,
): OAuthProviderAdapter {
  const authorizationEndpoint = new URL(options.authorizationEndpoint);
  const protocolAuthorizationEndpoint = new URL(authorizationEndpoint);
  if (authorizationEndpoint.hash) {
    protocolAuthorizationEndpoint.search = "";
    protocolAuthorizationEndpoint.hash = "";
  }
  const tokenEndpoint = new URL(options.tokenEndpoint);
  const revocationEndpoint = options.revocation
    ? new URL(options.revocation.endpoint ?? tokenEndpoint)
    : undefined;
  const timeoutMs = options.timeoutMs ?? 10_000;
  const tokenExchangeState = new AsyncLocalStorage<string>();
  const clientId = required(options.clientId, "OAuth client ID");
  const clientSecret = required(options.clientSecret, "OAuth client secret");
  const clientAuthentication =
    options.clientAuthMethod === "client_secret_basic"
      ? oauth.ClientSecretBasic(clientSecret)
      : oauth.ClientSecretPost(clientSecret);
  const serverMetadata = {
    issuer: new URL(options.issuer).href,
    authorization_endpoint: protocolAuthorizationEndpoint.href,
    token_endpoint: tokenEndpoint.href,
    ...(options.identityTransport === "token-introspection"
      ? { introspection_endpoint: new URL(options.identityEndpoint).href }
      : {}),
    ...(options.revocation?.transport === "rfc7009" && revocationEndpoint
      ? { revocation_endpoint: revocationEndpoint.href }
      : {}),
  };
  const configuration = new oauth.Configuration(
    serverMetadata,
    clientId,
    { client_secret: clientSecret },
    clientAuthentication,
  );
  configuration.timeout = timeoutMs / 1000;
  if (
    options.allowInsecureAuthorizationEndpoint ||
    options.allowInsecureProviderEndpoints ||
    options.allowInsecureRequests
  ) {
    oauth.allowInsecureRequests(configuration);
  }
  const requestHeaders = new Headers(options.requestHeaders);
  configuration[oauth.customFetch] = async (url, init) => {
    const headers = new Headers(requestHeaders);
    for (const [name, value] of Object.entries(init.headers)) {
      headers.set(name, value);
    }
    const response = await fetch(url, {
      ...init,
      headers,
      body:
        options.sendStateToTokenEndpoint &&
        new URL(url).href === tokenEndpoint.href
          ? tokenBodyWithState(init.body, tokenExchangeState.getStore())
          : init.body,
    });
    const responseUrl = new URL(url).href;
    if (responseUrl === tokenEndpoint.href) {
      return normalizeTokenEndpointResponse(response);
    }
    if (
      options.identityTransport === "token-introspection" &&
      responseUrl === identityEndpoint.href
    ) {
      return normalizeIntrospectionEndpointResponse(response);
    }
    return response;
  };
  const identityEndpoint = new URL(options.identityEndpoint);
  const scope = required(options.scope, "OAuth scope");
  const profileEndpoint = options.profileEndpoint
    ? new URL(options.profileEndpoint)
    : undefined;

  async function resolveIdentity(
    accessToken: string,
    tokenType: string | undefined,
  ): Promise<ExternalIdentity> {
    const identity = await fetchExternalIdentity(
      configuration,
      accessToken,
      tokenType,
      identityEndpoint,
      options.identityTransport,
    );
    if (!profileEndpoint) return identity;
    try {
      const profile = await fetchExternalIdentity(
        configuration,
        accessToken,
        tokenType,
        profileEndpoint,
        "bearer-get",
      );
      if (profile.userId !== identity.userId) return identity;
      return { ...identity, ...profile };
    } catch {
      // Profile availability must not invalidate an independently verified identity.
      return identity;
    }
  }

  const adapter: OAuthProviderAdapter = {
    authorizationUrl({ state, redirectUri }) {
      const url = oauth.buildAuthorizationUrl(configuration, {
        redirect_uri: redirectUri,
        scope,
        state,
      });
      return authorizationEndpoint.hash
        ? authorizationUrlWithFragmentRoute(authorizationEndpoint, url)
        : url;
    },

    async exchangeAuthorizationCode({ code, state, redirectUri }) {
      const callbackUrl = new URL(redirectUri);
      callbackUrl.searchParams.set("code", code);
      callbackUrl.searchParams.set("state", state);
      const tokens = await tokenExchangeState.run(state, () =>
        oauth.authorizationCodeGrant(
          configuration,
          callbackUrl,
          { expectedState: state },
        ),
      );
      const identity = await resolveIdentity(
        tokens.access_token,
        tokens.token_type,
      );
      const expiresIn = tokens.expiresIn();
      return {
        identity,
        credential: tokenResponseCredential(tokens, expiresIn),
      };
    },

    async refreshCredential(credential) {
      if (!credential.refreshToken) {
        throw new Error("Provider refresh credential is unavailable");
      }
      const tokens = await oauth.refreshTokenGrant(
        configuration,
        credential.refreshToken,
      );
      const expiresIn = tokens.expiresIn();
      const refreshed = tokenResponseCredential(
        tokens,
        expiresIn,
        credential.refreshToken,
      );
      return refreshed;
    },

    async validateCredential(credential) {
      return resolveIdentity(
        credential.accessToken,
        credential.tokenType,
      );
    },

    async isAccessTokenInvalid(response) {
      if (response.status === 401) return true;
      let value: unknown;
      try {
        value = await response.clone().json();
      } catch {
        return false;
      }
      return providerAuthenticationInvalid(value);
    },

  };
  let revokeCredential:
    | ((credential: ProviderCredential) => Promise<void>)
    | undefined;
  if (options.revocation?.transport === "rfc7009") {
    revokeCredential = async credential => {
      await oauth.tokenRevocation(configuration, credential.accessToken);
    };
  } else if (
    options.revocation?.transport === "delete-query-basic" &&
    revocationEndpoint
  ) {
    revokeCredential = async credential => {
      await deleteQueryRevocation({
        endpoint: revocationEndpoint,
        accessToken: credential.accessToken,
        clientId,
        clientSecret,
        requestHeaders,
        timeoutMs,
      });
    };
  }
  return {
    ...adapter,
    ...(revokeCredential ? { revokeCredential } : {}),
  };
}

function authorizationUrlWithFragmentRoute(
  endpoint: URL,
  protocolUrl: URL,
): URL {
  const url = new URL(endpoint);
  const fragment = url.hash.slice(1);
  const queryIndex = fragment.indexOf("?");
  const route = queryIndex === -1 ? fragment : fragment.slice(0, queryIndex);
  const parameters = new URLSearchParams(
    queryIndex === -1 ? "" : fragment.slice(queryIndex + 1),
  );
  for (const [name, value] of protocolUrl.searchParams) {
    url.searchParams.delete(name);
    parameters.set(name, value);
  }
  const query = parameters.toString();
  url.hash = query ? `${route}?${query}` : route;
  return url;
}

async function fetchExternalIdentity(
  configuration: oauth.Configuration,
  accessToken: string,
  tokenType: string | undefined,
  identityEndpoint: URL,
  identityTransport: OAuth2ProviderAdapterOptions["identityTransport"],
): Promise<ExternalIdentity> {
  if (identityTransport === "token-introspection") {
    const identity = await oauth.tokenIntrospection(configuration, accessToken);
    if (!identity.active) throw new OAuthProviderContractError();
    return parseExternalIdentity({
      ...identity,
      userId: identity.userId ?? identity.user_id ?? identity.id ?? identity.sub,
    });
  }
  if (tokenType && tokenType.trim().toLowerCase() !== "bearer") {
    throw new Error("Provider access token type is unsupported");
  }
  const response = await oauth.fetchProtectedResource(
    configuration,
    accessToken,
    identityEndpoint,
    "GET",
  );
  if (!response.ok) {
    throw new Error("Provider identity request failed", {
      cause: { status: response.status },
    });
  }
  return parseExternalIdentity(await response.json());
}

async function deleteQueryRevocation(input: {
  readonly endpoint: URL;
  readonly accessToken: string;
  readonly clientId: string;
  readonly clientSecret: string;
  readonly requestHeaders: Headers;
  readonly timeoutMs: number;
}): Promise<void> {
  const url = new URL(input.endpoint);
  url.searchParams.set("token", input.accessToken);
  const headers = new Headers(input.requestHeaders);
  headers.set(
    "authorization",
    `Basic ${Buffer.from(`${input.clientId}:${input.clientSecret}`).toString("base64")}`,
  );
  const response = await fetch(url, {
    method: "DELETE",
    headers,
    redirect: "error",
    signal: AbortSignal.timeout(input.timeoutMs),
  });
  if (!response.ok) throw new Error("Provider credential revocation failed");
  let result: unknown;
  try {
    result = await response.json();
  } catch {
    return;
  }
  if (
    result &&
    typeof result === "object" &&
    !Array.isArray(result) &&
    "code" in result &&
    (result as Record<string, unknown>).code !== 0
  ) {
    throw new Error("Provider credential revocation failed");
  }
}

function tokenResponseCredential(
  tokens: {
    readonly access_token: string;
    readonly refresh_token?: string;
    readonly token_type?: string;
  },
  expiresIn: number | undefined,
  fallbackRefreshToken?: string,
): ProviderCredential {
  const refreshToken = tokens.refresh_token ?? fallbackRefreshToken;
  return {
    accessToken: tokens.access_token,
    ...(refreshToken ? { refreshToken } : {}),
    ...(tokens.token_type ? { tokenType: tokens.token_type } : {}),
    ...(expiresIn !== undefined
      ? { expiresAt: Date.now() + expiresIn * 1000 }
      : {}),
  };
}

function parseExternalIdentity(value: unknown): ExternalIdentity {
  const identity = providerDataObject(value);
  if (!identity) {
    throw new OAuthProviderContractError();
  }
  const userId = normalizedIdentifier(
    identity.userId ?? identity.user_id ?? identity.id,
  );
  if (!userId) {
    throw new OAuthProviderContractError();
  }
  const displayName = normalizedString(
    identity.displayName ?? identity.nickname ?? identity.name ?? identity.username,
  );
  const avatarUrl = normalizedString(identity.avatarUrl ?? identity.avatar);
  return {
    userId,
    ...(displayName ? { displayName } : {}),
    ...(avatarUrl ? { avatarUrl } : {}),
  };
}

async function normalizeTokenEndpointResponse(
  response: Response,
): Promise<Response> {
  let value: unknown;
  try {
    value = await response.clone().json();
  } catch {
    return response;
  }
  const data = providerDataObject(value);
  if (!data || typeof data.access_token !== "string") return response;

  const headers = new Headers(response.headers);
  headers.delete("content-encoding");
  headers.delete("content-length");
  headers.set("content-type", "application/json");
  const normalized = new Response(JSON.stringify(data), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
  Object.defineProperty(normalized, "url", { value: response.url });
  return normalized;
}

async function normalizeIntrospectionEndpointResponse(
  response: Response,
): Promise<Response> {
  let value: unknown;
  try {
    value = await response.clone().json();
  } catch {
    return response;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return response;
  }
  const record = value as Record<string, unknown>;
  if (!("code" in record)) return response;
  const successful = record.code === 0 || record.code === "0";
  const data =
    successful &&
    record.data &&
    typeof record.data === "object" &&
    !Array.isArray(record.data)
      ? (record.data as Record<string, unknown>)
      : {};
  const headers = new Headers(response.headers);
  headers.delete("content-encoding");
  headers.delete("content-length");
  headers.set("content-type", "application/json");
  const normalized = new Response(
    JSON.stringify({ active: successful, ...data }),
    {
      status: response.status,
      statusText: response.statusText,
      headers,
    },
  );
  Object.defineProperty(normalized, "url", { value: response.url });
  return normalized;
}

function providerDataObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if ("code" in record && record.code !== 0 && record.code !== "0") return null;
  if (
    !("userId" in record) &&
    !("id" in record) &&
    record.data &&
    typeof record.data === "object" &&
    !Array.isArray(record.data)
  ) {
    return record.data as Record<string, unknown>;
  }
  return record;
}

function providerAuthenticationInvalid(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const code = (value as Record<string, unknown>).code;
  return code === 401 || (typeof code === "string" && code.trim() === "401");
}

function normalizedIdentifier(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isSafeInteger(value)) {
    return String(value);
  }
  return null;
}

function normalizedString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function tokenBodyWithState(
  body: oauth.FetchBody,
  state: string | undefined,
): oauth.FetchBody {
  if (!state) return body;
  const params =
    body instanceof URLSearchParams
      ? new URLSearchParams(body)
      : typeof body === "string"
        ? new URLSearchParams(body)
        : null;
  if (!params || params.get("grant_type") !== "authorization_code") {
    return body;
  }
  params.set("state", state);
  return params;
}

function required(value: string, name: string): string {
  if (!value.trim()) throw new Error(`${name} is required`);
  return value.trim();
}
