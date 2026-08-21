import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAnonymousUserContextResolver } from "../bridge/anonymous-user-context.js";
import {
  createOAuthAuthentication,
  type OAuthAuthenticationOptions,
  type OAuthProviderAdapter,
} from "../bridge/oauth-authentication.js";
import {
  createOAuth2ProviderAdapter,
  OAuthProviderContractError,
} from "../bridge/oauth-provider.js";
import {
  DEFAULT_BRIDGE_CONFIG,
  type ClientMessage,
  type ServerMessage,
} from "../bridge/types.js";
import { startDanoServer, type DanoServerController } from "../server.js";

const controllers: DanoServerController[] = [];
const authentications: Array<{ dispose(): Promise<void> }> = [];
const runtimeRoots: string[] = [];
const providerServers: http.Server[] = [];

afterEach(async () => {
  await Promise.all(controllers.splice(0).map(controller => controller.stop()));
  await Promise.all(
    authentications.splice(0).map(authentication => authentication.dispose()),
  );
  for (const root of runtimeRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
  await Promise.all(
    providerServers.splice(0).map(
      server =>
        new Promise<void>(resolve => server.close(() => resolve())),
    ),
  );
});

async function startOAuthServer(
  provider: OAuthProviderAdapter,
  existingRuntimeRootPath?: string,
  overrides: Partial<
    Pick<
      OAuthAuthenticationOptions,
      | "credentialEncryptionKey"
      | "maxPendingTransactions"
      | "now"
      | "sessionAbsoluteTtlMs"
      | "sessionGcIntervalMs"
      | "sessionIdleTtlMs"
      | "stateTtlMs"
    >
  > = {},
  anonymousCleanup?: {
    idleTtlMs: number;
    intervalMs: number;
    now: () => number;
  },
) {
  const runtimeRootPath =
    existingRuntimeRootPath ??
    fs.mkdtempSync(path.join(os.tmpdir(), "dano-oauth-http-"));
  if (!existingRuntimeRootPath) runtimeRoots.push(runtimeRootPath);
  const authentication = await createOAuthAuthentication({
    runtimeRootPath,
    appOrigin: "https://dano.example.test",
    redirectUri: "https://dano.example.test/api/auth/callback",
    provider,
    credentialEncryptionKey: {
      version: "test-v1",
      key: Buffer.alloc(32, 7),
    },
    ...overrides,
  });
  authentications.push(authentication);
  const anonymousUsers = createAnonymousUserContextResolver({
    runtimeRootPath,
    secureCookie: false,
    authenticatedResolver: authentication,
    now: anonymousCleanup?.now,
    activityWriteIntervalMs: anonymousCleanup
      ? Math.max(1, Math.floor(anonymousCleanup.idleTtlMs / 2))
      : undefined,
  });
  const controller = await startDanoServer(
    {
      ...DEFAULT_BRIDGE_CONFIG,
      host: "127.0.0.1",
      port: 0,
      upload: {
        ...DEFAULT_BRIDGE_CONFIG.upload,
        uploadDir: path.join(runtimeRootPath, "uploads"),
      },
    },
    {
      captureSigint: false,
      userContextResolver: anonymousUsers,
      ...(anonymousCleanup
        ? {
            anonymousUsers,
            anonymousUserCleanup: anonymousCleanup,
          }
        : {}),
      authHttpHandler: authentication,
    },
  );
  controllers.push(controller);
  const origin = controller.getBridgeUrl();
  if (!origin) throw new Error("Dano OAuth test server did not start");
  return { authentication, controller, origin, runtimeRootPath };
}

function cookieFrom(response: Response, name: string): string {
  const setCookie = response.headers.get("set-cookie");
  if (!setCookie) throw new Error(`Expected ${name} Cookie`);
  const pair = setCookie.split(";", 1)[0];
  if (!pair?.startsWith(`${name}=`)) {
    throw new Error(`Expected ${name} Cookie`);
  }
  return pair;
}

async function startFakeProvider(options: {
  tokenDelayMs?: number;
  tokenStatus?: number;
  tokenResponse?: unknown | ((request: URLSearchParams) => unknown);
  identityStatus?: number;
  identity?: unknown;
} = {}) {
  const tokenRequests: URLSearchParams[] = [];
  const tokenRequestHeaders: http.IncomingHttpHeaders[] = [];
  const identityAuthorization: string[] = [];
  const identityRequestHeaders: http.IncomingHttpHeaders[] = [];
  const revocationRequests: Array<{
    method: string;
    token: string | null;
    authorization: string;
    providerContext: string;
  }> = [];
  const server = http.createServer((req, res) => {
    const requestUrl = new URL(req.url ?? "/", "http://provider.invalid");
    if (req.method === "DELETE" && requestUrl.pathname === "/revoke") {
      revocationRequests.push({
        method: req.method,
        token: requestUrl.searchParams.get("token"),
        authorization: req.headers.authorization ?? "",
        providerContext: String(req.headers["x-provider-context"] ?? ""),
      });
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ code: 0, data: true }));
      return;
    }
    if (req.method === "POST" && req.url === "/token") {
      let body = "";
      req.setEncoding("utf8");
      req.on("data", chunk => (body += chunk));
      req.on("end", () => {
        tokenRequestHeaders.push(req.headers);
        tokenRequests.push(new URLSearchParams(body));
        setTimeout(() => {
          res.writeHead(options.tokenStatus ?? 200, {
            "Content-Type": "application/json",
          });
          res.end(
            JSON.stringify(
              (typeof options.tokenResponse === "function"
                ? options.tokenResponse(tokenRequests.at(-1)!)
                : options.tokenResponse) ?? {
                access_token: "fake-access-token",
                refresh_token: "fake-refresh-token",
                token_type: "Bearer",
                expires_in: 3600,
              },
            ),
          );
        }, options.tokenDelayMs ?? 0);
      });
      return;
    }
    if (req.method === "GET" && req.url === "/identity") {
      identityRequestHeaders.push(req.headers);
      identityAuthorization.push(req.headers.authorization ?? "");
      res.writeHead(options.identityStatus ?? 200, {
        "Content-Type": "application/json",
      });
      res.end(
        JSON.stringify(
          options.identity ?? {
            userId: "fake-provider-user",
            displayName: "Fake Provider User",
          },
        ),
      );
      return;
    }
    res.writeHead(404).end();
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  providerServers.push(server);
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("Fake provider did not start");
  }
  return {
    origin: `http://127.0.0.1:${address.port}`,
    tokenRequests,
    tokenRequestHeaders,
    identityAuthorization,
    identityRequestHeaders,
    revocationRequests,
  };
}

