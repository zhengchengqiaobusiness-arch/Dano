import {
  createCipheriv,
  createDecipheriv,
  createHash,
  randomBytes,
  randomUUID,
} from "node:crypto";
import * as fs from "node:fs";
import type * as http from "node:http";
import * as path from "node:path";
import { oauthUserId } from "./oauth-user-id.js";
import { writeFile as writeFileAtomically } from "atomically";
import type {
  BridgeAuthenticationState,
  BridgeLoginErrorCode,
} from "../../types/protocol.js";
import { BRIDGE_LOGIN_ERROR_CODES, LOGIN_NEW_CHAT_QUERY_PARAM } from "../../types/protocol.js";
import { ensureSafeDirectory } from "./safe-directory.js";
import {
  OAuthProviderContractError,
  type ExternalIdentity,
  type OAuthProviderAdapter,
  type ProviderCredential,
} from "./oauth-provider.js";
export type {
  ExternalIdentity,
  OAuthProviderAdapter,
  ProviderCredential,
} from "./oauth-provider.js";
import type { AuthHttpHandler, AuthHttpLifecycle } from "./server.js";
import {
  ensureUserFolder,
  toBrowserUserSummary,
  type AuthenticatedUser,
  type AuthenticatedUserContext,
  type AuthenticatedUserContextResolver,
  type ClientUserResolution,
  UserContextError,
} from "./user-context.js";
import {
  classifyOAuthLoginFailure,
  reportOAuthLoginFailure,
  type OAuthLoginStage,
} from "./oauth-login-diagnostics.js";

const FLOW_COOKIE_NAME = "dano_oauth_flow";
const LOGIN_COOKIE_NAME = "dano_login";
const AUTH_ERROR_COOKIE_NAME = "dano_auth_error";
const STATE_TTL_MS = 10 * 60 * 1000;
const AUTH_ERROR_TTL_MS = 5 * 60 * 1000;
const SESSION_IDLE_TTL_MS = 8 * 60 * 60 * 1000;
const SESSION_ABSOLUTE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const SESSION_GC_INTERVAL_MS = 60 * 60 * 1000;
const MAX_PENDING_TRANSACTIONS = 8;
const OPAQUE_ID_PATTERN = /^[A-Za-z0-9_-]{43}$/;

export interface OAuthAuthenticationOptions {
  readonly runtimeRootPath: string;
  readonly appOrigin: string;
  readonly redirectUri: string;
  readonly provider: OAuthProviderAdapter;
  readonly credentialEncryptionKey: {
    readonly version: string;
    readonly key: Uint8Array;
  };
  readonly now?: () => number;
  readonly stateTtlMs?: number;
  readonly sessionIdleTtlMs?: number;
  readonly sessionAbsoluteTtlMs?: number;
  readonly sessionGcIntervalMs?: number;
  readonly maxPendingTransactions?: number;
}

export type OAuthAuthSessionState =
  | {
      readonly status: "authenticated";
      readonly loginSessionId: string;
      readonly userContext: AuthenticatedUserContext;
    }
  | { readonly status: "reauth_required"; readonly loginSessionId: string }
  | null;

export interface OAuthAuthentication
  extends AuthenticatedUserContextResolver,
    AuthHttpHandler {
  readProviderCredential(
    loginSessionId: string,
  ): Promise<ProviderCredential | null>;
  refreshProviderCredential(
    loginSessionId: string,
  ): Promise<ProviderCredential | null>;
  resolveAuthSessionState(
    headers: http.IncomingHttpHeaders,
  ): Promise<OAuthAuthSessionState>;
  requireReauthentication(loginSessionId: string): Promise<void>;
  dispose(): Promise<void>;
}

