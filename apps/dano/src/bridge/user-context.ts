import { createHmac, timingSafeEqual } from "node:crypto";
import * as fs from "node:fs";
import type { IncomingHttpHeaders } from "node:http";
import * as path from "node:path";
import type { BridgeUserSummary } from "../../types/protocol.js";

const USER_ID_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/;
const DEFAULT_COOKIE_NAME = "dano_auth";

export interface AuthenticatedUser {
  readonly id: string;
  readonly username: string;
  readonly avatarUrl?: string;
}

export interface AuthenticatedUserContext {
  readonly user: AuthenticatedUser;
  readonly folderPath: string;
}

export interface UserContextResolver {
  resolve(headers: IncomingHttpHeaders): Promise<AuthenticatedUserContext | null>;
}

export class UserContextError extends Error {
  constructor(
    readonly status: 401 | 403 | 503,
    message: string,
  ) {
    super(message);
  }
}

export interface JwtUserContextResolverOptions {
  readonly runtimeRootPath: string;
  readonly secret: string;
  readonly issuer?: string;
  readonly audience?: string;
  readonly cookieName?: string;
  readonly now?: () => number;
}

interface JwtClaims {
  sub?: unknown;
  name?: unknown;
  preferred_username?: unknown;
  picture?: unknown;
  iss?: unknown;
  aud?: unknown;
  exp?: unknown;
  nbf?: unknown;
}

export function toBrowserUserSummary(user: AuthenticatedUser): BridgeUserSummary {
  return user.avatarUrl
    ? { username: user.username, avatarUrl: user.avatarUrl }
    : { username: user.username };
}

export function createJwtUserContextResolver(
  options: JwtUserContextResolverOptions,
): UserContextResolver {
  const secret = options.secret.trim();
  if (!secret) throw new Error("JWT secret must not be empty");
  const cookieName = options.cookieName?.trim() || DEFAULT_COOKIE_NAME;
  const usersRootPath = path.resolve(options.runtimeRootPath, "users");

  return {
    async resolve(headers) {
      const token = readRequestToken(headers, cookieName);
      if (!token) return null;
      const claims = verifyHs256Jwt(token, {
        secret,
        issuer: options.issuer,
        audience: options.audience,
        now: options.now?.() ?? Date.now(),
      });
      const user = userFromClaims(claims);
      const folderPath = await ensureUserFolder(usersRootPath, user.id);
      return { user, folderPath };
    },
  };
}

function readRequestToken(
  headers: IncomingHttpHeaders,
  cookieName: string,
): string | null {
  const authorization = headers.authorization;
  if (authorization !== undefined) {
    const match = /^Bearer ([^\s]+)$/i.exec(authorization.trim());
    if (!match?.[1]) {
      throw new UserContextError(401, "Authorization bearer token is invalid");
    }
    return match[1];
  }

  const cookieHeader = headers.cookie;
  if (!cookieHeader) return null;
  for (const pair of cookieHeader.split(";")) {
    const separator = pair.indexOf("=");
    if (separator < 0) continue;
    const name = pair.slice(0, separator).trim();
    if (name !== cookieName) continue;
    const value = pair.slice(separator + 1).trim();
    if (!value) throw new UserContextError(401, "Authentication cookie is empty");
    try {
      return decodeURIComponent(value);
    } catch {
      throw new UserContextError(401, "Authentication cookie is invalid");
    }
  }
  return null;
}

