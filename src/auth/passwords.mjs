import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

const SCRYPT_N = 16384;
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const KEY_LENGTH = 64;
const SALT_LENGTH = 16;
const MAXMEM = 64 * 1024 * 1024;

function b64(value) {
  return Buffer.from(value).toString("base64url");
}

function unb64(value) {
  return Buffer.from(String(value || ""), "base64url");
}

export function hashPassword(password) {
  const salt = randomBytes(SALT_LENGTH);
  const derived = scryptSync(String(password), salt, KEY_LENGTH, {
    N: SCRYPT_N,
    r: SCRYPT_R,
    p: SCRYPT_P,
    maxmem: MAXMEM,
  });
  return ["scrypt", String(SCRYPT_N), String(SCRYPT_R), String(SCRYPT_P), b64(salt), b64(derived)].join("$");
}

export function verifyPassword(password, stored) {
  const parts = String(stored || "").split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;
  const n = Number(parts[1]);
  const r = Number(parts[2]);
  const p = Number(parts[3]);
  if (!Number.isInteger(n) || !Number.isInteger(r) || !Number.isInteger(p) || n <= 0 || r <= 0 || p <= 0) {
    return false;
  }
  let salt;
  let expected;
  try {
    salt = unb64(parts[4]);
    expected = unb64(parts[5]);
  } catch {
    return false;
  }
  if (!salt.length || !expected.length) return false;
  try {
    const derived = scryptSync(String(password), salt, expected.length, { N: n, r, p, maxmem: MAXMEM });
    return derived.length === expected.length && timingSafeEqual(derived, expected);
  } catch {
    return false;
  }
}

export const DUMMY_PASSWORD_HASH = hashPassword(randomBytes(16).toString("hex"));