export async function createOAuthAuthentication(
  options: OAuthAuthenticationOptions,
): Promise<OAuthAuthentication> {
  const appOrigin = new URL(options.appOrigin).origin;
  const redirectUri = new URL(options.redirectUri);
  if (redirectUri.origin !== appOrigin) {
    throw new Error("OAuth redirect URI must use the configured Dano origin");
  }
  if (options.credentialEncryptionKey.key.byteLength !== 32) {
    throw new Error("OAuth credential encryption key must be 32 bytes");
  }
  if (options.provider.revokeCredential && !options.provider.validateCredential) {
    throw new Error(
      "OAuth provider revocation requires Credential validation",
    );
  }
  const transactionsPath = path.resolve(
    options.runtimeRootPath,
    "auth",
    "login-transactions",
  );
  const sessionsPath = path.resolve(
    options.runtimeRootPath,
    "auth",
    "login-sessions",
  );
  const errorsPath = path.resolve(
    options.runtimeRootPath,
    "auth",
    "login-errors",
  );
  const usersRootPath = path.resolve(options.runtimeRootPath, "users");
  await ensureSafeDirectory(transactionsPath, {
    recursive: true,
    unsafeDirectoryError: () =>
      new Error("OAuth login transaction directory is not safe"),
  });
  await ensureSafeDirectory(sessionsPath, {
    recursive: true,
    unsafeDirectoryError: () =>
      new Error("OAuth Login Session directory is not safe"),
  });
  await ensureSafeDirectory(errorsPath, {
    recursive: true,
    unsafeDirectoryError: () =>
      new Error("OAuth login error directory is not safe"),
  });
  await Promise.all([
    fs.promises.chmod(transactionsPath, 0o700),
    fs.promises.chmod(sessionsPath, 0o700),
    fs.promises.chmod(errorsPath, 0o700),
  ]);
  const now = options.now ?? Date.now;
  const stateTtlMs = options.stateTtlMs ?? STATE_TTL_MS;
  const sessionIdleTtlMs = options.sessionIdleTtlMs ?? SESSION_IDLE_TTL_MS;
  const sessionAbsoluteTtlMs =
    options.sessionAbsoluteTtlMs ?? SESSION_ABSOLUTE_TTL_MS;
  if (sessionAbsoluteTtlMs <= sessionIdleTtlMs) {
    throw new Error(
      "OAuth Login Session absolute TTL must be greater than idle TTL",
    );
  }
  const maxPendingTransactions =
    options.maxPendingTransactions ?? MAX_PENDING_TRANSACTIONS;
  const browserLocks = new Map<string, Promise<void>>();
  const loginSessionLocks = new Map<string, Promise<void>>();
  const loginSessionUserLocks = new Map<string, Promise<void>>();
  let loginSessionRecordsMutation = Promise.resolve();
  const mutateLoginSessionRecords = async <T>(
    operation: () => Promise<T>,
  ): Promise<T> => {
    const previous = loginSessionRecordsMutation;
    let release: () => void;
    loginSessionRecordsMutation = new Promise<void>(resolve => {
      release = resolve;
    });
    await previous;
    try {
      return await operation();
    } finally {
      release!();
    }
  };
  const knownLoginSessionIds = new Map<string, string>();
  const credentialRefreshes = new Map<
    string,
    Promise<ProviderCredential | null>
  >();
  let currentLifecycle: AuthHttpLifecycle | undefined;
  await mutateLoginSessionRecords(() =>
    cleanupExpiredRecords(
      transactionsPath,
      sessionsPath,
      errorsPath,
      now(),
      stateTtlMs,
      sessionIdleTtlMs,
    ),
  );
  const cleanupInterval = setInterval(() => {
    void mutateLoginSessionRecords(() =>
      cleanupExpiredRecords(
        transactionsPath,
        sessionsPath,
        errorsPath,
        now(),
        stateTtlMs,
        sessionIdleTtlMs,
        recordName => {
          const sessionId = knownLoginSessionIds.get(recordName);
          if (!sessionId) return;
          knownLoginSessionIds.delete(recordName);
          currentLifecycle?.disconnectLoginSession(sessionId);
        },
      ),
    ).catch(() => {});
  }, options.sessionGcIntervalMs ?? SESSION_GC_INTERVAL_MS);
  cleanupInterval.unref?.();

  const loadSessionUnlocked = async (
    sessionId: string,
    touch: boolean,
  ): Promise<StoredLoginSession | null> => {
    if (!OPAQUE_ID_PATTERN.test(sessionId)) return null;
    const recordPath = loginSessionPath(sessionsPath, sessionId);
    let session: StoredLoginSession;
    try {
      session = parseLoginSession(await fs.promises.readFile(recordPath, "utf8"));
      if (session.status === "active") {
        decryptCredential(
          session.credential!,
          options.credentialEncryptionKey,
          digest(sessionId),
        );
      }
      knownLoginSessionIds.set(path.basename(recordPath), sessionId);
    } catch {
      await fs.promises.rm(recordPath, { force: true }).catch(() => {});
      knownLoginSessionIds.delete(path.basename(recordPath));
      currentLifecycle?.disconnectLoginSession(sessionId);
      return null;
    }
    const currentTime = now();
    if (
      currentTime - session.lastActiveAt >= sessionIdleTtlMs ||
      currentTime >= session.absoluteExpiresAt
    ) {
      await fs.promises.rm(recordPath, { force: true }).catch(() => {});
      knownLoginSessionIds.delete(path.basename(recordPath));
      currentLifecycle?.disconnectLoginSession(sessionId);
      return null;
    }
    if (touch && session.status === "active") {
      session = { ...session, lastActiveAt: currentTime };
      try {
        await writeLoginSession(recordPath, session);
      } catch {
        return null;
      }
    }
    return session;
  };

  const loadSession = (
    sessionId: string,
    touch: boolean,
  ): Promise<StoredLoginSession | null> =>
    mutateLoginSessionRecords(() =>
      withKeyLock(loginSessionLocks, sessionId, () =>
        loadSessionUnlocked(sessionId, touch),
      ),
    );

  const resolve = async (
    headers: http.IncomingHttpHeaders,
  ): Promise<AuthenticatedUserContext | null> => {
    const resolved = await resolveLoginSession(headers);
    return resolved?.status === "authenticated" ? resolved.userContext : null;
  };

  const resolveLoginSession = async (
    headers: http.IncomingHttpHeaders,
  ): Promise<OAuthAuthSessionState> => {
    const sessionId = readCookie(headers.cookie, LOGIN_COOKIE_NAME);
    if (!sessionId) return null;
    const session = await loadSession(sessionId, true);
    if (!session) return null;
    if (session.status === "reauth_required") {
      return { status: "reauth_required", loginSessionId: sessionId };
    }
    return {
      status: "authenticated",
      loginSessionId: sessionId,
      userContext: {
        user: session.user,
        folderPath: await ensureUserFolder(usersRootPath, session.user.id),
      },
    };
  };

  const resolveClientLoginSession = async (
    headers: http.IncomingHttpHeaders,
  ): Promise<ClientUserResolution | null> => {
    const resolved = await resolveLoginSession(headers);
    if (resolved?.status === "reauth_required") {
      throw new UserContextError(
        401,
        "Dano Login Session requires reauthentication",
      );
    }
    return resolved
      ? {
          userContext: resolved.userContext,
          authentication: {
            status: "authenticated",
            user: toBrowserUserSummary(resolved.userContext.user),
          },
          loginSessionId: resolved.loginSessionId,
        }
      : null;
  };

  return {
    resolve,
    async readProviderCredential(loginSessionId) {
      const session = await loadSession(loginSessionId, true);
      return session?.status === "active" && session.credential
        ? decryptCredential(
            session.credential,
            options.credentialEncryptionKey,
            digest(loginSessionId),
          )
        : null;
    },
    refreshProviderCredential(loginSessionId) {
      const existing = credentialRefreshes.get(loginSessionId);
      if (existing) return existing;
      const refresh = (async () => {
        const session = await loadSession(loginSessionId, true);
        if (
          !session ||
          session.status !== "active" ||
          !session.credential ||
          !options.provider.refreshCredential
        ) {
          return null;
        }
        const credential = decryptCredential(
          session.credential,
          options.credentialEncryptionKey,
          digest(loginSessionId),
        );
        if (!credential.refreshToken) return null;
        return withKeyLock(loginSessionUserLocks, session.user.id, async () => {
          const current = await loadSession(loginSessionId, false);
          if (!current || current.status !== "active") return null;
          const refreshed = validCredential(
            await options.provider.refreshCredential!(credential),
          );
          const rotated = {
            ...refreshed,
            refreshToken: refreshed.refreshToken ?? credential.refreshToken,
          };
          if (options.provider.validateCredential) {
            const refreshedIdentity =
              await options.provider.validateCredential(rotated);
            if (externalIdentityUser(refreshedIdentity).id !== current.user.id) {
              throw new OAuthProviderContractError();
            }
          }
          return mutateLoginSessionRecords(() =>
            withKeyLock(loginSessionLocks, loginSessionId, async () => {
              const latest = await loadSessionUnlocked(loginSessionId, false);
              if (!latest || latest.status !== "active") return null;
              await writeLoginSession(
                loginSessionPath(sessionsPath, loginSessionId),
                {
                  ...latest,
                  credential: encryptCredential(
                    rotated,
                    options.credentialEncryptionKey,
                    digest(loginSessionId),
                  ),
                },
              );
              return rotated;
            }),
          );
        });
      })().finally(() => {
        if (credentialRefreshes.get(loginSessionId) === refresh) {
          credentialRefreshes.delete(loginSessionId);
        }
      });
      credentialRefreshes.set(loginSessionId, refresh);
      return refresh;
    },
    async requireReauthentication(loginSessionId) {
      await mutateLoginSessionRecords(() =>
        withKeyLock(loginSessionLocks, loginSessionId, async () => {
          const session = await loadSessionUnlocked(loginSessionId, false);
          if (!session || session.status === "reauth_required") return;
          const { credential: _credential, ...withoutCredential } = session;
          const recordPath = loginSessionPath(sessionsPath, loginSessionId);
          try {
            await writeLoginSession(recordPath, {
              ...withoutCredential,
              status: "reauth_required",
            });
          } catch (error) {
            await fs.promises.rm(recordPath, { force: true }).catch(() => {});
            throw error;
          }
        }),
      );
    },
    resolveAuthSessionState: resolveLoginSession,
    resolveForClient: resolveClientLoginSession,
    resolveExisting: resolveClientLoginSession,
    async handle(
      req: http.IncomingMessage,
      res: http.ServerResponse,
      url: URL,
      lifecycle: AuthHttpLifecycle,
    ): Promise<boolean> {
      currentLifecycle = lifecycle;
      if (req.method === "GET" && url.pathname === "/api/auth/current") {
        const [current, authError] = await Promise.all([
          resolveLoginSession(req.headers),
          consumeAuthError(req.headers, errorsPath, now()),
        ]);
        if (readCookie(req.headers.cookie, AUTH_ERROR_COOKIE_NAME)) {
          res.setHeader("Set-Cookie", serializeExpiredAuthErrorCookie());
        }
        const loginErrorDto = authError
          ? { loginError: { code: authError.code } }
          : {};
        const authentication: BridgeAuthenticationState =
          current?.status === "authenticated"
            ? {
                status: "authenticated",
                user: toBrowserUserSummary(current.userContext.user),
                ...loginErrorDto,
              }
            : current?.status === "reauth_required"
              ? { status: "reauth_required", ...loginErrorDto }
              : { status: "anonymous", ...loginErrorDto };
        writeJson(res, 200, authentication);
        return true;
      }
      if (req.method === "GET" && url.pathname === "/api/auth/callback") {
        await handleCallback(req, res, url, {
          transactionsPath,
          sessionsPath,
          errorsPath,
          redirectUri: redirectUri.href,
          provider: options.provider,
          encryptionKey: options.credentialEncryptionKey,
          usersRootPath,
          now,
          stateTtlMs,
          sessionIdleTtlMs,
          sessionAbsoluteTtlMs,
          lifecycle,
          mutateLoginSessions: mutateLoginSessionRecords,
          withUserRevocationBarrier: (userId, operation) =>
            withKeyLock(loginSessionUserLocks, userId, operation),
          loginSessionUserId: async loginSessionId =>
            (await loadSession(loginSessionId, false))?.user.id ?? null,
          rotateLoginSession: loginSessionId =>
            withKeyLock(loginSessionLocks, loginSessionId, async () => {
              const session = await loadSessionUnlocked(loginSessionId, false);
              if (!session) return null;
              const credential =
                session.status === "active" && session.credential
                  ? decryptCredential(
                      session.credential,
                      options.credentialEncryptionKey,
                      digest(loginSessionId),
                    )
                  : null;
              const recordPath = loginSessionPath(sessionsPath, loginSessionId);
              await fs.promises.rm(recordPath, { force: true });
              knownLoginSessionIds.delete(path.basename(recordPath));
              return { credential, userId: session.user.id };
            }),
        });
        return true;
      }
      if (url.pathname === "/api/auth/logout") {
        if (req.method !== "POST") {
          writeJson(res, 405, { error: "Logout requires POST" });
          return true;
        }
        if (!sameOrigin(req.headers.origin, appOrigin)) {
          writeJson(res, 403, { error: "Logout origin is invalid" });
          return true;
        }
        const sessionId = readCookie(req.headers.cookie, LOGIN_COOKIE_NAME);
        if (sessionId) {
          const userId = (await loadSession(sessionId, false))?.user.id;
          if (userId) {
            await withKeyLock(loginSessionUserLocks, userId, async () => {
              const removed = await mutateLoginSessionRecords(() =>
                withKeyLock(
                  loginSessionLocks,
                  sessionId,
                  async () => {
                    const session = await loadSessionUnlocked(sessionId, false);
                    if (!session) return null;
                    const current =
                      session.status === "active" && session.credential
                        ? decryptCredential(
                            session.credential,
                            options.credentialEncryptionKey,
                            digest(sessionId),
                          )
                        : null;
                    const recordPath = loginSessionPath(sessionsPath, sessionId);
                    await fs.promises.rm(recordPath, { force: true });
                    knownLoginSessionIds.delete(path.basename(recordPath));
                    return { credential: current, userId: session.user.id };
                  },
                ),
              );
              lifecycle.disconnectLoginSession(sessionId);
              if (removed?.credential) {
                await options.provider
                  .revokeCredential?.(removed.credential)
                  .catch(() => {});
              }
            });
          } else {
            lifecycle.disconnectLoginSession(sessionId);
          }
        }
        res.setHeader("Set-Cookie", serializeExpiredLoginCookie());
        writeJson(res, 200, { status: "anonymous" });
        return true;
      }
      if (req.method !== "GET" || url.pathname !== "/api/auth/login") return false;

      const returnTo = safeReturnPath(
        url.searchParams.get("returnTo") ?? "/",
        appOrigin,
      );
      if (!returnTo) {
        writeJson(res, 400, { error: "Login return path is invalid" });
        return true;
      }
      const guestBinding = readCookie(req.headers.cookie, "dano_guest");
      const existingFlowBinding = readCookie(
        req.headers.cookie,
        FLOW_COOKIE_NAME,
      );
      const browserBinding =
        guestBinding ?? existingFlowBinding ?? randomOpaqueId();
      const browserBindingHash = digest(browserBinding);
      const anonymousUser = guestBinding
        ? await lifecycle.resolveAnonymousUser(req.headers)
        : null;
      const currentLoginSessionId = readCookie(
        req.headers.cookie,
        LOGIN_COOKIE_NAME,
      );
      const currentLoginSession = currentLoginSessionId
        ? await loadSession(currentLoginSessionId, false)
        : null;
      const replacedLoginSessionId =
        currentLoginSession ? currentLoginSessionId : null;
      const pendingLogin = await withKeyLock(
        browserLocks,
        browserBindingHash,
        async () => {
          const pending = await countPendingTransactions(
            transactionsPath,
            browserBindingHash,
            now(),
            stateTtlMs,
          );
          if (pending >= maxPendingTransactions) return null;
          const state = randomOpaqueId();
          const recordPath = transactionPath(transactionsPath, state);
          await fs.promises.writeFile(
            recordPath,
            `${JSON.stringify({
              version: 1,
              browserBindingHash,
              returnTo,
              ...(anonymousUser
                ? { anonymousUserId: anonymousUser.user.id }
                : {}),
              ...(replacedLoginSessionId
                ? { replacedLoginSessionId }
                : {}),
              createdAt: now(),
            } satisfies StoredLoginTransaction)}\n`,
            { encoding: "utf8", flag: "wx", mode: 0o600 },
          );
          try {
            return {
              state,
              authorizationUrl: options.provider.authorizationUrl({
                state,
                redirectUri: redirectUri.href,
              }),
            };
          } catch (error) {
            await fs.promises.rm(recordPath, { force: true });
            throw error;
          }
        },
      );
      if (!pendingLogin) {
        writeJson(res, 429, { error: "Too many pending login attempts" });
        return true;
      }
      const { state, authorizationUrl } = pendingLogin;
      res.writeHead(303, {
        Location: authorizationUrl.href,
        ...(!guestBinding && !existingFlowBinding
          ? { "Set-Cookie": serializeCookie(FLOW_COOKIE_NAME, browserBinding) }
          : {}),
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
      });
      res.end();
      return true;
    },
    async dispose(): Promise<void> {
      clearInterval(cleanupInterval);
    },
  };
}

