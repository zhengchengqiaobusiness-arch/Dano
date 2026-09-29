import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { credentialShape, issuedCredential, noteLoginBody, presentToModel, readAuthVault, redactSecrets, rollCredentialUrl, writeAuthVault } from "../src/auth-vault.mjs";
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
  const shape = credentialShape(vault);
  assert.deepEqual(shape, { method: "POST", path: "/session/refresh", query: true });
  assert.equal(JSON.stringify(shape).includes("ref-old"), false);
  const shown = redactSecrets({
    url: "https://example.test/session/refresh?tenantId=1&refreshToken=ref-old",
    refreshToken: "ref-old",
    headers: { Authorization: "Bearer abc12345" },
  });
  assert.equal(shown.url, "https://example.test/session/refresh?tenantId=1&refreshToken=");
  assert.equal(shown.refreshToken, "");
  assert.equal(shown.headers.Authorization, "");
  assert.equal(JSON.stringify(shown).includes("ref-old"), false);
  assert.equal(JSON.stringify(shown).includes("abc12345"), false);
  const credVault = { credential: { method: "POST", url: "https://example.test/session/refresh?refreshToken=ref-old" } };
  const presented = presentToModel({ url: credVault.credential.url, method: "POST" }, credVault);
  assert.equal(presented.replay, "credential");
  assert.equal(presented.url.includes("ref-old"), false);
  const other = presentToModel({ url: "https://example.test/items?name=pen" }, credVault);
  assert.equal(other.replay, undefined);
  assert.equal(other.url, "https://example.test/items?name=pen");
});
