import { createHash, randomBytes } from "node:crypto";
import * as fs from "node:fs";
import type { IncomingHttpHeaders } from "node:http";
import * as path from "node:path";
import { writeFile as writeFileAtomically } from "atomically";
import { ensureSafeDirectory } from "./safe-directory.js";
import {
  ensureUserFolder,
  toBrowserUserSummary,
  type AuthenticatedUserContext,
  type AuthenticatedUserContextResolver,
  type ClientUserResolution,
  type UserContext,
  type UserContextResolver,
  UserContextError,
} from "./user-context.js";

const DEFAULT_GUEST_COOKIE_NAME = "dano_guest";
const DEFAULT_ACTIVITY_WRITE_INTERVAL_MS = 60 * 1000;
const OPAQUE_GUEST_ID_PATTERN = /^[A-Za-z0-9_-]{43}$/;

interface StoredGuestSession {
  readonly userId: string;
  readonly lastActiveAt: number;
  readonly cleanupPending?: true;
}

export interface AnonymousUserContextResolverOptions {
  readonly runtimeRootPath: string;
  readonly authenticatedResolver?: AuthenticatedUserContextResolver;
  readonly cookieName?: string;
  readonly secureCookie: boolean;
  readonly now?: () => number;
  readonly activityWriteIntervalMs?: number;
}

export interface AnonymousUserSweepOptions {
  readonly idleTtlMs: number;
  readonly beginCleanup: (userId: string) => (() => void) | null;
  readonly cleanupUser: (userContext: UserContext) => Promise<void>;
  readonly retainUser?: (userContext: UserContext) => boolean;
}

export interface AnonymousUserSweepResult {
  readonly removed: number;
  readonly skipped: number;
  readonly failed: number;
}

export interface AnonymousUserContextResolver extends UserContextResolver {
  completeAnonymousUserCleanup(userId: string): Promise<boolean>;
  touchAnonymousUser(userId: string): Promise<boolean>;
  sweepExpired(
    options: AnonymousUserSweepOptions,
  ): Promise<AnonymousUserSweepResult>;
}

