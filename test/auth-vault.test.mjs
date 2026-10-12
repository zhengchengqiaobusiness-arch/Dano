import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { headersFromStorageState, issuedCredential, mergePackedHeaders, noteLoginBody, readAuthVault, redactSecrets, rollCredentialUrl, usableAuthHeaders, writeAuthVault } from "../src/auth-vault.mjs";
import { writeAuthLocalFile } from "../src/token-store.mjs";
import { authSpawnEnv, writeRuntimeConfig, writeSkillFile } from "../src/skillpack/files.mjs";
import { pickLatestCredentialHeaders } from "../src/browser/network.mjs";
import { authStoppedOutput } from "../src/skillpack/verify.mjs";
import { resultLine } from "../src/agent/pi-session.mjs";

test("a later header write keeps the credential request, and only the matching query value rolls", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-auth-"));
  const issued = issuedCredential(JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref-old", expiresTime: 9 } }));
  assert.equal(issued.refreshToken, "ref-old");
  assert.equal(issuedCredential(JSON.stringify({ data: { id: 1 } })), null);
  const url = "https://example.test/session/refresh?tenantId=1&refreshToken=ref-old";
  assert.equal(rollCredentialUrl(url, "ref-old", "ref-new"), "https://example.test/session/refresh?tenantId=1&refreshToken=ref-new");
  await noteLoginBody("rec_auth", JSON.stringify({ data: { accessToken: "abc12345", refreshToken: "ref-old" } }), {
    method: "post",
    url,
    headers: { "tenant-id": "9", authorization: "Bearer abc12345" },
  });
  await writeAuthVault("rec_auth", { Authorization: "Bearer abc12345" });
  const vault = await readAuthVault("rec_auth");
  assert.equal(vault.credential.method, "POST");
  assert.equal(vault.credential.refresh_token, "ref-old");
  assert.equal(vault.credential.headers["Tenant-Id"], "9");
  assert.match(vault.headers.Authorization, /^Bearer /);
  const dir = path.join(process.env.CABP_DATA, "pack");
  await writeAuthLocalFile(dir, vault.headers, vault.credential);
  const { readFile } = await import("node:fs/promises");
  const packed = JSON.parse(await readFile(path.join(dir, "config", "auth.local.json"), "utf8"));
  assert.equal(packed.credential.url, url);
  assert.equal(packed.credential.refresh_token, "ref-old");
  assert.equal(packed.credential.headers["Tenant-Id"], "9");
});

test("a later credential response rolls the unused refresh into the packed url, and packing does not replay it", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-auth-"));
  const first = "https://example.test/session/refresh?refreshToken=ref-old";
  await noteLoginBody("rec_roll", JSON.stringify({ data: { accessToken: "tok-old", refreshToken: "ref-old" } }), {
    method: "POST",
    url: first,
    headers: { "tenant-id": "9" },
  });
  await noteLoginBody("rec_roll", JSON.stringify({ data: { accessToken: "tok-new", refreshToken: "ref-new" } }), {
    method: "POST",
    url: first,
    headers: { "tenant-id": "9" },
  });
  const vault = await readAuthVault("rec_roll");
  assert.match(vault.credential.url, /refreshToken=ref-new/);
  assert.equal(vault.credential.refresh_token, "ref-new");
  let fetches = 0;
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    fetches += 1;
    return new Response(JSON.stringify({ data: { accessToken: "tok-pack", refreshToken: "ref-pack" } }));
  };
  try {
    await writeRuntimeConfig({ id: "rec_roll", tenant: "", subsystem: "app", startUrl: "https://example.test/" }, "app.rec_roll");
  } finally {
    globalThis.fetch = original;
  }
  assert.equal(fetches, 0);
  const { readFile } = await import("node:fs/promises");
  const packed = JSON.parse(await readFile(path.join(process.env.CABP_DATA, "skills", "app.rec_roll", "config", "auth.local.json"), "utf8"));
  assert.match(packed.credential.url, /refreshToken=ref-new/);
  assert.equal(packed.credential.refresh_token, "ref-new");
});

