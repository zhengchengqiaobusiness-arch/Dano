import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { issuedCredential, noteLoginBody, readAuthVault, rollCredentialUrl, writeAuthVault } from "../src/auth-vault.mjs";
import { writeAuthLocalFile } from "../src/token-store.mjs";

test("a later header write keeps the credential request, and only the matching query value rolls", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-auth-"));
  const issued = issuedCredential(JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref-old", expiresTime: 9 } }));
  assert.equal(issued.refreshToken, "ref-old");
  assert.equal(issuedCredential(JSON.stringify({ data: { id: 1 } })), null);
  const url = "https://example.test/session/refresh?tenantId=1&refreshToken=ref-old";
  assert.equal(rollCredentialUrl(url, "ref-old", "ref-new"), "https://example.test/session/refresh?tenantId=1&refreshToken=ref-new");
  await noteLoginBody("rec_auth", JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref-old" } }), { method: "post", url });
  await writeAuthVault("rec_auth", { Authorization: "Bearer abc12345" });
  const vault = await readAuthVault("rec_auth");
  assert.equal(vault.credential.method, "POST");
  assert.equal(vault.credential.refresh_token, "ref-old");
  assert.match(vault.headers.Authorization, /^Bearer /);
  const dir = path.join(process.env.CABP_DATA, "pack");
  await writeAuthLocalFile(dir, vault.headers, vault.credential);
  const { readFile } = await import("node:fs/promises");
  const packed = JSON.parse(await readFile(path.join(dir, "config", "auth.local.json"), "utf8"));
  assert.equal(packed.credential.url, url);
  assert.equal(packed.credential.refresh_token, undefined);
});