export function createAnonymousUserContextResolver(
  options: AnonymousUserContextResolverOptions,
): AnonymousUserContextResolver {
  const cookieName = options.cookieName?.trim() || DEFAULT_GUEST_COOKIE_NAME;
  const now = options.now ?? Date.now;
  const activityWriteIntervalMs =
    options.activityWriteIntervalMs ?? DEFAULT_ACTIVITY_WRITE_INTERVAL_MS;
  if (
    !Number.isSafeInteger(activityWriteIntervalMs) ||
    activityWriteIntervalMs < 0
  ) {
    throw new Error(
      "Anonymous User activity write interval must be a non-negative integer",
    );
  }
  const activity = new Map<
    string,
    { lastActiveAt: number; lastPersistedAt: number }
  >();
  const activityWrites = new Map<string, Promise<boolean>>();
  const revokingUsers = new Set<string>();
  const recordPaths = new Map<string, string>();
  const usersRootPath = path.resolve(options.runtimeRootPath, "users");
  const sessionsRootPath = path.resolve(
    options.runtimeRootPath,
    "anonymous-sessions",
  );

  const resolveAuthenticated = async (
    headers: IncomingHttpHeaders,
  ): Promise<AuthenticatedUserContext | null> => {
    try {
      return (await options.authenticatedResolver?.resolve(headers)) ?? null;
    } catch (error) {
      if (error instanceof UserContextError && error.status === 401) return null;
      throw error;
    }
  };

  const resolveAuthenticatedClient = async (
    headers: IncomingHttpHeaders,
    method: "resolveForClient" | "resolveExisting",
  ): Promise<ClientUserResolution | null> => {
    try {
      return (
        (await options.authenticatedResolver?.[method]?.(headers)) ?? null
      );
    } catch (error) {
      if (error instanceof UserContextError && error.status === 401) return null;
      throw error;
    }
  };

  const resolveGuest = async (
    headers: IncomingHttpHeaders,
  ): Promise<UserContext | null> => {
    const guestId = readCookie(headers, cookieName);
    if (!guestId || !OPAQUE_GUEST_ID_PATTERN.test(guestId)) return null;
    const stored = await readStoredSession(sessionsRootPath, guestId);
    if (!stored || stored.cleanupPending) return null;
    rememberActivity(activity, stored);
    recordPaths.set(
      stored.userId,
      guestSessionPath(sessionsRootPath, guestId),
    );
    return {
      user: { id: stored.userId },
      folderPath: await ensureUserFolder(usersRootPath, stored.userId),
    };
  };

  return {
    async resolve(headers) {
      const authenticated = await resolveAuthenticated(headers);
      return authenticated ?? resolveGuest(headers);
    },

    async resolveForClient(headers) {
      const authenticatedResolutionFromResolver = await resolveAuthenticatedClient(
        headers,
        "resolveForClient",
      );
      if (
        authenticatedResolutionFromResolver?.authentication.status ===
        "authenticated"
      ) {
        return authenticatedResolutionFromResolver;
      }
      const authenticated = await resolveAuthenticated(headers);
      if (authenticated) return authenticatedResolution(authenticated);

      const guest = await resolveGuest(headers);
      if (guest) {
        return { userContext: guest, authentication: { status: "anonymous" } };
      }

      const createdAt = now();
      const created = await createGuestSession(
        sessionsRootPath,
        usersRootPath,
        createdAt,
      );
      activity.set(created.userContext.user.id, {
        lastActiveAt: createdAt,
        lastPersistedAt: createdAt,
      });
      recordPaths.set(created.userContext.user.id, created.recordPath);
      return {
        userContext: created.userContext,
        authentication: { status: "anonymous" },
        setCookie: serializeGuestCookie(
          cookieName,
          created.guestId,
          options.secureCookie,
        ),
      };
    },

    async resolveExisting(headers) {
      const authenticatedResolutionFromResolver = await resolveAuthenticatedClient(
        headers,
        "resolveExisting",
      );
      if (authenticatedResolutionFromResolver) {
        return authenticatedResolutionFromResolver;
      }
      const authenticated = await resolveAuthenticated(headers);
      if (authenticated) return authenticatedResolution(authenticated);
      const guest = await resolveGuest(headers);
      return guest
        ? { userContext: guest, authentication: { status: "anonymous" } }
        : null;
    },

    resolveAnonymous: resolveGuest,

    async revokeAnonymous(headers, expectedUserId) {
      const guestId = readCookie(headers, cookieName);
      if (!guestId || !OPAQUE_GUEST_ID_PATTERN.test(guestId)) return false;
      const stored = await readStoredSession(sessionsRootPath, guestId);
      if (
        !stored ||
        stored.cleanupPending ||
        stored.userId !== expectedUserId
      ) {
        return false;
      }
      if (revokingUsers.has(expectedUserId)) return false;
      revokingUsers.add(expectedUserId);
      const recordPath = guestSessionPath(sessionsRootPath, guestId);
      const previous = activityWrites.get(expectedUserId) ?? Promise.resolve(true);
      const revoking = previous.then(async () => {
        const current = await readStoredSessionPath(recordPath);
        if (
          !current ||
          current.cleanupPending ||
          current.userId !== expectedUserId
        ) {
          return false;
        }
        await writeStoredSession(recordPath, {
          ...current,
          cleanupPending: true,
        });
        forgetUserActivity(
          activity,
          activityWrites,
          recordPaths,
          expectedUserId,
        );
        return true;
      });
      activityWrites.set(expectedUserId, revoking);
      try {
        return await revoking;
      } finally {
        revokingUsers.delete(expectedUserId);
        if (activityWrites.get(expectedUserId) === revoking) {
          activityWrites.delete(expectedUserId);
        }
      }
    },

    async completeAnonymousUserCleanup(userId) {
      const record = await findStoredSessionByUserId(
        sessionsRootPath,
        userId,
        recordPaths.get(userId),
      );
      if (!record?.session.cleanupPending) return false;
      await fs.promises.rm(record.path);
      forgetUserActivity(activity, activityWrites, recordPaths, userId);
      return true;
    },

    async touchAnonymousUser(userId) {
      if (revokingUsers.has(userId)) return false;
      const knownState = activity.get(userId);
      if (knownState && recordPaths.has(userId)) {
        knownState.lastActiveAt = Math.max(knownState.lastActiveAt, now());
        if (
          knownState.lastActiveAt - knownState.lastPersistedAt <
          activityWriteIntervalMs
        ) {
          return true;
        }
      }
      const record = await findStoredSessionByUserId(
        sessionsRootPath,
        userId,
        recordPaths.get(userId),
      );
      if (!record || record.session.cleanupPending) {
        activity.delete(userId);
        recordPaths.delete(userId);
        return false;
      }
      if (revokingUsers.has(userId)) return false;
      recordPaths.set(userId, record.path);
      const state = rememberActivity(activity, record.session);
      state.lastActiveAt = Math.max(state.lastActiveAt, now());
      if (
        state.lastActiveAt - state.lastPersistedAt <
        activityWriteIntervalMs
      ) {
        return true;
      }
      const previous = activityWrites.get(userId) ?? Promise.resolve(true);
      const writing = previous.then(async () => {
        const current = await findStoredSessionByUserId(
          sessionsRootPath,
          userId,
          recordPaths.get(userId),
        );
        if (!current) return false;
        const latest = rememberActivity(activity, current.session);
        if (
          latest.lastActiveAt - latest.lastPersistedAt <
          activityWriteIntervalMs
        ) {
          return true;
        }
        const persistedAt = latest.lastActiveAt;
        await writeStoredSession(current.path, {
          ...current.session,
          lastActiveAt: persistedAt,
        });
        latest.lastPersistedAt = Math.max(latest.lastPersistedAt, persistedAt);
        return true;
      });
      activityWrites.set(userId, writing);
      try {
        return await writing;
      } finally {
        if (activityWrites.get(userId) === writing) {
          activityWrites.delete(userId);
        }
      }
    },

    async sweepExpired(sweepOptions) {
      const result = { removed: 0, skipped: 0, failed: 0 };
      const records = await listStoredSessions(sessionsRootPath);
      for (const record of records) {
        recordPaths.set(record.session.userId, record.path);
        if (
          !record.session.cleanupPending &&
          now() - effectiveLastActiveAt(activity, record.session) <
            sweepOptions.idleTtlMs
        ) {
          continue;
        }
        if (
          sweepOptions.retainUser?.({
            user: { id: record.session.userId },
            folderPath: path.join(usersRootPath, record.session.userId),
          })
        ) {
          result.skipped += 1;
          continue;
        }
        const release = sweepOptions.beginCleanup(record.session.userId);
        if (!release) {
          result.skipped += 1;
          continue;
        }
        try {
          const current = await readStoredSessionPath(record.path);
          if (
            !current ||
            current.userId !== record.session.userId ||
            (!current.cleanupPending &&
              now() - effectiveLastActiveAt(activity, current) <
                sweepOptions.idleTtlMs)
          ) {
            result.skipped += 1;
            continue;
          }
          const userContext: UserContext = {
            user: { id: current.userId },
            folderPath: await ensureUserFolder(usersRootPath, current.userId),
          };
          await sweepOptions.cleanupUser(userContext);
          await fs.promises.rm(record.path);
          forgetUserActivity(
            activity,
            activityWrites,
            recordPaths,
            current.userId,
          );
          result.removed += 1;
        } catch {
          result.failed += 1;
        } finally {
          release();
        }
      }
      return result;
    },
  };
}