interface StoredLoginTransaction {
  readonly version: 1;
  readonly browserBindingHash: string;
  readonly returnTo: string;
  readonly anonymousUserId?: string;
  readonly replacedLoginSessionId?: string;
  readonly createdAt: number;
}

interface StoredEncryptedCredential {
  readonly algorithm: "aes-256-gcm";
  readonly keyVersion: string;
  readonly iv: string;
  readonly ciphertext: string;
  readonly tag: string;
}

interface StoredLoginSession {
  readonly version: 1;
  readonly status: "active" | "reauth_required";
  readonly user: AuthenticatedUser;
  readonly createdAt: number;
  readonly lastActiveAt: number;
  readonly absoluteExpiresAt: number;
  readonly credential?: StoredEncryptedCredential;
}

interface StoredAuthError {
  readonly version: 1;
  readonly code: BridgeLoginErrorCode;
  readonly expiresAt: number;
}

interface RemovedLoginSession {
  readonly credential: ProviderCredential | null;
  readonly userId: string;
}

async function handleCallback(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  url: URL,
  options: {
    transactionsPath: string;
    sessionsPath: string;
    errorsPath: string;
    redirectUri: string;
    provider: OAuthProviderAdapter;
    encryptionKey: OAuthAuthenticationOptions["credentialEncryptionKey"];
    usersRootPath: string;
    now: () => number;
    stateTtlMs: number;
    sessionIdleTtlMs: number;
    sessionAbsoluteTtlMs: number;
    lifecycle: AuthHttpLifecycle;
    mutateLoginSessions<T>(operation: () => Promise<T>): Promise<T>;
    withUserRevocationBarrier<T>(
      userId: string,
      operation: () => Promise<T>,
    ): Promise<T>;
    loginSessionUserId(loginSessionId: string): Promise<string | null>;
    rotateLoginSession(
      loginSessionId: string,
    ): Promise<RemovedLoginSession | null>;
  },
): Promise<void> {
  const state = url.searchParams.get("state") ?? "";
  const browserBinding =
    readCookie(req.headers.cookie, "dano_guest") ??
    readCookie(req.headers.cookie, FLOW_COOKIE_NAME);
  const consumed =
    browserBinding && OPAQUE_ID_PATTERN.test(state)
      ? await consumeTransaction(
          options.transactionsPath,
          state,
          digest(browserBinding),
          options.now(),
          options.stateTtlMs,
        )
      : null;
  if (!consumed) {
    redirectAfterCallback(res, "/");
    return;
  }

  const startedAt = performance.now();
  let stage: OAuthLoginStage = "provider_exchange";
  try {
    const code = url.searchParams.get("code");
    if (url.searchParams.has("error") || !code) {
      redirectAfterCallback(res, consumed.transaction.returnTo);
      return;
    }
    const result = await options.provider.exchangeAuthorizationCode({
      code,
      state,
      redirectUri: options.redirectUri,
    });
    let user = externalIdentityUser(result.identity);
    const credential = validCredential(result.credential);
    stage = "credential_encryption";
    const sessionId = randomOpaqueId();
    const sessionKey = digest(sessionId);
    const createdAt = options.now();
    const session: StoredLoginSession = {
      version: 1,
      status: "active",
      user,
      createdAt,
      lastActiveAt: createdAt,
      absoluteExpiresAt: createdAt + options.sessionAbsoluteTtlMs,
      credential: encryptCredential(
        credential,
        options.encryptionKey,
        sessionKey,
      ),
    };
    const sessionPath = loginSessionPath(options.sessionsPath, sessionId);
    await options.withUserRevocationBarrier(user.id, async () => {
      stage = "credential_validation";
      const validatedIdentity = options.provider.validateCredential
        ? await options.provider.validateCredential(credential)
        : result.identity;
      const validatedUser = externalIdentityUser(validatedIdentity);
      if (validatedUser.id !== user.id) {
        throw new OAuthProviderContractError();
      }
      user = validatedUser;
      try {
        stage = "session_persistence";
        await options.mutateLoginSessions(() =>
          writeLoginSession(sessionPath, { ...session, user: validatedUser }),
        );
      } catch (error) {
        const hasAnotherActiveLoginSession =
          await options.mutateLoginSessions(async () => {
            await fs.promises.rm(sessionPath, { force: true }).catch(() => {});
            return hasActiveLoginSessionForUser(
              options.sessionsPath,
              user.id,
              options.now(),
              options.sessionIdleTtlMs,
            );
          });
        if (!hasAnotherActiveLoginSession) {
          await options.provider.revokeCredential?.(credential).catch(() => {});
        }
        throw error;
      }
    });
    try {
      if (consumed.transaction.anonymousUserId) {
        stage = "anonymous_transfer";
        await options.lifecycle.transferAnonymousUser(
          req.headers,
          consumed.transaction.anonymousUserId,
          {
            user,
            folderPath: await ensureUserFolder(options.usersRootPath, user.id),
          },
        );
      }
      stage = "session_rotation";
      const replacedLoginSessionId =
        consumed.transaction.replacedLoginSessionId;
      const replacedUserId = replacedLoginSessionId
        ? await options.loginSessionUserId(replacedLoginSessionId)
        : null;
      if (replacedLoginSessionId && replacedUserId) {
        await options.withUserRevocationBarrier(replacedUserId, async () => {
          const result = await options.mutateLoginSessions(async () => {
            const removed =
              await options.rotateLoginSession(replacedLoginSessionId);
            const shouldRevoke =
              !!removed?.credential &&
              !(await hasActiveLoginSessionForUser(
                options.sessionsPath,
                removed.userId,
                options.now(),
                options.sessionIdleTtlMs,
              ));
            return { removed, shouldRevoke };
          });
          options.lifecycle.disconnectLoginSession(replacedLoginSessionId);
          if (result.shouldRevoke && result.removed?.credential) {
            await options.provider
              .revokeCredential?.(result.removed.credential)
              .catch(() => {});
          }
        });
      }
    } catch (error) {
      await options.withUserRevocationBarrier(user.id, async () => {
        const shouldRevoke = await options.mutateLoginSessions(async () => {
          await fs.promises.rm(sessionPath, { force: true }).catch(() => {});
          const hasAnotherActiveLoginSession =
            await hasActiveLoginSessionForUser(
              options.sessionsPath,
              user.id,
              options.now(),
              options.sessionIdleTtlMs,
            );
          return !hasAnotherActiveLoginSession;
        });
        if (shouldRevoke) {
          await options.provider.revokeCredential?.(credential).catch(() => {});
        }
      });
      throw error;
    }
    await consumeAuthError(req.headers, options.errorsPath, options.now());
    redirectAfterCallback(
      res,
      consumed.transaction.returnTo,
      sessionId,
      options.sessionAbsoluteTtlMs,
    );
  } catch (error) {
    reportOAuthLoginFailure(stage, error, performance.now() - startedAt);
    const code = classifyOAuthLoginFailure(stage, error);
    const authErrorId = await writeAuthError(
      options.errorsPath,
      code,
      options.now(),
    );
    redirectAfterCallback(
      res,
      consumed.transaction.returnTo,
      undefined,
      options.sessionAbsoluteTtlMs,
      authErrorId,
    );
  } finally {
    await fs.promises.rm(consumed.consumedPath, { force: true }).catch(() => {});
  }
}