function verifyHs256Jwt(
  token: string,
  options: {
    secret: string;
    issuer?: string;
    audience?: string;
    now: number;
  },
): JwtClaims {
  const parts = token.split(".");
  if (parts.length !== 3 || parts.some(part => part.length === 0)) {
    throw new UserContextError(401, "JWT is malformed");
  }
  const [encodedHeader, encodedPayload, encodedSignature] = parts as [string, string, string];
  const header = decodeJwtObject(encodedHeader, "header") as { alg?: unknown };
  if (header.alg !== "HS256") {
    throw new UserContextError(401, "JWT algorithm is not allowed");
  }

  const expectedSignature = createHmac("sha256", options.secret)
    .update(`${encodedHeader}.${encodedPayload}`)
    .digest();
  let actualSignature: Buffer;
  try {
    actualSignature = Buffer.from(encodedSignature, "base64url");
  } catch {
    throw new UserContextError(401, "JWT signature is invalid");
  }
  if (
    actualSignature.length !== expectedSignature.length ||
    !timingSafeEqual(actualSignature, expectedSignature)
  ) {
    throw new UserContextError(401, "JWT signature is invalid");
  }

  const claims = decodeJwtObject(encodedPayload, "payload") as JwtClaims;
  const nowSeconds = Math.floor(options.now / 1000);
  if (typeof claims.exp !== "number" || !Number.isFinite(claims.exp)) {
    throw new UserContextError(401, "JWT expiration is required");
  }
  if (nowSeconds >= claims.exp) throw new UserContextError(401, "JWT has expired");
  if (
    claims.nbf !== undefined &&
    (typeof claims.nbf !== "number" || !Number.isFinite(claims.nbf) || nowSeconds < claims.nbf)
  ) {
    throw new UserContextError(401, "JWT is not active");
  }
  if (options.issuer && claims.iss !== options.issuer) {
    throw new UserContextError(401, "JWT issuer is invalid");
  }
  if (options.audience && !jwtAudienceIncludes(claims.aud, options.audience)) {
    throw new UserContextError(401, "JWT audience is invalid");
  }
  return claims;
}

function decodeJwtObject(encoded: string, part: "header" | "payload"): object {
  try {
    const value = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as unknown;
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return value;
  } catch {
    throw new UserContextError(401, `JWT ${part} is invalid`);
  }
}

function jwtAudienceIncludes(value: unknown, audience: string): boolean {
  return value === audience || (Array.isArray(value) && value.some(item => item === audience));
}

function userFromClaims(claims: JwtClaims): AuthenticatedUser {
  const id = typeof claims.sub === "string" ? claims.sub.trim() : "";
  if (!USER_ID_PATTERN.test(id)) {
    throw new UserContextError(401, "JWT subject is invalid");
  }
  const username = firstNonEmptyString(claims.name, claims.preferred_username, id);
  const avatarUrl = safeAvatarUrl(claims.picture);
  return avatarUrl ? { id, username, avatarUrl } : { id, username };
}

function firstNonEmptyString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  throw new UserContextError(401, "JWT username is invalid");
}

function safeAvatarUrl(value: unknown): string | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  try {
    const url = new URL(value.trim());
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.toString()
      : undefined;
  } catch {
    return undefined;
  }
}

async function ensureUserFolder(usersRootPath: string, userId: string): Promise<string> {
  await ensureDirectoryIsNotSymlink(usersRootPath);
  const realUsersRoot = await fs.promises.realpath(usersRootPath);
  const realRuntimeRoot = await fs.promises.realpath(path.dirname(usersRootPath));
  if (realUsersRoot !== path.join(realRuntimeRoot, path.basename(usersRootPath))) {
    throw new UserContextError(403, "Users root is outside the runtime root");
  }
  const candidate = path.resolve(realUsersRoot, userId);
  if (!isInside(candidate, realUsersRoot)) {
    throw new UserContextError(403, "User Folder is outside the users root");
  }
  await ensureDirectoryIsNotSymlink(candidate, false, "User Folder");
  const realCandidate = await fs.promises.realpath(candidate);
  if (!isInside(realCandidate, realUsersRoot)) {
    throw new UserContextError(403, "User Folder is outside the users root");
  }
  return realCandidate;
}

async function ensureDirectoryIsNotSymlink(
  directoryPath: string,
  recursive = true,
  label = "Users root",
): Promise<void> {
  try {
    const stats = await fs.promises.lstat(directoryPath);
    if (stats.isSymbolicLink() || !stats.isDirectory()) {
      throw new UserContextError(403, `${label} is not a safe directory`);
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    try {
      await fs.promises.mkdir(directoryPath, { recursive, mode: 0o700 });
    } catch (mkdirError) {
      if ((mkdirError as NodeJS.ErrnoException).code !== "EEXIST") {
        throw mkdirError;
      }
    }
    const stats = await fs.promises.lstat(directoryPath);
    if (stats.isSymbolicLink() || !stats.isDirectory()) {
      throw new UserContextError(403, `${label} is not a safe directory`);
    }
  }
}

function isInside(candidate: string, root: string): boolean {
  return candidate.startsWith(`${root}${path.sep}`);
}