function forgetUserActivity(
  activity: Map<
    string,
    { lastActiveAt: number; lastPersistedAt: number }
  >,
  activityWrites: Map<string, Promise<boolean>>,
  recordPaths: Map<string, string>,
  userId: string,
): void {
  activity.delete(userId);
  activityWrites.delete(userId);
  recordPaths.delete(userId);
}

function rememberActivity(
  activity: Map<string, { lastActiveAt: number; lastPersistedAt: number }>,
  session: StoredGuestSession,
): { lastActiveAt: number; lastPersistedAt: number } {
  const existing = activity.get(session.userId);
  if (existing) {
    existing.lastActiveAt = Math.max(
      existing.lastActiveAt,
      session.lastActiveAt,
    );
    existing.lastPersistedAt = Math.max(
      existing.lastPersistedAt,
      session.lastActiveAt,
    );
    return existing;
  }
  const created = {
    lastActiveAt: session.lastActiveAt,
    lastPersistedAt: session.lastActiveAt,
  };
  activity.set(session.userId, created);
  return created;
}

function effectiveLastActiveAt(
  activity: Map<string, { lastActiveAt: number }>,
  session: StoredGuestSession,
): number {
  return Math.max(
    session.lastActiveAt,
    activity.get(session.userId)?.lastActiveAt ?? session.lastActiveAt,
  );
}

function authenticatedResolution(
  userContext: AuthenticatedUserContext,
): ClientUserResolution {
  const user = userContext.user;
  return {
    userContext,
    authentication: {
      status: "authenticated",
      user: toBrowserUserSummary(user),
    },
  };
}