async function consumeTransaction(
  transactionsPath: string,
  state: string,
  browserBindingHash: string,
  now: number,
  stateTtlMs: number,
): Promise<{
  transaction: StoredLoginTransaction;
  consumedPath: string;
} | null> {
  const sourcePath = transactionPath(transactionsPath, state);
  const consumedPath = `${sourcePath}.${randomUUID()}.consumed`;
  try {
    await fs.promises.rename(sourcePath, consumedPath);
    const transaction = parseLoginTransaction(
      await fs.promises.readFile(consumedPath, "utf8"),
    );
    if (
      transaction.browserBindingHash !== browserBindingHash ||
      now - transaction.createdAt >= stateTtlMs
    ) {
      await fs.promises.rm(consumedPath, { force: true });
      return null;
    }
    return { transaction, consumedPath };
  } catch {
    await fs.promises.rm(consumedPath, { force: true }).catch(() => {});
    return null;
  }
}

function externalIdentityUser(identity: ExternalIdentity): AuthenticatedUser {
  const username = identity.displayName?.trim() || "已登录用户";
  const avatarUrl = safeAvatarUrl(identity.avatarUrl);
  return {
    id: oauthUserId(identity.userId),
    username,
    ...(avatarUrl ? { avatarUrl } : {}),
  };
}

