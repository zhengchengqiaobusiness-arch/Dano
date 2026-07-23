#!/usr/bin/env node
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import {
  readEnvValues,
  updateEnvFile,
} from "./deploy-env-file.mjs";

const DEMO_SUBJECT = "demo-user";
const DEMO_NAME = "演示用户";
const DEMO_TOKEN_LIFETIME_SECONDS = 10 * 365 * 24 * 60 * 60;

export function initializeDemoAuth(envFile) {
  const nowSeconds = Math.floor(Date.now() / 1000);
  const current = existsSync(envFile) ? readFileSync(envFile, "utf8") : "";
  const values = readEnvValues(current);
  const fileSecret = values.get("DANO_AUTH_JWT_SECRET")?.trim() || "";
  const fileToken = values.get("DANO_DEMO_JWT")?.trim() || "";
  const environment = process.env;
  const envSecret = environment.DANO_AUTH_JWT_SECRET?.trim() || "";
  const envToken = environment.DANO_DEMO_JWT?.trim() || "";
  const issuer = effectiveValue(
    environment.DANO_AUTH_JWT_ISSUER,
    values.get("DANO_AUTH_JWT_ISSUER"),
  );
  const audience = effectiveValue(
    environment.DANO_AUTH_JWT_AUDIENCE,
    values.get("DANO_AUTH_JWT_AUDIENCE"),
  );

  if (Boolean(fileSecret) !== Boolean(fileToken)) {
    throw new Error(
      "DANO_AUTH_JWT_SECRET and DANO_DEMO_JWT must both be set or both be absent",
    );
  }
  if (Boolean(envSecret) !== Boolean(envToken)) {
    throw new Error(
      "DANO_AUTH_JWT_SECRET and DANO_DEMO_JWT must both be set or both be absent",
    );
  }
  if (
    fileSecret &&
    envSecret &&
    (fileSecret !== envSecret || fileToken !== envToken)
  ) {
    throw new Error(
      "environment Demo credentials do not match the persisted pair",
    );
  }

  let nextSecret = fileSecret || envSecret;
  let nextToken = fileToken || envToken;
  let initialized = false;
  if (!nextSecret) {
    nextSecret = randomBytes(32).toString("base64url");
    nextToken = signJwt({
      sub: DEMO_SUBJECT,
      name: DEMO_NAME,
      exp: nowSeconds + DEMO_TOKEN_LIFETIME_SECONDS,
      ...(issuer ? { iss: issuer } : {}),
      ...(audience ? { aud: audience } : {}),
    }, nextSecret);
    initialized = true;
  }

  const claims = verifyDemoJwt(nextToken, nextSecret, nowSeconds, {
    issuer,
    audience,
  });
  const expires = new Date(claims.exp * 1000).toUTCString();
  updateEnvFile(envFile, {
    DANO_AUTH_JWT_SECRET: nextSecret,
    DANO_DEMO_JWT: nextToken,
    DANO_DEMO_COOKIE_EXPIRES: expires,
  });

  return { initialized, expiresAt: claims.exp };
}

function signJwt(claims, secret) {
  const header = encodeJwtPart({ alg: "HS256", typ: "JWT" });
  const payload = encodeJwtPart(claims);
  const input = `${header}.${payload}`;
  const signature = createHmac("sha256", secret).update(input).digest("base64url");
  return `${input}.${signature}`;
}

function verifyDemoJwt(token, secret, nowSeconds, constraints) {
  const parts = token.split(".");
  if (parts.length !== 3 || parts.some(part => part.length === 0)) {
    throw new Error("DANO_DEMO_JWT is malformed");
  }
  const [encodedHeader, encodedPayload, encodedSignature] = parts;
  const header = decodeJwtPart(encodedHeader, "header");
  const claims = decodeJwtPart(encodedPayload, "claims");
  if (header.alg !== "HS256") {
    throw new Error("DANO_DEMO_JWT must use HS256");
  }

  const expected = createHmac("sha256", secret)
    .update(`${encodedHeader}.${encodedPayload}`)
    .digest();
  let actual;
  try {
    actual = Buffer.from(encodedSignature, "base64url");
  } catch {
    throw new Error("DANO_DEMO_JWT signature is invalid");
  }
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    throw new Error("DANO_DEMO_JWT signature is invalid");
  }
  if (claims.sub !== DEMO_SUBJECT || claims.name !== DEMO_NAME) {
    throw new Error("DANO_DEMO_JWT demo identity claims are invalid");
  }
  if (constraints.issuer && claims.iss !== constraints.issuer) {
    throw new Error("DANO_DEMO_JWT issuer is invalid");
  }
  if (
    constraints.audience &&
    claims.aud !== constraints.audience &&
    !(Array.isArray(claims.aud) && claims.aud.includes(constraints.audience))
  ) {
    throw new Error("DANO_DEMO_JWT audience is invalid");
  }
  if (!Number.isInteger(claims.exp)) {
    throw new Error("DANO_DEMO_JWT expiration is invalid");
  }
  if (claims.exp <= nowSeconds) {
    throw new Error("DANO_DEMO_JWT has expired");
  }
  return claims;
}

function encodeJwtPart(value) {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

function decodeJwtPart(value, label) {
  try {
    const parsed = JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error();
    }
    return parsed;
  } catch {
    throw new Error(`DANO_DEMO_JWT ${label} is invalid`);
  }
}

function effectiveValue(environmentValue, fileValue) {
  return environmentValue?.trim() || fileValue?.trim() || "";
}

function readEnvFileArg(args) {
  const values = args[0] === "--" ? args.slice(1) : args;
  if (values.length === 0) return ".env";
  if (values.length === 2 && values[0] === "--file" && values[1]) {
    return values[1];
  }
  throw new Error("Usage: node scripts/init-demo-auth.mjs [--file .env]");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const envFile = readEnvFileArg(process.argv.slice(2));
    const result = initializeDemoAuth(envFile);
    console.log(
      result.initialized
        ? `[init-demo-auth] initialized Demo authentication in ${envFile}`
        : `[init-demo-auth] verified existing Demo authentication in ${envFile}`,
    );
  } catch (error) {
    console.error(
      `[init-demo-auth] ${error instanceof Error ? error.message : error}`,
    );
    process.exitCode = 1;
  }
}