async function createGuestSession(
  sessionsRootPath: string,
  usersRootPath: string,
  createdAt: number,
): Promise<{ guestId: string; recordPath: string; userContext: UserContext }> {
  await ensureSafeDirectory(sessionsRootPath, {
    recursive: true,
    unsafeDirectoryError: () =>
      new Error("Anonymous sessions root is not a safe directory"),
  });
  const guestId = randomBytes(32).toString("base64url");
  const userId = `anonymous_${randomBytes(24).toString("hex")}`;
  const recordPath = guestSessionPath(sessionsRootPath, guestId);
  await fs.promises.writeFile(
    recordPath,
    `${JSON.stringify({ userId, lastActiveAt: createdAt } satisfies StoredGuestSession)}\n`,
    { encoding: "utf8", flag: "wx", mode: 0o600 },
  );
  return {
    guestId,
    recordPath,
    userContext: {
      user: { id: userId },
      folderPath: await ensureUserFolder(usersRootPath, userId),
    },
  };
}

async function readStoredSession(
  sessionsRootPath: string,
  guestId: string,
): Promise<StoredGuestSession | null> {
  try {
    const value = JSON.parse(
      await fs.promises.readFile(
        guestSessionPath(sessionsRootPath, guestId),
        "utf8",
      ),
    ) as unknown;
    if (!isStoredGuestSession(value)) return null;
    return value;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    if (error instanceof SyntaxError) return null;
    throw error;
  }
}

async function readStoredSessionPath(
  recordPath: string,
): Promise<StoredGuestSession | null> {
  try {
    const value = JSON.parse(
      await fs.promises.readFile(recordPath, "utf8"),
    ) as unknown;
    return isStoredGuestSession(value) ? value : null;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    if (error instanceof SyntaxError) return null;
    throw error;
  }
}

async function listStoredSessions(
  sessionsRootPath: string,
): Promise<Array<{ path: string; session: StoredGuestSession }>> {
  let entries: fs.Dirent[];
  try {
    entries = await fs.promises.readdir(sessionsRootPath, {
      withFileTypes: true,
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const records: Array<{ path: string; session: StoredGuestSession }> = [];
  for (const entry of entries) {
    if (!entry.isFile() || !/^[a-f0-9]{64}\.json$/.test(entry.name)) continue;
    const recordPath = path.join(sessionsRootPath, entry.name);
    const session = await readStoredSessionPath(recordPath);
    if (session) records.push({ path: recordPath, session });
  }
  return records;
}

async function findStoredSessionByUserId(
  sessionsRootPath: string,
  userId: string,
  knownRecordPath?: string,
): Promise<{ path: string; session: StoredGuestSession } | null> {
  if (knownRecordPath) {
    const session = await readStoredSessionPath(knownRecordPath);
    if (session?.userId === userId) {
      return { path: knownRecordPath, session };
    }
  }
  const records = await listStoredSessions(sessionsRootPath);
  return records.find(record => record.session.userId === userId) ?? null;
}

async function writeStoredSession(
  recordPath: string,
  session: StoredGuestSession,
): Promise<void> {
  await writeFileAtomically(recordPath, `${JSON.stringify(session)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    fsync: true,
  });
  await fs.promises.chmod(recordPath, 0o600);
}

function isStoredGuestSession(value: unknown): value is StoredGuestSession {
  return (
    typeof value === "object" &&
    value !== null &&
    "userId" in value &&
    typeof value.userId === "string" &&
    /^anonymous_[a-f0-9]{48}$/.test(value.userId) &&
    "lastActiveAt" in value &&
    typeof value.lastActiveAt === "number" &&
    Number.isFinite(value.lastActiveAt) &&
    value.lastActiveAt >= 0 &&
    (!("cleanupPending" in value) || value.cleanupPending === true)
  );
}

function guestSessionPath(sessionsRootPath: string, guestId: string): string {
  const key = createHash("sha256").update(guestId).digest("hex");
  return path.join(sessionsRootPath, `${key}.json`);
}

function readCookie(
  headers: IncomingHttpHeaders,
  cookieName: string,
): string | null {
  for (const pair of headers.cookie?.split(";") ?? []) {
    const separator = pair.indexOf("=");
    if (separator < 0 || pair.slice(0, separator).trim() !== cookieName) continue;
    const value = pair.slice(separator + 1).trim();
    try {
      return decodeURIComponent(value);
    } catch {
      return null;
    }
  }
  return null;
}

function serializeGuestCookie(
  cookieName: string,
  guestId: string,
  secure: boolean,
): string {
  return [
    `${cookieName}=${encodeURIComponent(guestId)}`,
    "Path=/",
    "HttpOnly",
    ...(secure ? ["Secure"] : []),
    "SameSite=Lax",
  ].join("; ");
}