function safeAvatarUrl(value: string | undefined): string | undefined {
  if (!value?.trim()) return undefined;
  try {
    const url = new URL(value.trim());
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

function validCredential(credential: ProviderCredential): ProviderCredential {
  if (!credential.accessToken?.trim()) {
    throw new Error("Provider credential is invalid");
  }
  return credential;
}

function encryptCredential(
  credential: ProviderCredential,
  key: OAuthAuthenticationOptions["credentialEncryptionKey"],
  sessionKey: string,
): StoredEncryptedCredential {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key.key, iv);
  cipher.setAAD(Buffer.from(`dano-credential:v1:${key.version}:${sessionKey}`));
  const ciphertext = Buffer.concat([
    cipher.update(JSON.stringify(credential), "utf8"),
    cipher.final(),
  ]);
  return {
    algorithm: "aes-256-gcm",
    keyVersion: key.version,
    iv: iv.toString("base64url"),
    ciphertext: ciphertext.toString("base64url"),
    tag: cipher.getAuthTag().toString("base64url"),
  };
}

function decryptCredential(
  stored: StoredEncryptedCredential,
  key: OAuthAuthenticationOptions["credentialEncryptionKey"],
  sessionKey: string,
): ProviderCredential {
  if (stored.algorithm !== "aes-256-gcm" || stored.keyVersion !== key.version) {
    throw new Error("Provider credential key is unavailable");
  }
  const decipher = createDecipheriv(
    "aes-256-gcm",
    key.key,
    Buffer.from(stored.iv, "base64url"),
  );
  decipher.setAAD(Buffer.from(`dano-credential:v1:${key.version}:${sessionKey}`));
  decipher.setAuthTag(Buffer.from(stored.tag, "base64url"));
  const plaintext = Buffer.concat([
    decipher.update(Buffer.from(stored.ciphertext, "base64url")),
    decipher.final(),
  ]).toString("utf8");
  return validCredential(JSON.parse(plaintext) as ProviderCredential);
}

async function writeLoginSession(
  recordPath: string,
  session: StoredLoginSession,
): Promise<void> {
  await writeFileAtomically(recordPath, `${JSON.stringify(session)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    fsync: true,
  });
  await fs.promises.chmod(recordPath, 0o600);
}

function parseLoginTransaction(serialized: string): StoredLoginTransaction {
  const value = JSON.parse(serialized) as Partial<StoredLoginTransaction>;
  if (
    value.version !== 1 ||
    typeof value.browserBindingHash !== "string" ||
    typeof value.returnTo !== "string" ||
    (value.anonymousUserId !== undefined &&
      typeof value.anonymousUserId !== "string") ||
    (value.replacedLoginSessionId !== undefined &&
      (typeof value.replacedLoginSessionId !== "string" ||
        !OPAQUE_ID_PATTERN.test(value.replacedLoginSessionId))) ||
    typeof value.createdAt !== "number"
  ) {
    throw new Error("OAuth login transaction is invalid");
  }
  return value as StoredLoginTransaction;
}

function parseLoginSession(serialized: string): StoredLoginSession {
  const value = JSON.parse(serialized) as Partial<StoredLoginSession>;
  if (
    value.version !== 1 ||
    !value.user ||
    typeof value.user.id !== "string" ||
    typeof value.user.username !== "string" ||
    typeof value.createdAt !== "number" ||
    typeof value.lastActiveAt !== "number" ||
    typeof value.absoluteExpiresAt !== "number" ||
    (value.status !== "active" && value.status !== "reauth_required") ||
    (value.status === "active" && !value.credential)
  ) {
    throw new Error("OAuth Login Session is invalid");
  }
  return value as StoredLoginSession;
}

async function hasActiveLoginSessionForUser(
  sessionsPath: string,
  userId: string,
  currentTime: number,
  idleTtlMs: number,
): Promise<boolean> {
  for (const name of await fs.promises.readdir(sessionsPath)) {
    if (!name.endsWith(".json")) continue;
    try {
      const session = parseLoginSession(
        await fs.promises.readFile(path.join(sessionsPath, name), "utf8"),
      );
      if (
        session.status !== "active" ||
        session.user.id !== userId ||
        currentTime - session.lastActiveAt >= idleTtlMs ||
        currentTime >= session.absoluteExpiresAt
      ) {
        continue;
      }
      return true;
    } catch {
      // Invalid or unreadable records cannot protect an active Login Session.
    }
  }
  return false;
}

async function countPendingTransactions(
  transactionsPath: string,
  browserBindingHash: string,
  now: number,
  stateTtlMs: number,
): Promise<number> {
  let count = 0;
  for (const name of await fs.promises.readdir(transactionsPath)) {
    if (!name.endsWith(".json")) continue;
    const recordPath = path.join(transactionsPath, name);
    try {
      const transaction = parseLoginTransaction(
        await fs.promises.readFile(recordPath, "utf8"),
      );
      if (now - transaction.createdAt >= stateTtlMs) {
        await fs.promises.rm(recordPath, { force: true });
      } else if (transaction.browserBindingHash === browserBindingHash) {
        count += 1;
      }
    } catch {
      await fs.promises.rm(recordPath, { force: true }).catch(() => {});
    }
  }
  return count;
}

async function withKeyLock<T>(
  locks: Map<string, Promise<void>>,
  key: string,
  operation: () => Promise<T>,
): Promise<T> {
  const previous = locks.get(key) ?? Promise.resolve();
  let release: () => void;
  const current = new Promise<void>(resolve => {
    release = resolve;
  });
  const queued = previous.then(() => current);
  locks.set(key, queued);
  await previous;
  try {
    return await operation();
  } finally {
    release!();
    if (locks.get(key) === queued) locks.delete(key);
  }
}

async function writeAuthError(
  errorsPath: string,
  code: StoredAuthError["code"],
  now: number,
): Promise<string> {
  const id = randomOpaqueId();
  await fs.promises.writeFile(
    authErrorPath(errorsPath, id),
    `${JSON.stringify({
      version: 1,
      code,
      expiresAt: now + AUTH_ERROR_TTL_MS,
    } satisfies StoredAuthError)}\n`,
    { encoding: "utf8", flag: "wx", mode: 0o600 },
  );
  return id;
}

async function consumeAuthError(
  headers: http.IncomingHttpHeaders,
  errorsPath: string,
  now: number,
): Promise<StoredAuthError | null> {
  const id = readCookie(headers.cookie, AUTH_ERROR_COOKIE_NAME);
  if (!id) return null;
  const sourcePath = authErrorPath(errorsPath, id);
  const consumedPath = `${sourcePath}.${randomUUID()}.consumed`;
  try {
    await fs.promises.rename(sourcePath, consumedPath);
    const error = parseAuthError(
      await fs.promises.readFile(consumedPath, "utf8"),
    );
    return now < error.expiresAt ? error : null;
  } catch {
    return null;
  } finally {
    await fs.promises.rm(consumedPath, { force: true }).catch(() => {});
  }
}

function parseAuthError(serialized: string): StoredAuthError {
  const value = JSON.parse(serialized) as Partial<StoredAuthError>;
  if (
    value.version !== 1 ||
    !BRIDGE_LOGIN_ERROR_CODES.some(code => code === value.code) ||
    typeof value.expiresAt !== "number"
  ) {
    throw new Error("OAuth login error is invalid");
  }
  return value as StoredAuthError;
}

async function cleanupExpiredRecords(
  transactionsPath: string,
  sessionsPath: string,
  errorsPath: string,
  now: number,
  stateTtlMs: number,
  sessionIdleTtlMs: number,
  onLoginSessionRemoved: (recordName: string) => void = () => {},
): Promise<void> {
  await countPendingTransactions(transactionsPath, "", now, stateTtlMs);
  for (const name of await fs.promises.readdir(sessionsPath)) {
    if (!name.endsWith(".json")) continue;
    const recordPath = path.join(sessionsPath, name);
    try {
      const session = parseLoginSession(
        await fs.promises.readFile(recordPath, "utf8"),
      );
      if (
        now - session.lastActiveAt >= sessionIdleTtlMs ||
        now >= session.absoluteExpiresAt
      ) {
        await fs.promises.rm(recordPath, { force: true });
        onLoginSessionRemoved(name);
      }
    } catch {
      await fs.promises.rm(recordPath, { force: true }).catch(() => {});
      onLoginSessionRemoved(name);
    }
  }
  for (const name of await fs.promises.readdir(errorsPath)) {
    if (!name.endsWith(".json")) continue;
    const recordPath = path.join(errorsPath, name);
    try {
      const error = parseAuthError(
        await fs.promises.readFile(recordPath, "utf8"),
      );
      if (now >= error.expiresAt) {
        await fs.promises.rm(recordPath, { force: true });
      }
    } catch {
      await fs.promises.rm(recordPath, { force: true }).catch(() => {});
    }
  }
}

function randomOpaqueId(): string {
  return randomBytes(32).toString("base64url");
}

function digest(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function transactionPath(rootPath: string, state: string): string {
  return path.join(rootPath, `${digest(state)}.json`);
}

function loginSessionPath(rootPath: string, sessionId: string): string {
  return path.join(rootPath, `${digest(sessionId)}.json`);
}

function authErrorPath(rootPath: string, id: string): string {
  return path.join(rootPath, `${digest(id)}.json`);
}

function readCookie(header: string | undefined, name: string): string | null {
  for (const pair of header?.split(";") ?? []) {
    const separator = pair.indexOf("=");
    if (separator < 0 || pair.slice(0, separator).trim() !== name) continue;
    try {
      const value = decodeURIComponent(pair.slice(separator + 1).trim());
      return OPAQUE_ID_PATTERN.test(value) ? value : null;
    } catch {
      return null;
    }
  }
  return null;
}

function serializeCookie(name: string, value: string): string {
  return `${name}=${encodeURIComponent(value)}; Path=/; HttpOnly; Secure; SameSite=Lax`;
}

function serializeLoginCookie(sessionId: string, absoluteTtlMs: number): string {
  return `${serializeCookie(LOGIN_COOKIE_NAME, sessionId)}; Max-Age=${Math.floor(
    absoluteTtlMs / 1000,
  )}`;
}

function serializeExpiredLoginCookie(): string {
  return `${serializeCookie(LOGIN_COOKIE_NAME, "")}; Max-Age=0`;
}

function serializeAuthErrorCookie(id: string): string {
  return `${serializeCookie(AUTH_ERROR_COOKIE_NAME, id)}; Max-Age=${Math.floor(
    AUTH_ERROR_TTL_MS / 1000,
  )}`;
}

function serializeExpiredAuthErrorCookie(): string {
  return `${serializeCookie(AUTH_ERROR_COOKIE_NAME, "")}; Max-Age=0`;
}

function sameOrigin(value: string | undefined, appOrigin: string): boolean {
  if (!value) return false;
  try {
    return new URL(value).origin === appOrigin;
  } catch {
    return false;
  }
}

function loginReturnPath(returnTo: string): string {
  // returnTo has already been restricted to a local path. Preserve its query
  // and fragment without introducing an origin or exposing login credentials.
  const hashIndex = returnTo.indexOf("#");
  const fragment = hashIndex < 0 ? "" : returnTo.slice(hashIndex);
  const pathAndQuery = hashIndex < 0 ? returnTo : returnTo.slice(0, hashIndex);
  const queryIndex = pathAndQuery.indexOf("?");
  const pathname = queryIndex < 0 ? pathAndQuery : pathAndQuery.slice(0, queryIndex);
  const query = new URLSearchParams(queryIndex < 0 ? "" : pathAndQuery.slice(queryIndex + 1));
  query.set(LOGIN_NEW_CHAT_QUERY_PARAM, "1");
  return `${pathname}?${query}${fragment}`;
}

function redirectAfterCallback(
  res: http.ServerResponse,
  returnTo: string,
  sessionId?: string,
  sessionAbsoluteTtlMs = SESSION_ABSOLUTE_TTL_MS,
  authErrorId?: string,
): void {
  res.writeHead(303, {
    Location: sessionId ? loginReturnPath(returnTo) : returnTo,
    ...(sessionId
      ? { "Set-Cookie": [
          serializeLoginCookie(sessionId, sessionAbsoluteTtlMs),
          serializeExpiredAuthErrorCookie(),
        ] }
      : authErrorId
        ? { "Set-Cookie": serializeAuthErrorCookie(authErrorId) }
      : {}),
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
  });
  res.end();
}

function safeReturnPath(value: string, appOrigin: string): string | null {
  try {
    const target = new URL(value, appOrigin);
    if (
      target.origin !== appOrigin ||
      !value.startsWith("/") ||
      value.startsWith("//")
    ) {
      return null;
    }
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return null;
  }
}

function writeJson(
  res: http.ServerResponse,
  status: number,
  value: unknown,
): void {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
  });
  res.end(JSON.stringify(value));
}
