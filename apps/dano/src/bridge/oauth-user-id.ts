import { createHash } from "node:crypto";

/** Same immutable provider subject maps to the same Dano owner after rotation. */
export function oauthUserId(subject: string): string {
  return `oauth_${createHash("sha256").update(subject).digest("hex")}`;
}