describe("OAuth authentication over HTTP", () => {
  it("projects an Anonymous User from /api/auth/current without a Login Session", async () => {
    const provider: OAuthProviderAdapter = {
      authorizationUrl() {
        throw new Error("not used");
      },
      async exchangeAuthorizationCode() {
        throw new Error("not used");
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);

    const response = await fetch(`${origin}/api/auth/current`);

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ status: "anonymous" });
  });

  it("starts a browser-bound login with a strong one-time state and fixed redirect URI", async () => {
    const authorizationInputs: Array<{ state: string; redirectUri: string }> = [];
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        authorizationInputs.push(input);
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("response_type", "code");
        url.searchParams.set("state", input.state);
        url.searchParams.set("redirect_uri", input.redirectUri);
        return url;
      },
      async exchangeAuthorizationCode() {
        throw new Error("not used");
      },
    };
    const { origin } = await startOAuthServer(provider);

    const response = await fetch(
      `${origin}/api/auth/login?returnTo=${encodeURIComponent("/chat?session=one")}`,
      { redirect: "manual" },
    );

    expect(response.status).toBe(303);
    const location = new URL(response.headers.get("location")!);
    expect(location.origin).toBe("https://provider.example.test");
    expect(location.searchParams.get("state")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(location.searchParams.get("redirect_uri")).toBe(
      "https://dano.example.test/api/auth/callback",
    );
    expect(location.searchParams.has("code_challenge")).toBe(false);
    expect(authorizationInputs).toEqual([
      {
        state: location.searchParams.get("state"),
        redirectUri: "https://dano.example.test/api/auth/callback",
      },
    ]);
    expect(response.headers.get("set-cookie")).toMatch(
      /^dano_oauth_flow=[A-Za-z0-9_-]{43}; Path=\/; HttpOnly; Secure; SameSite=Lax$/,
    );
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
  });

  it("atomically creates a persistent opaque Login Session and projects only External Identity", async () => {
    const exchanges: Array<{
      code: string;
      state: string;
      redirectUri: string;
    }> = [];
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("state", input.state);
        url.searchParams.set("redirect_uri", input.redirectUri);
        return url;
      },
      async exchangeAuthorizationCode(input) {
        exchanges.push(input);
        return {
          identity: {
            userId: "opaque/external/user",
            displayName: "Example User",
            avatarUrl: "https://images.example.test/avatar.png",
          },
          credential: {
            accessToken: "fixture-access-secret",
            refreshToken: "fixture-refresh-secret",
            tokenType: "Bearer",
            expiresAt: 2_000_000_000_000,
          },
        };
      },
    };
    const firstServer = await startOAuthServer(provider);
    const started = await fetch(`${firstServer.origin}/api/auth/login?returnTo=/chat`, {
      redirect: "manual",
    });
    const flowCookie = cookieFrom(started, "dano_oauth_flow");
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${firstServer.origin}/api/auth/callback?code=fixture-code&state=${encodeURIComponent(state)}`,
      { headers: { Cookie: flowCookie }, redirect: "manual" },
    );

    expect(callback.status).toBe(303);
    expect(callback.headers.get("location")).toBe("/chat");
    expect(callback.headers.get("cache-control")).toBe("no-store");
    expect(callback.headers.get("referrer-policy")).toBe("no-referrer");
    expect(callback.headers.get("set-cookie")).toMatch(
      /^dano_login=[A-Za-z0-9_-]{43}; Path=\/; HttpOnly; Secure; SameSite=Lax; Max-Age=604800$/,
    );
    expect(exchanges).toEqual([
      {
        code: "fixture-code",
        state,
        redirectUri: "https://dano.example.test/api/auth/callback",
      },
    ]);

    const loginCookie = cookieFrom(callback, "dano_login");
    const current = await fetch(`${firstServer.origin}/api/auth/current`, {
      headers: { Cookie: loginCookie },
    });
    expect(await current.json()).toEqual({
      status: "authenticated",
      user: {
        id: expect.stringMatching(/^oauth_[a-f0-9]{64}$/),
        username: "Example User",
        avatarUrl: "https://images.example.test/avatar.png",
      },
    });
    const currentText = await (
      await fetch(`${firstServer.origin}/api/auth/current`, {
        headers: { Cookie: loginCookie },
      })
    ).text();
    expect(currentText).not.toContain("opaque/external/user");
    expect(currentText).not.toContain("fixture-access-secret");
    expect(currentText).not.toContain("fixture-refresh-secret");

    const persisted = fs
      .readdirSync(firstServer.runtimeRootPath, {
        recursive: true,
        encoding: "utf8",
      })
      .filter(entry => entry.endsWith(".json"))
      .map(entry =>
        fs.readFileSync(path.join(firstServer.runtimeRootPath, entry), "utf8"),
      )
      .join("\n");
    expect(persisted).not.toContain("fixture-access-secret");
    expect(persisted).not.toContain("fixture-refresh-secret");

    await firstServer.controller.stop();
    await firstServer.authentication.dispose();
    const restarted = await startOAuthServer(
      provider,
      firstServer.runtimeRootPath,
    );
    const restored = await fetch(`${restarted.origin}/api/auth/current`, {
      headers: { Cookie: loginCookie },
    });
    expect(await restored.json()).toMatchObject({ status: "authenticated" });
  });

  it("keeps client creation and existing-client authentication on the same Login Session contract", async () => {
    const { authentication, origin } = await startOAuthServer(
      successfulProvider("shared-resolution-user", "shared-resolution-token"),
    );

    await expect(authentication.resolveForClient!({})).resolves.toBeNull();
    await expect(authentication.resolveExisting!({})).resolves.toBeNull();
    await expect(authentication.resolveAuthSessionState({})).resolves.toBeNull();

    const loginCookie = await completeLogin(origin);
    const headers = { cookie: loginCookie };
    const createdClientResolution = await authentication.resolveForClient!(
      headers,
    );
    const existingClientResolution = await authentication.resolveExisting!(
      headers,
    );
    const authenticatedSessionState =
      await authentication.resolveAuthSessionState(headers);

    expect(createdClientResolution).toEqual(existingClientResolution);
    expect(createdClientResolution).toMatchObject({
      authentication: {
        status: "authenticated",
        user: { username: "已登录用户" },
      },
      loginSessionId: loginCookie.slice("dano_login=".length),
      userContext: {
        user: { username: "已登录用户" },
      },
    });
    expect(authenticatedSessionState).toMatchObject({
      status: "authenticated",
      loginSessionId: loginCookie.slice("dano_login=".length),
      userContext: { user: { username: "已登录用户" } },
    });

    await authentication.requireReauthentication(
      loginCookie.slice("dano_login=".length),
    );
    const reauthenticationError = {
      status: 401,
      message: "Dano Login Session requires reauthentication",
    };
    await expect(
      authentication.resolveForClient!(headers),
    ).rejects.toMatchObject(reauthenticationError);
    await expect(
      authentication.resolveExisting!(headers),
    ).rejects.toMatchObject(reauthenticationError);
    await expect(
      authentication.resolveAuthSessionState(headers),
    ).resolves.toEqual({
      status: "reauth_required",
      loginSessionId: loginCookie.slice("dano_login=".length),
    });
  });

  it("preserves an explicitly allowed HTTP authorization origin", () => {
    const provider = createOAuth2ProviderAdapter({
      issuer: "https://provider.example.test",
      authorizationEndpoint:
        "http://provider-browser.example.test:90/system/oauth2/authorize",
      tokenEndpoint: "https://provider.example.test/token",
      identityEndpoint: "https://provider.example.test/identity",
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "profile",
      allowInsecureAuthorizationEndpoint: true,
    });

    const authorizationUrl = provider.authorizationUrl({
      state: "state-1",
      redirectUri: "https://dano.example.test/api/auth/callback",
    });

    expect(authorizationUrl.origin).toBe(
      "http://provider-browser.example.test:90",
    );
    expect(authorizationUrl.pathname).toBe("/system/oauth2/authorize");
  });

  it("uses openid-client for the confidential Authorization Code exchange without PKCE", async () => {
    const identityFixture = JSON.parse(
      fs.readFileSync(
        new URL("./fixtures/oauth-external-identity.json", import.meta.url),
        "utf8",
      ),
    ) as Record<string, unknown>;
    const fakeProvider = await startFakeProvider({ identity: identityFixture });
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "profile offline_access",
      allowInsecureRequests: true,
    });
    const { origin } = await startOAuthServer(provider);
    expect(provider.revokeCredential).toBeUndefined();
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const authorizationUrl = new URL(started.headers.get("location")!);
    const state = authorizationUrl.searchParams.get("state")!;
    expect(authorizationUrl.searchParams.get("client_id")).toBe("fake-client");
    expect(authorizationUrl.searchParams.get("scope")).toBe(
      "profile offline_access",
    );
    expect(authorizationUrl.searchParams.has("code_challenge")).toBe(false);

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fake-code&state=${encodeURIComponent(state)}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.status).toBe(303);
    expect(cookieFrom(callback, "dano_login")).toMatch(/^dano_login=/);
    expect(fakeProvider.tokenRequests).toHaveLength(1);
    expect(fakeProvider.tokenRequests[0]?.get("grant_type")).toBe(
      "authorization_code",
    );
    expect(fakeProvider.tokenRequests[0]?.get("code")).toBe("fake-code");
    expect(fakeProvider.tokenRequests[0]?.get("redirect_uri")).toBe(
      "https://dano.example.test/api/auth/callback",
    );
    expect(fakeProvider.tokenRequests[0]?.has("code_verifier")).toBe(false);
    expect(fakeProvider.tokenRequests[0]?.get("client_id")).toBe("fake-client");
    expect(fakeProvider.tokenRequests[0]?.get("client_secret")).toBe(
      "fake-client-secret",
    );
    expect(fakeProvider.identityAuthorization).toEqual([
      "Bearer fake-access-token",
      "Bearer fake-access-token",
    ]);
    const current = await fetch(`${origin}/api/auth/current`, {
      headers: { Cookie: cookieFrom(callback, "dano_login") },
    });
    expect(await current.json()).toEqual({
      status: "authenticated",
      user: {
        id: expect.stringMatching(/^oauth_[a-f0-9]{64}$/),
        username: "Fixture User",
        avatarUrl: "https://avatar.invalid/profile.png",
      },
    });
  });

  it("supports HTTP Basic client authentication for the token endpoint", async () => {
    const fakeProvider = await startFakeProvider();
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "basicclient",
      clientSecret: "basicsecret",
      clientAuthMethod: "client_secret_basic",
      scope: "user.read",
      allowInsecureRequests: true,
    });
    const { origin } = await startOAuthServer(provider);
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fake-code&state=${encodeURIComponent(state)}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.status).toBe(303);
    expect(fakeProvider.tokenRequests).toHaveLength(1);
    expect(fakeProvider.tokenRequests[0]?.has("client_id")).toBe(false);
    expect(fakeProvider.tokenRequests[0]?.has("client_secret")).toBe(false);
    expect(fakeProvider.tokenRequestHeaders[0]?.authorization).toBe(
      `Basic ${Buffer.from("basicclient:basicsecret").toString("base64")}`,
    );
  });

  it("normalizes a provider data envelope inside the adapter boundary", async () => {
    const fakeProvider = await startFakeProvider({
      tokenResponse: {
        code: 0,
        data: {
          access_token: "wrapped-access-token",
          refresh_token: "wrapped-refresh-token",
          token_type: "Bearer",
          expires_in: 3600,
        },
      },
      identity: {
        code: 0,
        data: {
          id: 4242,
          nickname: "Wrapped Identity",
          avatar: "https://avatar.invalid/wrapped.png",
          privateProfile: "must-not-cross-the-adapter",
        },
      },
    });
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "user.read",
      requestHeaders: { "x-provider-context": "fixed-context" },
      sendStateToTokenEndpoint: true,
      allowInsecureRequests: true,
    });
    const { origin } = await startOAuthServer(provider);
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=wrapped-code&state=${encodeURIComponent(state)}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(cookieFrom(callback, "dano_login")).toMatch(/^dano_login=/);
    expect(fakeProvider.tokenRequestHeaders[0]?.["x-provider-context"]).toBe(
      "fixed-context",
    );
    expect(fakeProvider.tokenRequests[0]?.get("state")).toBe(state);
    expect(fakeProvider.identityRequestHeaders[0]?.["x-provider-context"]).toBe(
      "fixed-context",
    );
    const current = await fetch(`${origin}/api/auth/current`, {
      headers: { Cookie: cookieFrom(callback, "dano_login") },
    });
    expect(await current.json()).toEqual({
      status: "authenticated",
      user: {
        id: expect.stringMatching(/^oauth_[a-f0-9]{64}$/),
        username: "Wrapped Identity",
        avatarUrl: "https://avatar.invalid/wrapped.png",
      },
    });
  });

  it("recognizes a successful HTTP response whose provider wrapper rejects the access token", async () => {
    const provider = createOAuth2ProviderAdapter({
      issuer: "https://provider.test",
      authorizationEndpoint: "https://provider.test/authorize",
      tokenEndpoint: "https://provider.test/token",
      identityEndpoint: "https://provider.test/identity",
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "user.read",
    });

    await expect(
      provider.isAccessTokenInvalid?.(
        new Response('{"code":401,"data":null}', { status: 200 }),
      ),
    ).resolves.toBe(true);
  });

  it("recognizes transport and string wrapper failures while preserving provider responses", async () => {
    const provider = createOAuth2ProviderAdapter({
      issuer: "https://provider.test",
      authorizationEndpoint: "https://provider.test/authorize",
      tokenEndpoint: "https://provider.test/token",
      identityEndpoint: "https://provider.test/identity",
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "user.read",
    });
    const cases = [
      {
        response: new Response("transport unauthorized", { status: 401 }),
        expected: true,
      },
      {
        response: new Response('{"code":"401","data":null}'),
        expected: true,
      },
      {
        response: new Response('{"code":0,"data":{"value":"ok"}}'),
        expected: false,
      },
      { response: new Response("plain provider response"), expected: false },
    ];

    for (const testCase of cases) {
      const originalBody = await testCase.response.clone().text();
      await expect(
        provider.isAccessTokenInvalid?.(testCase.response),
      ).resolves.toBe(testCase.expected);
      await expect(testCase.response.text()).resolves.toBe(originalBody);
    }
  });

  it("uses openid-client to refresh and retains a refresh token omitted during rotation", async () => {
    const fakeProvider = await startFakeProvider({
      tokenResponse(request: URLSearchParams) {
        return request.get("grant_type") === "refresh_token"
          ? {
              access_token: "renewed-access-token",
              token_type: "Bearer",
              expires_in: 1800,
            }
          : {
              access_token: "initial-access-token",
              refresh_token: "initial-refresh-token",
              token_type: "Bearer",
              expires_in: 3600,
            };
      },
    });
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "profile offline_access",
      requestHeaders: { "x-provider-context": "fixed-context" },
      allowInsecureRequests: true,
    });

    const refreshed = await provider.refreshCredential?.({
      accessToken: "expired-access-token",
      refreshToken: "initial-refresh-token",
      tokenType: "Bearer",
    });
    await provider.validateCredential?.(refreshed!);

    expect(fakeProvider.tokenRequests).toHaveLength(1);
    expect(fakeProvider.tokenRequests[0]?.get("grant_type")).toBe("refresh_token");
    expect(fakeProvider.tokenRequests[0]?.get("refresh_token")).toBe(
      "initial-refresh-token",
    );
    expect(refreshed).toMatchObject({
      accessToken: "renewed-access-token",
      refreshToken: "initial-refresh-token",
      tokenType: "bearer",
      expiresAt: expect.any(Number),
    });
    expect(fakeProvider.identityAuthorization).toEqual([
      "Bearer renewed-access-token",
    ]);
    expect(fakeProvider.identityRequestHeaders[0]?.["x-provider-context"]).toBe(
      "fixed-context",
    );
  });

  it("rejects a refreshed credential when the provider identity contract is invalid", async () => {
    const fakeProvider = await startFakeProvider({
      tokenResponse: {
        access_token: "renewed-access-secret",
        token_type: "Bearer",
        expires_in: 1800,
      },
      identity: { code: 401, data: null },
    });
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "profile offline_access",
      requestHeaders: { "x-provider-context": "fixed-context" },
      allowInsecureRequests: true,
    });

    let rejection: unknown;
    try {
      const refreshed = await provider.refreshCredential?.({
        accessToken: "expired-access-secret",
        refreshToken: "initial-refresh-secret",
        tokenType: "Bearer",
      });
      await provider.validateCredential?.(refreshed!);
    } catch (error) {
      rejection = error;
    }

    expect(rejection).toBeInstanceOf(OAuthProviderContractError);
    expect(String(rejection)).not.toContain("renewed-access-secret");
    expect(String(rejection)).not.toContain("initial-refresh-secret");
    expect(fakeProvider.identityAuthorization).toEqual([
      "Bearer renewed-access-secret",
    ]);
    expect(fakeProvider.identityRequestHeaders[0]?.["x-provider-context"]).toBe(
      "fixed-context",
    );
  });

  it("rejects a refreshed credential when the provider identity request fails", async () => {
    const fakeProvider = await startFakeProvider({
      tokenResponse: {
        access_token: "renewed-http-failure-secret",
        token_type: "Bearer",
      },
      identityStatus: 401,
    });
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "profile offline_access",
      allowInsecureRequests: true,
    });

    let rejection: unknown;
    try {
      const refreshed = await provider.refreshCredential?.({
        accessToken: "expired-http-failure-secret",
        refreshToken: "refresh-http-failure-secret",
      });
      await provider.validateCredential?.(refreshed!);
    } catch (error) {
      rejection = error;
    }

    expect(rejection).toBeInstanceOf(Error);
    expect((rejection as Error).message).toBe("Provider identity request failed");
    expect(String(rejection)).not.toMatch(
      /renewed-http-failure-secret|refresh-http-failure-secret/,
    );
    expect(fakeProvider.identityAuthorization).toEqual([
      "Bearer renewed-http-failure-secret",
    ]);
  });

  it("rejects a refreshed non-Bearer credential without sending the wrong scheme", async () => {
    const fakeProvider = await startFakeProvider({
      tokenResponse: {
        access_token: "renewed-non-bearer-secret",
        token_type: "DPoP",
      },
    });
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "profile offline_access",
      allowInsecureRequests: true,
    });

    let rejection: unknown;
    try {
      const refreshed = await provider.refreshCredential?.({
        accessToken: "expired-non-bearer-secret",
        refreshToken: "refresh-non-bearer-secret",
      });
      await provider.validateCredential?.(refreshed!);
    } catch (error) {
      rejection = error;
    }

    expect(rejection).toBeInstanceOf(Error);
    expect(String(rejection)).not.toMatch(
      /renewed-non-bearer-secret|refresh-non-bearer-secret/,
    );
    expect(fakeProvider.identityAuthorization).toEqual([]);
  });

  it("supports a provider DELETE revocation contract without exposing the credential", async () => {
    const fakeProvider = await startFakeProvider();
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      revocation: {
        transport: "delete-query-basic",
        endpoint: `${fakeProvider.origin}/revoke`,
      },
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "user.read",
      requestHeaders: { "x-provider-context": "fixed-context" },
      allowInsecureRequests: true,
    });

    await provider.revokeCredential?.({
      accessToken: "access-token-to-revoke",
      refreshToken: "refresh-token-must-not-be-sent",
    });

    expect(fakeProvider.revocationRequests).toEqual([
      {
        method: "DELETE",
        token: "access-token-to-revoke",
        authorization: `Basic ${Buffer.from(
          "fake-client:fake-client-secret",
        ).toString("base64")}`,
        providerContext: "fixed-context",
      },
    ]);
  });

  it("atomically consumes state and rejects replay or another browser binding", async () => {
    let exchanges = 0;
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("state", input.state);
        return url;
      },
      async exchangeAuthorizationCode() {
        exchanges += 1;
        return {
          identity: { userId: "state-user" },
          credential: { accessToken: "state-access" },
        };
      },
    };
    const { origin } = await startOAuthServer(provider);
    const first = await fetch(`${origin}/api/auth/login`, { redirect: "manual" });
    const firstState = new URL(first.headers.get("location")!).searchParams.get(
      "state",
    )!;
    const firstFlowCookie = cookieFrom(first, "dano_oauth_flow");
    const wrongBrowser = await fetch(
      `${origin}/api/auth/callback?code=wrong-browser&state=${firstState}`,
      {
        headers: { Cookie: `dano_oauth_flow=${"A".repeat(43)}` },
        redirect: "manual",
      },
    );
    expect(wrongBrowser.headers.get("set-cookie")).toBeNull();
    expect(exchanges).toBe(0);

    const second = await fetch(`${origin}/api/auth/login`, {
      headers: { Cookie: firstFlowCookie },
      redirect: "manual",
    });
    const secondState = new URL(second.headers.get("location")!).searchParams.get(
      "state",
    )!;
    const accepted = await fetch(
      `${origin}/api/auth/callback?code=accepted&state=${secondState}`,
      { headers: { Cookie: firstFlowCookie }, redirect: "manual" },
    );
    expect(cookieFrom(accepted, "dano_login")).toMatch(/^dano_login=/);
    const replay = await fetch(
      `${origin}/api/auth/callback?code=replay&state=${secondState}`,
      { headers: { Cookie: firstFlowCookie }, redirect: "manual" },
    );
    expect(replay.headers.get("set-cookie")).toBeNull();
    expect(exchanges).toBe(1);
  });

  it("expires short-lived state using the configured clock", async () => {
    let currentTime = 10_000;
    let exchanges = 0;
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("state", input.state);
        return url;
      },
      async exchangeAuthorizationCode() {
        exchanges += 1;
        throw new Error("expired state must not reach provider");
      },
    };
    const { origin } = await startOAuthServer(provider, undefined, {
      now: () => currentTime,
      stateTtlMs: 1_000,
    });
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;
    currentTime += 1_000;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=late&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.headers.get("set-cookie")).toBeNull();
    expect(exchanges).toBe(0);
  });

  it("limits parallel login transactions for one browser", async () => {
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("state", input.state);
        return url;
      },
      async exchangeAuthorizationCode() {
        throw new Error("not used");
      },
    };
    const { origin } = await startOAuthServer(provider, undefined, {
      maxPendingTransactions: 2,
    });
    const first = await fetch(`${origin}/api/auth/login`, { redirect: "manual" });
    const flowCookie = cookieFrom(first, "dano_oauth_flow");
    const second = await fetch(`${origin}/api/auth/login`, {
      headers: { Cookie: flowCookie },
      redirect: "manual",
    });
    const blocked = await fetch(`${origin}/api/auth/login`, {
      headers: { Cookie: flowCookie },
      redirect: "manual",
    });

    expect(first.status).toBe(303);
    expect(second.status).toBe(303);
    expect(blocked.status).toBe(429);
  });

  it("binds login state to the existing Anonymous User browser Cookie", async () => {
    const provider = successfulProvider("guest-bound-user", "guest-bound-token");
    const { origin } = await startOAuthServer(provider);
    const anonymous = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const guestCookie = cookieFrom(anonymous, "dano_guest");

    const started = await fetch(`${origin}/api/auth/login`, {
      headers: { Cookie: guestCookie },
      redirect: "manual",
    });
    expect(started.headers.get("set-cookie")).toBeNull();
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;
    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      { headers: { Cookie: guestCookie }, redirect: "manual" },
    );

    expect(cookieFrom(callback, "dano_login")).toMatch(/^dano_login=/);
  });

  it("atomically transfers only the callback-bound Anonymous User data before login", async () => {
    let now = 1_000;
    const provider = successfulProvider("transfer-owner", "transfer-token");
    const { origin } = await startOAuthServer(provider, undefined, {}, {
      idleTtlMs: 1_000,
      intervalMs: 10,
      now: () => now,
    });
    const anonymous = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const anonymousBody = (await anonymous.json()) as {
      client: { id: string };
      defaultWorkspacePath: string;
    };
    const guestCookie = cookieFrom(anonymous, "dano_guest");
    fs.writeFileSync(
      path.join(anonymousBody.defaultWorkspacePath, "guest-note.txt"),
      "owned by the callback guest",
      "utf8",
    );
    const savedPreference = await fetch(
      `${origin}/api/clients/${anonymousBody.client.id}/preferences/theme`,
      {
        method: "PUT",
        headers: {
          Cookie: guestCookie,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ accentColorPreset: "purple" }),
      },
    );
    expect(savedPreference.status).toBe(200);

    const started = await fetch(`${origin}/api/auth/login?returnTo=/chat`, {
      headers: { Cookie: guestCookie },
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;
    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      { headers: { Cookie: guestCookie }, redirect: "manual" },
    );
    const loginCookie = cookieFrom(callback, "dano_login");
    const authenticated = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { Cookie: loginCookie, "Content-Type": "application/json" },
      body: "{}",
    });
    const authenticatedBody = (await authenticated.json()) as {
      client: { id: string };
      defaultWorkspacePath: string;
    };

    expect(authenticated.status).toBe(201);
    expect(
      fs.readFileSync(
        path.join(authenticatedBody.defaultWorkspacePath, "guest-note.txt"),
        "utf8",
      ),
    ).toBe("owned by the callback guest");
    const transferredPreference = await fetch(
      `${origin}/api/clients/${authenticatedBody.client.id}/preferences/theme`,
      { headers: { Cookie: loginCookie } },
    );
    expect(await transferredPreference.json()).toEqual({
      accentColorPreset: "purple",
    });

    const staleGuest = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { Cookie: guestCookie, "Content-Type": "application/json" },
      body: "{}",
    });
    const staleGuestBody = (await staleGuest.json()) as {
      client: { id: string };
      defaultWorkspacePath: string;
    };
    expect(staleGuest.headers.get("set-cookie")).toMatch(/^dano_guest=/);
    expect(staleGuestBody.defaultWorkspacePath).not.toBe(
      authenticatedBody.defaultWorkspacePath,
    );
    expect(
      fs.existsSync(path.join(staleGuestBody.defaultWorkspacePath, "guest-note.txt")),
    ).toBe(false);
    await fetch(
      `${origin}/api/clients/${authenticatedBody.client.id}/disconnect`,
      { method: "POST", headers: { Cookie: loginCookie }, body: "{}" },
    );
    const replacementGuestCookie = cookieFrom(staleGuest, "dano_guest");
    await fetch(`${origin}/api/clients/${staleGuestBody.client.id}/disconnect`, {
      method: "POST",
      headers: { Cookie: replacementGuestCookie },
      body: "{}",
    });
    now = 2_001;
    await vi.waitFor(
      () =>
        expect(fs.existsSync(staleGuestBody.defaultWorkspacePath)).toBe(false),
      { timeout: 2_000, interval: 10 },
    );
    expect(
      fs.readFileSync(
        path.join(authenticatedBody.defaultWorkspacePath, "guest-note.txt"),
        "utf8",
      ),
    ).toBe("owned by the callback guest");
  });

  it("rolls back a failed Anonymous User transfer and keeps the guest usable", async () => {
    const externalUserId = "rollback-owner";
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider(externalUserId, "rollback-token"),
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const existingCookie = await completeLogin(origin, "existing");
    const anonymous = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const anonymousBody = (await anonymous.json()) as {
      client: { id: string };
      defaultWorkspacePath: string;
    };
    const guestCookie = cookieFrom(anonymous, "dano_guest");
    const retainedPath = path.join(
      anonymousBody.defaultWorkspacePath,
      "a-retained.txt",
    );
    fs.writeFileSync(retainedPath, "guest remains owner", "utf8");
    const unavailableUpload = await uploadProjectFile(
      origin,
      anonymousBody as TestBridgeClient,
      guestCookie,
      "unavailable.txt",
      "removed before owner transfer",
    );
    fs.rmSync(unavailableUpload.path);
    const started = await fetch(`${origin}/api/auth/login`, {
      headers: { Cookie: guestCookie },
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      { headers: { Cookie: guestCookie }, redirect: "manual" },
    );

    expect(callback.headers.get("set-cookie")).toMatch(
      /^dano_auth_error=[A-Za-z0-9_-]{43};/,
    );
    expect(revoked).toEqual([]);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(1);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: existingCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
    expect(fs.readFileSync(retainedPath, "utf8")).toBe("guest remains owner");
    const canonicalUserId = `oauth_${createHash("sha256")
      .update(externalUserId)
      .digest("hex")}`;
    expect(
      fs.existsSync(
        path.join(
          runtimeRootPath,
          "users",
          canonicalUserId,
          "workspaces",
          "default",
          "a-retained.txt",
        ),
      ),
    ).toBe(false);
    expect(
      (
        await fetch(
          `${origin}/api/clients/${anonymousBody.client.id}/preferences/theme`,
          { headers: { Cookie: guestCookie } },
        )
      ).status,
    ).toBe(200);
  });

  it("does not revoke an uncommitted Credential when validation cannot establish its User", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("validation-failure-user", "candidate-token"),
      async validateCredential() {
        throw new Error("fixture identity validation unavailable");
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.headers.get("set-cookie")).toMatch(/^dano_auth_error=/);
    expect(revoked).toEqual([]);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toEqual([]);
  });

  it("does not cascade revoke a failed candidate while the same User remains active", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("validation-shared-user", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: { userId: "validation-shared-user" },
          credential: { accessToken: `access-${code}` },
        };
      },
      async validateCredential(credential) {
        if (credential.accessToken === "access-candidate") {
          throw new Error("fixture identity validation unavailable");
        }
        return { userId: "validation-shared-user" };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const existingCookie = await completeLogin(origin, "existing");
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=candidate&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.headers.get("set-cookie")).toMatch(/^dano_auth_error=/);
    expect(revoked).toEqual([]);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(1);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: existingCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
  });

  it("never revokes a Credential whose validated User differs from exchange", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("validated-owner-b", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: {
            userId:
              code === "candidate"
                ? "claimed-owner-a"
                : "validated-owner-b",
          },
          credential: { accessToken: `access-${code}` },
        };
      },
      async validateCredential() {
        return { userId: "validated-owner-b" };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const existingCookie = await completeLogin(origin, "existing");
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=candidate&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.headers.get("set-cookie")).toMatch(/^dano_auth_error=/);
    expect(revoked).toEqual([]);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(1);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: existingCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
  });

  it("merges guest sessions without replacing an authenticated User's existing data", async () => {
    const provider = successfulProvider("merge-owner", "merge-token");
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const existingLoginCookie = await completeLogin(origin, "existing-login");
    const existingClient = await createAuthenticatedClient(
      origin,
      existingLoginCookie,
    );
    expect(
      (
        await fetch(
          `${origin}/api/clients/${existingClient.client.id}/preferences/theme`,
          {
            method: "PUT",
            headers: {
              Cookie: existingLoginCookie,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ accentColorPreset: "blue" }),
          },
        )
      ).status,
    ).toBe(200);
    fs.writeFileSync(
      path.join(existingClient.defaultWorkspacePath, "shared.txt"),
      "authenticated value",
      "utf8",
    );
    const existingState = await executeCommand(
      origin,
      existingClient,
      existingLoginCookie,
      { id: "existing-state", type: "get_state" },
    );
    const existingSession = (
      existingState.payload as {
        data?: { sessionFile?: string; sessionId?: string };
      }
    ).data;
    const existingSessionPath = existingSession?.sessionFile;
    expect(existingSessionPath).toBeTruthy();
    fs.writeFileSync(
      existingSessionPath!,
      `${JSON.stringify({
        type: "session",
        id: existingSession?.sessionId,
        timestamp: "2026-08-11T00:00:00.000Z",
        cwd: existingClient.defaultWorkspacePath,
      })}\n`,
      "utf8",
    );

    const anonymous = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const guestClient = (await anonymous.json()) as TestBridgeClient;
    const guestCookie = cookieFrom(anonymous, "dano_guest");
    fs.writeFileSync(
      path.join(guestClient.defaultWorkspacePath, "shared.txt"),
      "guest value",
      "utf8",
    );
    const binaryValue = Buffer.from([0, 255, 254, 128, 65, 0]);
    fs.writeFileSync(
      path.join(guestClient.defaultWorkspacePath, "binary.bin"),
      binaryValue,
    );
    expect(
      (
        await fetch(
          `${origin}/api/clients/${guestClient.client.id}/preferences/theme`,
          {
            method: "PUT",
            headers: {
              Cookie: guestCookie,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ accentColorPreset: "purple" }),
          },
        )
      ).status,
    ).toBe(200);
    const guestState = await executeCommand(origin, guestClient, guestCookie, {
      id: "guest-state",
      type: "get_state",
    });
    const guestSession = (
      guestState.payload as {
        data?: { sessionFile?: string; sessionId?: string };
      }
    ).data;
    const guestSessionPath = guestSession?.sessionFile;
    expect(guestSessionPath).toBeTruthy();
    fs.writeFileSync(
      guestSessionPath!,
      `${JSON.stringify({
        type: "session",
        id: guestSession?.sessionId,
        timestamp: "2026-08-11T00:00:00.000Z",
        cwd: guestClient.defaultWorkspacePath,
      })}\n`,
      "utf8",
    );
    const guestUpload = await uploadProjectFile(
      origin,
      guestClient,
      guestCookie,
      "guest-upload.txt",
      "guest upload keeps its resource id",
    );

    const started = await fetch(`${origin}/api/auth/login`, {
      headers: { Cookie: guestCookie },
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;
    const callback = await fetch(
      `${origin}/api/auth/callback?code=merge-login&state=${state}`,
      { headers: { Cookie: guestCookie }, redirect: "manual" },
    );
    expect(cookieFrom(callback, "dano_login")).toMatch(/^dano_login=/);

    expect(
      fs.readFileSync(
        path.join(existingClient.defaultWorkspacePath, "shared.txt"),
        "utf8",
      ),
    ).toBe("authenticated value");
    expect(
      fs.readFileSync(
        path.join(
          existingClient.defaultWorkspacePath,
          "shared.anonymous-1.txt",
        ),
        "utf8",
      ),
    ).toBe("guest value");
    expect(
      fs.readFileSync(
        path.join(existingClient.defaultWorkspacePath, "binary.bin"),
      ),
    ).toEqual(binaryValue);
    const mergedPreference = await fetch(
      `${origin}/api/clients/${existingClient.client.id}/preferences/theme`,
      { headers: { Cookie: existingLoginCookie } },
    );
    expect(await mergedPreference.json()).toEqual({
      accentColorPreset: "blue",
    });
    expect(
      fs.readdirSync(
        path.join(
          path.dirname(path.dirname(existingClient.defaultWorkspacePath)),
          "preferences",
        ),
      ),
    ).toEqual(["theme.json"]);
    const sessions = await executeCommand(
      origin,
      existingClient,
      existingLoginCookie,
      {
        id: "merged-sessions",
        type: "list_sessions",
        workspacePath: existingClient.defaultWorkspacePath,
      },
    );
    const sessionPaths = (
      sessions.payload as { data?: { sessions?: Array<{ path: string }> } }
    ).data?.sessions?.map(session => session.path) ?? [];
    expect(sessionPaths).toHaveLength(2);
    expect(
      sessionPaths.map(sessionPath =>
        (JSON.parse(fs.readFileSync(sessionPath, "utf8")) as { id: string }).id,
      ),
    ).toEqual(
      expect.arrayContaining([
        existingSession?.sessionId,
        guestSession?.sessionId,
      ]),
    );
    expect(
      sessionPaths.every(sessionPath =>
        fs
          .readFileSync(sessionPath, "utf8")
          .includes(existingClient.defaultWorkspacePath),
      ),
    ).toBe(true);
    const uploadRecords = fs
      .readdirSync(path.join(runtimeRootPath, "uploads", "records"))
      .map(name =>
        JSON.parse(
          fs.readFileSync(
            path.join(runtimeRootPath, "uploads", "records", name),
            "utf8",
          ),
        ) as {
          upload: { id: string; ownerUserId: string; path: string };
        },
      );
    const transferredUpload = uploadRecords.find(
      record => record.upload.id === guestUpload.id,
    )?.upload;
    expect(transferredUpload).toMatchObject({
      id: guestUpload.id,
      ownerUserId: expect.stringMatching(/^oauth_/),
    });
    expect(transferredUpload?.path.startsWith(existingClient.defaultWorkspacePath)).toBe(
      true,
    );
    expect(fs.readFileSync(transferredUpload!.path, "utf8")).toBe(
      "guest upload keeps its resource id",
    );
  });

  it("rejects cross-origin and scheme-relative return paths", async () => {
    const provider: OAuthProviderAdapter = {
      authorizationUrl() {
        throw new Error("invalid return path must not reach provider");
      },
      async exchangeAuthorizationCode() {
        throw new Error("not used");
      },
    };
    const { origin } = await startOAuthServer(provider);

    for (const returnTo of ["https://outside.example.test/path", "//outside.test"]) {
      const response = await fetch(
        `${origin}/api/auth/login?returnTo=${encodeURIComponent(returnTo)}`,
        { redirect: "manual" },
      );
      expect(response.status).toBe(400);
    }
  });

  it("consumes provider denial without creating a Login Session", async () => {
    let exchanges = 0;
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("state", input.state);
        return url;
      },
      async exchangeAuthorizationCode() {
        exchanges += 1;
        throw new Error("denial must not exchange a code");
      },
    };
    const { origin } = await startOAuthServer(provider);
    const started = await fetch(`${origin}/api/auth/login?returnTo=/denied`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const denied = await fetch(
      `${origin}/api/auth/callback?error=access_denied&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(denied.status).toBe(303);
    expect(denied.headers.get("location")).toBe("/denied");
    expect(denied.headers.get("set-cookie")).toBeNull();
    expect(exchanges).toBe(0);
  });

  it("leaves no partial state after an invalid code", async () => {
    const setup = {
      tokenStatus: 400,
      tokenResponse: { error: "invalid_grant" },
    };
    const fakeProvider = await startFakeProvider(setup);
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "failure-client",
      clientSecret: "failure-secret",
      scope: "profile",
      allowInsecureRequests: true,
    });
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const started = await fetch(`${origin}/api/auth/login?returnTo=/failure`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.status).toBe(303);
    expect(callback.headers.get("location")).toBe("/failure");
    const authErrorCookie = cookieFrom(callback, "dano_auth_error");
    const current = await fetch(`${origin}/api/auth/current`, {
      headers: { Cookie: authErrorCookie },
    });
    expect(await current.json()).toEqual({
      status: "anonymous",
      loginError: { code: "provider_login_failed" },
    });
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toEqual([]);
  });

  it("rejects an invalid provider identity with a sanitized contract error", async () => {
    const fakeProvider = await startFakeProvider({
      identity: {
        displayName: "Missing Identifier",
      },
    });
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "contract-client",
      clientSecret: "contract-secret",
      scope: "profile",
      allowInsecureRequests: true,
    });
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const started = await fetch(`${origin}/api/auth/login?returnTo=/failure`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.status).toBe(303);
    expect(callback.headers.get("location")).toBe("/failure");
    expect(callback.headers.get("location")).not.toMatch(/code=|state=/);
    expect(callback.headers.get("cache-control")).toBe("no-store");
    expect(callback.headers.get("referrer-policy")).toBe("no-referrer");
    const authErrorCookie = cookieFrom(callback, "dano_auth_error");
    expect(authErrorCookie).toMatch(/^dano_auth_error=[A-Za-z0-9_-]{43}$/);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toEqual([]);
    const firstCurrent = await fetch(`${origin}/api/auth/current`, {
      headers: { Cookie: authErrorCookie },
    });
    expect(await firstCurrent.json()).toEqual({
      status: "anonymous",
      loginError: { code: "provider_identity_invalid" },
    });
    expect(firstCurrent.headers.get("set-cookie")).toMatch(
      /^dano_auth_error=; Path=\/; HttpOnly; Secure; SameSite=Lax; Max-Age=0$/,
    );
    const secondCurrent = await fetch(`${origin}/api/auth/current`, {
      headers: { Cookie: authErrorCookie },
    });
    expect(await secondCurrent.json()).toEqual({ status: "anonymous" });
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-errors")),
    ).toEqual([]);

    const retry = await fetch(`${origin}/api/auth/login?returnTo=/failure`, {
      headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
      redirect: "manual",
    });
    expect(retry.status).toBe(303);
    expect(new URL(retry.headers.get("location")!).pathname).toBe("/authorize");
    await expect(
      provider.exchangeAuthorizationCode({
        code: "another-code",
        state: "another-state",
        redirectUri: "https://dano.example.test/api/auth/callback",
      }),
    ).rejects.toBeInstanceOf(OAuthProviderContractError);
  });

  it("leaves no partial state when credential encryption fails", async () => {
    const credentialEncryptionKey = {
      version: "test-v1",
      key: Buffer.alloc(32, 3) as Uint8Array,
    };
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("state", input.state);
        return url;
      },
      async exchangeAuthorizationCode() {
        return {
          identity: { userId: "encryption-user" },
          credential: { accessToken: "encryption-token" },
        };
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(
      provider,
      undefined,
      { credentialEncryptionKey },
    );
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;
    credentialEncryptionKey.key = Buffer.alloc(31, 3);

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.headers.get("set-cookie")).toMatch(
      /^dano_auth_error=[A-Za-z0-9_-]{43};/,
    );
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toEqual([]);
  });

  it("times out the fake provider without leaving a partial Login Session", async () => {
    const fakeProvider = await startFakeProvider({ tokenDelayMs: 100 });
    const provider = createOAuth2ProviderAdapter({
      issuer: fakeProvider.origin,
      authorizationEndpoint: `${fakeProvider.origin}/authorize`,
      tokenEndpoint: `${fakeProvider.origin}/token`,
      identityEndpoint: `${fakeProvider.origin}/identity`,
      clientId: "fake-client",
      clientSecret: "fake-client-secret",
      scope: "profile",
      timeoutMs: 10,
      allowInsecureRequests: true,
    });
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=slow&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.headers.get("set-cookie")).toMatch(
      /^dano_auth_error=[A-Za-z0-9_-]{43};/,
    );
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toEqual([]);
  });

  it("does not create a partial Login Session when persistent storage fails", async () => {
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("state", input.state);
        return url;
      },
      async exchangeAuthorizationCode() {
        return {
          identity: { userId: "storage-user" },
          credential: { accessToken: "storage-token" },
        };
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;
    const sessionsPath = path.join(
      runtimeRootPath,
      "auth",
      "login-sessions",
    );
    fs.rmdirSync(sessionsPath);
    fs.writeFileSync(sessionsPath, "storage unavailable", { mode: 0o600 });

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );

    expect(callback.headers.get("set-cookie")).toMatch(
      /^dano_auth_error=[A-Za-z0-9_-]{43};/,
    );
    expect(fs.statSync(sessionsPath).isFile()).toBe(true);
  });

  it("removes a Login Session record when publish fails after replacement", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("post-write-failure-user", "post-write-token"),
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const originalChmod = fs.promises.chmod.bind(fs.promises);
    const chmod = vi.spyOn(fs.promises, "chmod").mockImplementation(
      async (target, mode) => {
        await originalChmod(target, mode);
        if (
          String(target).includes(`${path.sep}auth${path.sep}login-sessions`) &&
          String(target).endsWith(".json")
        ) {
          throw new Error("fixture post-replacement failure");
        }
      },
    );
    const started = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=fixture&state=${state}`,
      {
        headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
        redirect: "manual",
      },
    );
    chmod.mockRestore();

    expect(callback.headers.get("set-cookie")).toMatch(/^dano_auth_error=/);
    expect(revoked).toEqual(["post-write-token"]);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toEqual([]);
  });

  it("expires Login Sessions after eight idle hours", async () => {
    let currentTime = 1_000;
    const provider = successfulProvider("idle-user", "idle-token");
    const { origin } = await startOAuthServer(provider, undefined, {
      now: () => currentTime,
    });
    const loginCookie = await completeLogin(origin);

    currentTime += sessionHours(8) - 1;
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: loginCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
    currentTime += sessionHours(8);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: loginCookie },
        })
      ).json(),
    ).toEqual({ status: "anonymous" });
  });

  it("never extends a Login Session beyond seven absolute days", async () => {
    let currentTime = 1_000;
    const provider = successfulProvider("absolute-user", "absolute-token");
    const { origin } = await startOAuthServer(provider, undefined, {
      now: () => currentTime,
    });
    const loginCookie = await completeLogin(origin);
    for (
      currentTime = 1_000 + sessionHours(7);
      currentTime < 1_000 + sessionDays(7);
      currentTime += sessionHours(7)
    ) {
      const current = await fetch(`${origin}/api/auth/current`, {
        headers: { Cookie: loginCookie },
      });
      expect(await current.json()).toMatchObject({ status: "authenticated" });
    }
    currentTime = 1_000 + sessionDays(7);

    const expired = await fetch(`${origin}/api/auth/current`, {
      headers: { Cookie: loginCookie },
    });

    expect(await expired.json()).toEqual({ status: "anonymous" });
  });

  it("removes expired Login Sessions during startup and periodic GC", async () => {
    let currentTime = 1_000;
    const provider = successfulProvider("gc-user", "gc-token");
    const first = await startOAuthServer(provider, undefined, {
      now: () => currentTime,
    });
    const loginCookie = await completeLogin(first.origin);
    await first.controller.stop();
    await first.authentication.dispose();
    currentTime += sessionHours(8);
    const restarted = await startOAuthServer(
      provider,
      first.runtimeRootPath,
      { now: () => currentTime, sessionGcIntervalMs: 10 },
    );
    expect(
      await (
        await fetch(`${restarted.origin}/api/auth/current`, {
          headers: { Cookie: loginCookie },
        })
      ).json(),
    ).toEqual({ status: "anonymous" });

    const freshCookie = await completeLogin(restarted.origin);
    currentTime += sessionHours(8);
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(
      await (
        await fetch(`${restarted.origin}/api/auth/current`, {
          headers: { Cookie: freshCookie },
        })
      ).json(),
    ).toEqual({ status: "anonymous" });
  });

  it("disconnects only the Bridge Client and SSE bound to an idle-expired Login Session", async () => {
    let currentTime = 1_000;
    const provider = successfulProvider("shared-gc-user", "unused");
    const { controller, origin } = await startOAuthServer(provider, undefined, {
      now: () => currentTime,
      sessionIdleTtlMs: 100,
      sessionAbsoluteTtlMs: 1_000,
      sessionGcIntervalMs: 10,
    });
    const expiredCookie = await completeLogin(origin, "expired");
    const retainedCookie = await completeLogin(origin, "retained");
    const expiredClient = await createAuthenticatedClient(origin, expiredCookie);
    const retainedClient = await createAuthenticatedClient(origin, retainedCookie);
    const expiredSse = waitForSseClose(
      `${origin}${expiredClient.eventsUrl}`,
      expiredCookie,
    );
    await expiredSse.ready;
    currentTime += 50;
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: retainedCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });

    currentTime += 50;

    await expect(expiredSse.closed).resolves.toBeUndefined();
    await vi.waitFor(() => {
      expect(controller.getClients()).not.toContainEqual(expiredClient.client);
    });
    expect(controller.getClients()).toContainEqual(retainedClient.client);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: expiredCookie },
        })
      ).json(),
    ).toEqual({ status: "anonymous" });
    expect(
      (
        await fetch(`${origin}${expiredClient.messagesUrl}`, {
          method: "POST",
          headers: {
            Cookie: expiredCookie,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            type: "command",
            payload: { id: "expired-command", type: "get_state" },
          }),
        })
      ).status,
    ).toBe(404);
    expect(
      await executeCommand(origin, retainedClient, retainedCookie, {
        id: "retained-command",
        type: "get_state",
      }),
    ).toMatchObject({
      type: "response",
      payload: { id: "retained-command", success: true },
    });
  });

  it("disconnects only the Bridge Client and SSE bound to an absolute-expired Login Session", async () => {
    let currentTime = 1_000;
    const provider = successfulProvider("shared-absolute-user", "unused");
    const { controller, origin } = await startOAuthServer(provider, undefined, {
      now: () => currentTime,
      sessionIdleTtlMs: 100,
      sessionAbsoluteTtlMs: 200,
      sessionGcIntervalMs: 10,
    });
    const expiredCookie = await completeLogin(origin, "absolute-expired");
    const expiredClient = await createAuthenticatedClient(origin, expiredCookie);
    const expiredSse = waitForSseClose(
      `${origin}${expiredClient.eventsUrl}`,
      expiredCookie,
    );
    await expiredSse.ready;
    for (currentTime = 1_090; currentTime < 1_200; currentTime += 90) {
      expect(
        await (
          await fetch(`${origin}/api/auth/current`, {
            headers: { Cookie: expiredCookie },
          })
        ).json(),
      ).toMatchObject({ status: "authenticated" });
    }
    const retainedCookie = await completeLogin(origin, "absolute-retained");
    const retainedClient = await createAuthenticatedClient(origin, retainedCookie);

    currentTime = 1_200;

    await expect(expiredSse.closed).resolves.toBeUndefined();
    await vi.waitFor(() => {
      expect(controller.getClients()).not.toContainEqual(expiredClient.client);
    });
    expect(controller.getClients()).toContainEqual(retainedClient.client);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: expiredCookie },
        })
      ).json(),
    ).toEqual({ status: "anonymous" });
    expect(
      (
        await fetch(`${origin}${expiredClient.messagesUrl}`, {
          method: "POST",
          headers: {
            Cookie: expiredCookie,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            type: "command",
            payload: { id: "absolute-expired-command", type: "get_state" },
          }),
        })
      ).status,
    ).toBe(404);
    expect(
      await executeCommand(origin, retainedClient, retainedCookie, {
        id: "absolute-retained-command",
        type: "get_state",
      }),
    ).toMatchObject({
      type: "response",
      payload: { id: "absolute-retained-command", success: true },
    });
  });

  it("accepts missing display profile and creates an independent Session for every login", async () => {
    const provider = successfulProvider("profile-optional-user", "profile-token");
    const { origin, runtimeRootPath } = await startOAuthServer(provider);

    const firstCookie = await completeLogin(origin);
    const secondCookie = await completeLogin(origin);

    expect(firstCookie).not.toBe(secondCookie);
    const firstCurrent = await fetch(`${origin}/api/auth/current`, {
      headers: { Cookie: firstCookie },
    });
    expect(await firstCurrent.json()).toEqual({
      status: "authenticated",
      user: {
        id: expect.stringMatching(/^oauth_[a-f0-9]{64}$/),
        username: "已登录用户",
      },
    });
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(2);
  });

  it("lets only server modules read the Credential owned by one Login Session", async () => {
    const provider = successfulProvider("broker-user", "broker-access-token");
    const { authentication, origin } = await startOAuthServer(provider);
    const loginCookie = await completeLogin(origin);
    const loginSessionId = loginCookie.slice("dano_login=".length);

    await expect(
      authentication.readProviderCredential(loginSessionId),
    ).resolves.toEqual({ accessToken: "broker-access-token" });

    await fetch(`${origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: loginCookie,
        Origin: "https://dano.example.test",
      },
    });
    await expect(
      authentication.readProviderCredential(loginSessionId),
    ).resolves.toBeNull();
  });

  it("atomically rotates one Login Session Credential through a single refresh flight", async () => {
    let releaseRefresh!: () => void;
    const refreshGate = new Promise<void>(resolve => {
      releaseRefresh = resolve;
    });
    let refreshCount = 0;
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("refresh-owner", "expired-access-token"),
      async exchangeAuthorizationCode() {
        return {
          identity: { userId: "refresh-owner" },
          credential: {
            accessToken: "expired-access-token",
            refreshToken: "retained-refresh-token",
          },
        };
      },
      async refreshCredential() {
        refreshCount += 1;
        await refreshGate;
        return { accessToken: "renewed-access-token" };
      },
    };
    const { authentication, origin } = await startOAuthServer(provider);
    const loginCookie = await completeLogin(origin);
    const loginSessionId = loginCookie.slice("dano_login=".length);

    const first = authentication.refreshProviderCredential(loginSessionId);
    const second = authentication.refreshProviderCredential(loginSessionId);
    await vi.waitFor(() => expect(refreshCount).toBe(1));
    releaseRefresh();

    await expect(Promise.all([first, second])).resolves.toEqual([
      {
        accessToken: "renewed-access-token",
        refreshToken: "retained-refresh-token",
      },
      {
        accessToken: "renewed-access-token",
        refreshToken: "retained-refresh-token",
      },
    ]);
    await expect(
      authentication.readProviderCredential(loginSessionId),
    ).resolves.toEqual({
      accessToken: "renewed-access-token",
      refreshToken: "retained-refresh-token",
    });
  });

  it("rejects a refreshed Credential that resolves to a different User", async () => {
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("refresh-owner-a", "access-a"),
      async exchangeAuthorizationCode() {
        return {
          identity: { userId: "refresh-owner-a" },
          credential: {
            accessToken: "access-a",
            refreshToken: "refresh-a",
          },
        };
      },
      async refreshCredential() {
        return { accessToken: "access-b", refreshToken: "refresh-b" };
      },
      async validateCredential(credential) {
        return {
          userId:
            credential.accessToken === "access-b"
              ? "refresh-owner-b"
              : "refresh-owner-a",
        };
      },
    };
    const { authentication, origin } = await startOAuthServer(provider);
    const loginCookie = await completeLogin(origin);
    const loginSessionId = loginCookie.slice("dano_login=".length);

    await expect(
      authentication.refreshProviderCredential(loginSessionId),
    ).rejects.toBeInstanceOf(OAuthProviderContractError);
    await expect(
      authentication.readProviderCredential(loginSessionId),
    ).resolves.toEqual({
      accessToken: "access-a",
      refreshToken: "refresh-a",
    });
  });

  it("projects reauthentication, disconnects only its old Bridge Clients, and survives refresh", async () => {
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("shared-reauth-user", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: {
            userId: "shared-reauth-user",
            displayName: "Shared User",
          },
          credential: {
            accessToken: `access-${code}`,
            refreshToken: `refresh-${code}`,
          },
        };
      },
    };
    const { authentication, controller, origin } = await startOAuthServer(provider);
    const firstCookie = await completeLogin(origin, "first-login");
    const secondCookie = await completeLogin(origin, "second-login");
    const firstLoginSessionId = firstCookie.slice("dano_login=".length);
    const secondLoginSessionId = secondCookie.slice("dano_login=".length);
    const firstClient = await createAuthenticatedClient(origin, firstCookie);
    const secondClient = await createAuthenticatedClient(origin, secondCookie);
    const projected = waitForAuthentication(
      `${origin}${firstClient.eventsUrl}`,
      firstCookie,
    );
    await projected.ready;

    await authentication.requireReauthentication(firstLoginSessionId);
    controller.requireReauthentication(firstLoginSessionId);

    await expect(projected.result).resolves.toEqual({
      type: "authentication",
      payload: { status: "reauth_required" },
    });
    projected.close();
    await expect(
      fetch(`${origin}${firstClient.messagesUrl}`, {
        method: "POST",
        headers: { Cookie: firstCookie, "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "command",
          payload: { id: "stale-client", type: "get_state" },
        }),
      }),
    ).resolves.toMatchObject({ status: 404 });
    expect(controller.getClients()).toContainEqual(secondClient.client);
    await expect(
      (await fetch(`${origin}/api/auth/current`, {
        headers: { Cookie: firstCookie },
      })).json(),
    ).resolves.toEqual({ status: "reauth_required" });
    await expect(
      (await fetch(`${origin}/api/auth/current`, {
        headers: { Cookie: secondCookie },
      })).json(),
    ).resolves.toMatchObject({ status: "authenticated" });
    await expect(
      authentication.readProviderCredential(firstLoginSessionId),
    ).resolves.toBeNull();
    await expect(
      authentication.readProviderCredential(secondLoginSessionId),
    ).resolves.toMatchObject({ accessToken: "access-second-login" });

    const staleReload = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { Cookie: firstCookie, "Content-Type": "application/json" },
      body: "{}",
    });
    expect(staleReload.status).toBe(201);
    await expect(staleReload.json()).resolves.toMatchObject({
      authentication: { status: "anonymous" },
    });
  });

  it("replaces a reauthentication record without affecting another Login Session", async () => {
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("shared-relogin-user", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: { userId: "shared-relogin-user" },
          credential: {
            accessToken: `access-${code}`,
            refreshToken: `refresh-${code}`,
          },
        };
      },
    };
    const { authentication, controller, origin, runtimeRootPath } =
      await startOAuthServer(provider);
    const staleCookie = await completeLogin(origin, "stale");
    const otherCookie = await completeLogin(origin, "other");
    const staleSessionId = staleCookie.slice("dano_login=".length);
    const otherSessionId = otherCookie.slice("dano_login=".length);
    await authentication.requireReauthentication(staleSessionId);
    controller.requireReauthentication(staleSessionId);

    const started = await fetch(`${origin}/api/auth/login?returnTo=/chat`, {
      headers: { Cookie: staleCookie },
      redirect: "manual",
    });
    const flowCookie = cookieFrom(started, "dano_oauth_flow");
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;
    const callback = await fetch(
      `${origin}/api/auth/callback?code=relogin&state=${encodeURIComponent(state)}`,
      {
        headers: { Cookie: `${staleCookie}; ${flowCookie}` },
        redirect: "manual",
      },
    );
    const replacementCookie = cookieFrom(callback, "dano_login");
    const replacementSessionId = replacementCookie.slice("dano_login=".length);

    expect(replacementSessionId).not.toBe(staleSessionId);
    await expect(
      authentication.readProviderCredential(staleSessionId),
    ).resolves.toBeNull();
    await expect(
      authentication.readProviderCredential(replacementSessionId),
    ).resolves.toMatchObject({ accessToken: "access-relogin" });
    await expect(
      authentication.readProviderCredential(otherSessionId),
    ).resolves.toMatchObject({ accessToken: "access-other" });
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(2);
  });

  it("rotates an active browser Login Session and disconnects only its old Client and SSE", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("shared-active-login-user", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: { userId: "shared-active-login-user" },
          credential: {
            accessToken: `access-${code}`,
            refreshToken: `refresh-${code}`,
          },
        };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { authentication, controller, origin, runtimeRootPath } =
      await startOAuthServer(provider);
    const rotatedCookie = await completeLogin(origin, "rotated");
    const retainedCookie = await completeLogin(origin, "retained");
    const rotatedSessionId = rotatedCookie.slice("dano_login=".length);
    const retainedSessionId = retainedCookie.slice("dano_login=".length);
    const rotatedClient = await createAuthenticatedClient(origin, rotatedCookie);
    const retainedClient = await createAuthenticatedClient(origin, retainedCookie);
    const rotatedSse = waitForSseClose(
      `${origin}${rotatedClient.eventsUrl}`,
      rotatedCookie,
    );
    await rotatedSse.ready;
    const started = await fetch(`${origin}/api/auth/login?returnTo=/chat`, {
      headers: { Cookie: rotatedCookie },
      redirect: "manual",
    });
    const flowCookie = cookieFrom(started, "dano_oauth_flow");
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=replacement&state=${encodeURIComponent(state)}`,
      {
        headers: { Cookie: `${rotatedCookie}; ${flowCookie}` },
        redirect: "manual",
      },
    );
    const replacementCookie = cookieFrom(callback, "dano_login");
    const replacementSessionId = replacementCookie.slice("dano_login=".length);

    expect(replacementSessionId).not.toBe(rotatedSessionId);
    await expect(rotatedSse.closed).resolves.toBeUndefined();
    expect(controller.getClients()).not.toContainEqual(rotatedClient.client);
    expect(controller.getClients()).toContainEqual(retainedClient.client);
    expect(revoked).toEqual([]);
    await expect(
      authentication.readProviderCredential(rotatedSessionId),
    ).resolves.toBeNull();
    await expect(
      authentication.readProviderCredential(replacementSessionId),
    ).resolves.toMatchObject({ accessToken: "access-replacement" });
    await expect(
      authentication.readProviderCredential(retainedSessionId),
    ).resolves.toMatchObject({ accessToken: "access-retained" });
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: rotatedCookie },
        })
      ).json(),
    ).toEqual({ status: "anonymous" });
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: replacementCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
    expect(
      (
        await fetch(`${origin}${rotatedClient.messagesUrl}`, {
          method: "POST",
          headers: {
            Cookie: rotatedCookie,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            type: "command",
            payload: { id: "rotated-command", type: "get_state" },
          }),
        })
      ).status,
    ).toBe(404);
    expect(
      await executeCommand(origin, retainedClient, retainedCookie, {
        id: "retained-after-rotation",
        type: "get_state",
      }),
    ).toMatchObject({
      type: "response",
      payload: { id: "retained-after-rotation", success: true },
    });
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(2);
  });

  it("revokes the replaced Credential when login rotation changes User", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("unused", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: {
            userId: code === "first" ? "rotation-user-a" : "rotation-user-b",
          },
          credential: { accessToken: `access-${code}` },
        };
      },
      async validateCredential(credential) {
        return {
          userId:
            credential.accessToken === "access-first"
              ? "rotation-user-a"
              : "rotation-user-b",
        };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin } = await startOAuthServer(provider);
    const firstCookie = await completeLogin(origin, "first");
    const started = await fetch(`${origin}/api/auth/login`, {
      headers: { Cookie: firstCookie },
      redirect: "manual",
    });
    const flowCookie = cookieFrom(started, "dano_oauth_flow");
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=second&state=${encodeURIComponent(state)}`,
      {
        headers: { Cookie: `${firstCookie}; ${flowCookie}` },
        redirect: "manual",
      },
    );
    const secondCookie = cookieFrom(callback, "dano_login");

    expect(revoked).toEqual(["access-first"]);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: secondCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
  });

  it("keeps an active Login Session, Client, and SSE when its new callback fails", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("failed-active-login-user", "original-token"),
      async exchangeAuthorizationCode({ code }) {
        if (code === "rejected") throw new Error("fixture provider failure");
        return {
          identity: { userId: "failed-active-login-user" },
          credential: { accessToken: "original-token" },
        };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { authentication, controller, origin, runtimeRootPath } =
      await startOAuthServer(provider);
    const loginCookie = await completeLogin(origin, "original");
    const loginSessionId = loginCookie.slice("dano_login=".length);
    const client = await createAuthenticatedClient(origin, loginCookie);
    const pending = waitForResponse(
      `${origin}${client.eventsUrl}`,
      loginCookie,
      "still-active",
    );
    await pending.ready;
    const started = await fetch(`${origin}/api/auth/login?returnTo=/chat`, {
      headers: { Cookie: loginCookie },
      redirect: "manual",
    });
    const flowCookie = cookieFrom(started, "dano_oauth_flow");
    const state = new URL(started.headers.get("location")!).searchParams.get(
      "state",
    )!;

    const callback = await fetch(
      `${origin}/api/auth/callback?code=rejected&state=${encodeURIComponent(state)}`,
      {
        headers: { Cookie: `${loginCookie}; ${flowCookie}` },
        redirect: "manual",
      },
    );
    const posted = await fetch(`${origin}${client.messagesUrl}`, {
      method: "POST",
      headers: { Cookie: loginCookie, "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "command",
        payload: { id: "still-active", type: "get_state" },
      }),
    });

    expect(callback.headers.get("set-cookie")).toMatch(
      /^dano_auth_error=[A-Za-z0-9_-]{43};/,
    );
    expect(posted.status).toBe(202);
    await expect(pending.result).resolves.toMatchObject({
      type: "response",
      payload: { id: "still-active", success: true },
    });
    pending.close();
    expect(controller.getClients()).toContainEqual(client.client);
    expect(revoked).toEqual([]);
    await expect(
      authentication.readProviderCredential(loginSessionId),
    ).resolves.toEqual({ accessToken: "original-token" });
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: loginCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(1);
  });

  it("cancels reauthentication into a fresh Anonymous User with a usable Bridge", async () => {
    const provider = successfulProvider("cancel-reauth-user", "expired-token");
    const { authentication, controller, origin } = await startOAuthServer(provider);
    const loginCookie = await completeLogin(origin);
    const loginSessionId = loginCookie.slice("dano_login=".length);
    const authenticatedClient = await createAuthenticatedClient(
      origin,
      loginCookie,
    );
    fs.writeFileSync(
      path.join(authenticatedClient.defaultWorkspacePath, "authenticated-only.txt"),
      "must remain with the authenticated User",
      "utf8",
    );
    await authentication.requireReauthentication(loginSessionId);
    controller.requireReauthentication(loginSessionId);
    await expect(
      authentication.resolveAuthSessionState({ cookie: loginCookie }),
    ).resolves.toEqual({ status: "reauth_required", loginSessionId });

    const logout = await fetch(`${origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: loginCookie,
        Origin: "https://dano.example.test",
      },
    });
    expect(logout.status).toBe(200);
    expect(await logout.json()).toEqual({ status: "anonymous" });

    const created = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    expect(created.status).toBe(201);
    const guestCookie = cookieFrom(created, "dano_guest");
    const guestClient = (await created.json()) as TestBridgeClient;
    expect(guestClient.defaultWorkspacePath).not.toBe(
      authenticatedClient.defaultWorkspacePath,
    );
    expect(
      fs.existsSync(
        path.join(guestClient.defaultWorkspacePath, "authenticated-only.txt"),
      ),
    ).toBe(false);
    expect(
      await executeCommand(origin, guestClient, guestCookie, {
        id: "anonymous-state",
        type: "get_state",
      }),
    ).toMatchObject({
      type: "response",
      payload: { id: "anonymous-state", success: true },
    });
  });

  it("logs out only the current Dano Login Session and revokes only its Provider Credential", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("shared-logout-user", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: {
            userId: "shared-logout-user",
            displayName: "Shared User",
          },
          credential: { accessToken: `access-${code}` },
        };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const firstCookie = await completeLogin(origin, "first-login");
    const secondCookie = await completeLogin(origin, "second-login");
    const firstClient = await createAuthenticatedClient(origin, firstCookie);
    const secondClient = await createAuthenticatedClient(origin, secondCookie);
    expect(firstClient.defaultWorkspacePath).toBe(
      secondClient.defaultWorkspacePath,
    );
    fs.writeFileSync(
      path.join(firstClient.defaultWorkspacePath, "authenticated-only.txt"),
      "do not copy on logout",
      "utf8",
    );
    expect(
      (
        await fetch(`${origin}/api/clients/${firstClient.client.id}/messages`, {
          method: "POST",
          headers: {
            Cookie: secondCookie,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            type: "command",
            payload: { id: "wrong-login-session", type: "get_state" },
          }),
        })
      ).status,
    ).toBe(401);

    const logout = await fetch(`${origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: firstCookie,
        Origin: "https://dano.example.test",
      },
    });

    expect(logout.status).toBe(200);
    expect(await logout.json()).toEqual({ status: "anonymous" });
    expect(logout.headers.get("set-cookie")).toMatch(
      /^dano_login=; Path=\/; HttpOnly; Secure; SameSite=Lax; Max-Age=0$/,
    );
    expect(revoked).toEqual(["access-first-login"]);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: firstCookie },
        })
      ).json(),
    ).toEqual({ status: "anonymous" });
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: secondCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
    expect(
      (
        await fetch(
          `${origin}/api/clients/${firstClient.client.id}/messages`,
          {
            method: "POST",
            headers: {
              Cookie: firstCookie,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              type: "command",
              payload: { id: "old-client", type: "get_state" },
            }),
          },
        )
      ).status,
    ).toBe(404);
    expect(
      (
        await fetch(
          `${origin}/api/clients/${secondClient.client.id}/user`,
          { headers: { Cookie: secondCookie } },
        )
      ).status,
    ).toBe(200);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(1);
    const anonymous = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { Cookie: firstCookie, "Content-Type": "application/json" },
      body: "{}",
    });
    const anonymousClient = (await anonymous.json()) as TestBridgeClient & {
      authentication: { status: string };
    };
    const anonymousCookie = cookieFrom(anonymous, "dano_guest");
    expect(anonymousClient.authentication).toEqual({ status: "anonymous" });
    expect(anonymousClient.defaultWorkspacePath).not.toBe(
      firstClient.defaultWorkspacePath,
    );
    expect(
      fs.existsSync(
        path.join(anonymousClient.defaultWorkspacePath, "authenticated-only.txt"),
      ),
    ).toBe(false);
    const anonymousState = await executeCommand(
      origin,
      anonymousClient,
      anonymousCookie,
      { id: "anonymous-after-logout", type: "get_state" },
    );
    expect(anonymousState.payload).toMatchObject({
      command: "get_state",
      success: true,
    });

    const lastLogout = await fetch(`${origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: secondCookie,
        Origin: "https://dano.example.test",
      },
    });
    expect(lastLogout.status).toBe(200);
    expect(revoked).toEqual([
      "access-first-login",
      "access-second-login",
    ]);
  });

  it("retains a same-User Login Session touched at the idle TTL boundary", async () => {
    let currentTime = 1_000;
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("idle-boundary-user", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: { userId: "idle-boundary-user" },
          credential: { accessToken: `access-${code}` },
        };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin } = await startOAuthServer(provider, undefined, {
      now: () => currentTime,
      sessionIdleTtlMs: 100,
      sessionAbsoluteTtlMs: 1_000,
    });
    const firstCookie = await completeLogin(origin, "idle-first");
    const retainedCookie = await completeLogin(origin, "idle-retained");
    currentTime += 99;
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: retainedCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
    currentTime += 1;

    const logout = await fetch(`${origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: firstCookie,
        Origin: "https://dano.example.test",
      },
    });

    expect(logout.status).toBe(200);
    expect(revoked).toEqual([]);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: retainedCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
  });

  it("rejects cross-origin logout without revoking the Login Session", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("csrf-user", "csrf-token"),
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin } = await startOAuthServer(provider);
    const loginCookie = await completeLogin(origin);

    const rejected = await fetch(`${origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: loginCookie,
        Origin: "https://outside.example.test",
      },
    });

    expect(rejected.status).toBe(403);
    expect(revoked).toEqual([]);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: loginCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
  });

  it("serializes concurrent logout and revokes each Login Session Credential", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("concurrent-logout-user", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: { userId: "concurrent-logout-user" },
          credential: { accessToken: `access-${code}` },
        };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const firstCookie = await completeLogin(origin, "concurrent-first");
    const secondCookie = await completeLogin(origin, "concurrent-second");

    const responses = await Promise.all(
      [firstCookie, secondCookie].map(cookie =>
        fetch(`${origin}/api/auth/logout`, {
          method: "POST",
          headers: {
            Cookie: cookie,
            Origin: "https://dano.example.test",
          },
        }),
      ),
    );

    expect(responses.map(response => response.status)).toEqual([200, 200]);
    expect(revoked.sort()).toEqual([
      "access-concurrent-first",
      "access-concurrent-second",
    ]);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(0);
  });

  it("blocks only same-User Login Session publication while revoke is in flight", async () => {
    let sameUserGrantRevision = 0;
    let releaseRevocation!: () => void;
    const revocationGate = new Promise<void>(resolve => {
      releaseRevocation = resolve;
    });
    let reportRevocationStarted!: () => void;
    const revocationStarted = new Promise<void>(resolve => {
      reportRevocationStarted = resolve;
    });
    let reportReplacementExchanged!: () => void;
    const replacementExchanged = new Promise<void>(resolve => {
      reportReplacementExchanged = resolve;
    });
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("unused", "unused"),
      async exchangeAuthorizationCode({ code }) {
        if (code === "same-replacement") reportReplacementExchanged();
        return {
          identity: {
            userId: code.startsWith("same-")
              ? "revocation-barrier-user"
              : "independent-user",
          },
          credential: {
            accessToken: `access-${code}-${sameUserGrantRevision}`,
          },
        };
      },
      async validateCredential(credential) {
        if (
          credential.accessToken.includes("same-") &&
          !credential.accessToken.endsWith(`-${sameUserGrantRevision}`)
        ) {
          throw new Error("fixture Credential was revoked with its grant");
        }
        return {
          userId: credential.accessToken.includes("same-")
            ? "revocation-barrier-user"
            : "independent-user",
        };
      },
      async revokeCredential() {
        reportRevocationStarted();
        await revocationGate;
        sameUserGrantRevision += 1;
      },
    };
    const { origin, runtimeRootPath } = await startOAuthServer(provider);
    const currentCookie = await completeLogin(origin, "same-current");
    const logout = fetch(`${origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: currentCookie,
        Origin: "https://dano.example.test",
      },
    });
    await revocationStarted;

    const independentCookie = await completeLogin(origin, "other-login");
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: independentCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });

    const sameLoginStarted = await fetch(`${origin}/api/auth/login`, {
      redirect: "manual",
    });
    const sameState = new URL(
      sameLoginStarted.headers.get("location")!,
    ).searchParams.get("state")!;
    let sameCallbackSettled = false;
    const sameCallback = fetch(
      `${origin}/api/auth/callback?code=same-replacement&state=${sameState}`,
      {
        headers: {
          Cookie: cookieFrom(sameLoginStarted, "dano_oauth_flow"),
        },
        redirect: "manual",
      },
    ).then(response => {
      sameCallbackSettled = true;
      return response;
    });
    await replacementExchanged;
    expect(sameCallbackSettled).toBe(false);
    expect(
      fs.readdirSync(path.join(runtimeRootPath, "auth", "login-sessions")),
    ).toHaveLength(1);

    releaseRevocation();
    expect((await logout).status).toBe(200);
    expect((await sameCallback).headers.get("set-cookie")).toMatch(
      /^dano_auth_error=/,
    );
    const replacementCookie = await completeLogin(origin, "same-retry");
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: replacementCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
  });

  it("revokes logout Credential when only a different User remains", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("unused", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: { userId: `logout-user-${code}` },
          credential: { accessToken: `access-${code}` },
        };
      },
      async validateCredential(credential) {
        return {
          userId: `logout-user-${credential.accessToken.slice("access-".length)}`,
        };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
        throw new Error("fixture revocation unavailable");
      },
    };
    const { origin } = await startOAuthServer(provider);
    const firstCookie = await completeLogin(origin, "first");
    const secondCookie = await completeLogin(origin, "second");

    const logout = await fetch(`${origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: firstCookie,
        Origin: "https://dano.example.test",
      },
    });

    expect(logout.status).toBe(200);
    expect(revoked).toEqual(["access-first"]);
    expect(
      await (
        await fetch(`${origin}/api/auth/current`, {
          headers: { Cookie: secondCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
  });

  it("revokes the current Credential after restart without removing another Login Session", async () => {
    const revoked: string[] = [];
    const provider: OAuthProviderAdapter = {
      ...successfulProvider("restart-shared-user", "unused"),
      async exchangeAuthorizationCode({ code }) {
        return {
          identity: { userId: "restart-shared-user" },
          credential: { accessToken: `access-${code}` },
        };
      },
      async revokeCredential(credential) {
        revoked.push(credential.accessToken);
      },
    };
    const firstRun = await startOAuthServer(provider);
    const firstCookie = await completeLogin(firstRun.origin, "restart-first");
    const secondCookie = await completeLogin(firstRun.origin, "restart-second");
    await firstRun.controller.stop();
    await firstRun.authentication.dispose();

    const restarted = await startOAuthServer(
      provider,
      firstRun.runtimeRootPath,
    );
    const logout = await fetch(`${restarted.origin}/api/auth/logout`, {
      method: "POST",
      headers: {
        Cookie: firstCookie,
        Origin: "https://dano.example.test",
      },
    });

    expect(logout.status).toBe(200);
    expect(revoked).toEqual(["access-restart-first"]);
    expect(
      await (
        await fetch(`${restarted.origin}/api/auth/current`, {
          headers: { Cookie: secondCookie },
        })
      ).json(),
    ).toMatchObject({ status: "authenticated" });
  });

  it("preserves the adapter-verified opaque userId exactly when deriving User ownership", async () => {
    let externalUserId = "opaque-user";
    const provider: OAuthProviderAdapter = {
      authorizationUrl(input) {
        const url = new URL("https://provider.example.test/authorize");
        url.searchParams.set("state", input.state);
        return url;
      },
      async exchangeAuthorizationCode() {
        return {
          identity: { userId: externalUserId },
          credential: { accessToken: "opaque-token" },
        };
      },
    };
    const { origin } = await startOAuthServer(provider);
    const firstCookie = await completeLogin(origin);
    externalUserId = " opaque-user ";
    const secondCookie = await completeLogin(origin);

    const firstClient = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { Cookie: firstCookie, "Content-Type": "application/json" },
      body: "{}",
    });
    const secondClient = await fetch(`${origin}/api/clients`, {
      method: "POST",
      headers: { Cookie: secondCookie, "Content-Type": "application/json" },
      body: "{}",
    });
    const firstBody = (await firstClient.json()) as {
      defaultWorkspacePath: string;
    };
    const secondBody = (await secondClient.json()) as {
      defaultWorkspacePath: string;
    };
    expect(firstBody.defaultWorkspacePath).not.toBe(
      secondBody.defaultWorkspacePath,
    );
  });
});

function successfulProvider(
  userId: string,
  accessToken: string,
): OAuthProviderAdapter {
  return {
    authorizationUrl(input) {
      const url = new URL("https://provider.example.test/authorize");
      url.searchParams.set("state", input.state);
      return url;
    },
    async exchangeAuthorizationCode() {
      return {
        identity: { userId },
        credential: { accessToken },
      };
    },
    async validateCredential() {
      return { userId };
    },
  };
}

async function completeLogin(
  origin: string,
  code = "fixture",
): Promise<string> {
  const started = await fetch(`${origin}/api/auth/login`, {
    redirect: "manual",
  });
  const state = new URL(started.headers.get("location")!).searchParams.get(
    "state",
  )!;
  const callback = await fetch(
    `${origin}/api/auth/callback?code=${encodeURIComponent(code)}&state=${state}`,
    {
      headers: { Cookie: cookieFrom(started, "dano_oauth_flow") },
      redirect: "manual",
    },
  );
  return cookieFrom(callback, "dano_login");
}

async function createAuthenticatedClient(origin: string, cookie: string) {
  const response = await fetch(`${origin}/api/clients`, {
    method: "POST",
    headers: { Cookie: cookie, "Content-Type": "application/json" },
    body: "{}",
  });
  expect(response.status).toBe(201);
  return (await response.json()) as TestBridgeClient;
}

type TestBridgeClient = {
  client: { id: string };
  defaultWorkspacePath: string;
  eventsUrl: string;
  messagesUrl: string;
};

function waitForResponse(
  url: string,
  cookie: string,
  correlationId: string,
): { close(): void; ready: Promise<void>; result: Promise<ServerMessage> } {
  let request: http.ClientRequest;
  let markReady!: () => void;
  const ready = new Promise<void>(resolve => {
    markReady = resolve;
  });
  const result = new Promise<ServerMessage>((resolve, reject) => {
    let buffer = "";
    const timeout = setTimeout(() => {
      request.destroy();
      reject(new Error(`Timed out waiting for ${correlationId}`));
    }, 2_000);
    request = http.get(url, { headers: { Cookie: cookie } }, response => {
      markReady();
      response.setEncoding("utf8");
      response.on("data", chunk => {
        buffer += chunk;
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = frame
            .split(/\r?\n/)
            .filter(line => line.startsWith("data: "))
            .map(line => line.slice(6))
            .join("\n");
          if (data) {
            const message = JSON.parse(data) as ServerMessage;
            if (
              message.type === "response" &&
              message.payload.id === correlationId
            ) {
              clearTimeout(timeout);
              resolve(message);
              return;
            }
          }
          boundary = buffer.indexOf("\n\n");
        }
      });
      response.on("error", reject);
    });
    request.on("error", reject);
  });
  return { ready, result, close: () => request.destroy() };
}

function waitForAuthentication(
  url: string,
  cookie: string,
): { close(): void; ready: Promise<void>; result: Promise<ServerMessage> } {
  let request: http.ClientRequest;
  let markReady!: () => void;
  const ready = new Promise<void>(resolve => {
    markReady = resolve;
  });
  const result = new Promise<ServerMessage>((resolve, reject) => {
    let buffer = "";
    const timeout = setTimeout(() => {
      request.destroy();
      reject(new Error("Timed out waiting for authentication state"));
    }, 2_000);
    request = http.get(url, { headers: { Cookie: cookie } }, response => {
      markReady();
      response.setEncoding("utf8");
      response.on("data", chunk => {
        buffer += chunk;
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = frame
            .split(/\r?\n/)
            .filter(line => line.startsWith("data: "))
            .map(line => line.slice(6))
            .join("\n");
          if (data) {
            const message = JSON.parse(data) as ServerMessage;
            if (message.type === "authentication") {
              clearTimeout(timeout);
              resolve(message);
              return;
            }
          }
          boundary = buffer.indexOf("\n\n");
        }
      });
      response.on("error", reject);
    });
    request.on("error", reject);
  });
  return { ready, result, close: () => request.destroy() };
}

function waitForSseClose(
  url: string,
  cookie: string,
): { ready: Promise<void>; closed: Promise<void> } {
  let markReady!: () => void;
  const ready = new Promise<void>(resolve => {
    markReady = resolve;
  });
  const closed = new Promise<void>((resolve, reject) => {
    const request = http.get(url, { headers: { Cookie: cookie } }, response => {
      markReady();
      response.resume();
      response.once("end", resolve);
      response.once("error", reject);
      response.once("aborted", resolve);
    });
    request.once("error", reject);
  });
  return { ready, closed };
}

async function executeCommand(
  origin: string,
  client: TestBridgeClient,
  cookie: string,
  payload: Extract<ClientMessage, { type: "command" }>["payload"],
): Promise<ServerMessage> {
  const correlationId = payload.id;
  if (!correlationId) throw new Error("Test commands require an id");
  const response = waitForResponse(
    `${origin}${client.eventsUrl}`,
    cookie,
    correlationId,
  );
  try {
    await response.ready;
    const posted = await fetch(`${origin}${client.messagesUrl}`, {
      method: "POST",
      headers: { Cookie: cookie, "Content-Type": "application/json" },
      body: JSON.stringify({ type: "command", payload } satisfies ClientMessage),
    });
    expect(posted.status).toBe(202);
    return await response.result;
  } finally {
    response.close();
  }
}

async function uploadProjectFile(
  origin: string,
  client: TestBridgeClient,
  cookie: string,
  name: string,
  content: string,
) {
  const body = new TextEncoder().encode(content);
  const sha256 = createHash("sha256").update(body).digest("hex");
  const response = await fetch(
    `${origin}/api/uploads?clientId=${encodeURIComponent(client.client.id)}&name=${encodeURIComponent(name)}&mimeType=text/plain&sha256=${sha256}`,
    { method: "POST", headers: { Cookie: cookie }, body },
  );
  expect(response.status).toBe(201);
  return (await response.json()) as {
    id: string;
    path: string;
    previewUrl: string;
  };
}

function sessionHours(hours: number): number {
  return hours * 60 * 60 * 1000;
}

function sessionDays(days: number): number {
  return days * 24 * 60 * 60 * 1000;
}