test("packed headers drop hop-by-hop and http2 pseudo names", () => {
  const headers = usableAuthHeaders({
    ":authority": "example.test",
    ":method": "GET",
    ":path": "/admin-api/x",
    ":scheme": "https",
    host: "example.test",
    "content-length": "0",
    connection: "keep-alive",
    "accept-encoding": "gzip",
    cookie: "a=1",
    authorization: "Bearer abcdefghijklmnop",
  });
  assert.equal(Object.keys(headers).some((key) => key.startsWith(":")), false);
  assert.equal(headers.Host, undefined);
  assert.equal(headers["Content-Length"], undefined);
  assert.equal(headers.Connection, undefined);
  assert.equal(headers["Accept-Encoding"], undefined);
  assert.equal(headers.Cookie, "a=1");
  assert.match(headers.Authorization, /^Bearer /);
});

test("storage tokens and live-looking keys become Authorization, and secrets are redacted", () => {
  const headers = headersFromStorageState({
    origins: [{
      origin: "http://example.test",
      localStorage: [
        { name: "user-token", value: "DPORTAL02026062310283464700000101427125!abc" },
        { name: "tenantId", value: "9" },
        { name: "profile", value: "{\"id\":1}" },
      ],
    }],
  });
  assert.match(headers.Authorization, /^Bearer /);
  assert.equal(headers["Tenant-Id"], "9");
  const redacted = redactSecrets('Authorization: Bearer abcdefghijklmnop reason=config/auth.local.json');
  assert.doesNotMatch(redacted, /abcdefghijklmnop/);
  assert.match(redacted, /\[redacted\]/);
});

test("live cookie-only headers keep vault access token and update Cookie", async () => {
  const packed = mergePackedHeaders(
    { Authorization: "Bearer stale-token-value-here", Cookie: "token=old" },
    { Cookie: "token=live-session" },
  );
  assert.match(packed.Authorization, /stale-token-value-here/);
  assert.match(packed.Cookie, /live-session/);
  const withLiveAuth = mergePackedHeaders(
    { Authorization: "Bearer stale-token-value-here" },
    { Authorization: "Bearer fresh-token-value-here" },
  );
  assert.match(withLiveAuth.Authorization, /fresh-token-value-here/);
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-auth-merge-"));
  await writeAuthVault("rec_merge", { Authorization: "Bearer stale-token-value-here", Cookie: "token=old" });
  await writeAuthVault("rec_merge", { Cookie: "token=live-session" });
  const vault = await readAuthVault("rec_merge");
  assert.match(vault.headers.Authorization, /stale-token-value-here/);
  assert.match(vault.headers.Cookie, /live-session/);
});

test("later cookie-only requests do not hide a prior access token", () => {
  const headers = pickLatestCredentialHeaders([
    { request_headers: { Authorization: "Bearer live-token-value-here", Cookie: "a=1" }, status: 200 },
    { request_headers: { Cookie: "a=2" }, status: 200 },
  ], {});
  assert.match(headers.Authorization, /live-token-value-here/);
  assert.match(headers.Cookie, /a=1/);
});

test("missing packed headers counts as auth stop, and api.md path headings are rejected before write", async () => {
  assert.equal(authStoppedOutput('{"ok": false, "error": "凭证缺失：config/auth.local.json 的 headers 里没有 Authorization"}'), true);
  assert.equal(authStoppedOutput("AuthExpired"), true);
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-auth-write-"));
  const rejected = await writeSkillFile("rec_headings", "app.rec_headings", "references/api.md", "## 1. 部门树\nGET /admin-api/system/dept/simple-list\n");
  assert.equal(rejected.ok, false);
  assert.equal(rejected.error, "not_invocable");
  assert.match(rejected.hint, /字段/);
  const line = resultLine({
    content: [{ type: "text", text: JSON.stringify(rejected) }],
  });
  assert.match(line, /失败/);
  assert.match(line, /字段/);
  const dir = await mkdtemp(path.join(os.tmpdir(), "cabp-auth-env-"));
  await writeAuthLocalFile(dir, { Authorization: "Bearer packed-token-value-here", Cookie: "sid=1" });
  const env = authSpawnEnv(path.join(dir, "config", "auth.local.json"));
  const overlay = JSON.parse(env.DANO_AUTH_HEADERS);
  assert.match(overlay.Authorization, /packed-token-value-here/);
  assert.equal(overlay.Cookie, "sid=1");
});
