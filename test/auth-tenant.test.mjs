import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { createApp, listen } from "../src/server.mjs";
import { AUTH_MAX_FAILURES } from "../src/auth/throttle.mjs";
import { validatePassword, PasswordPolicyError } from "../src/auth/policy.mjs";
import { hashPassword, verifyPassword } from "../src/auth/passwords.mjs";
import { AuthError, changePassword, login, registerTenant } from "../src/auth/service.mjs";
import { writeTokenRecord } from "../src/token-store.mjs";
import { upsertCatalog } from "../src/skillpack/catalog.mjs";
import { PASSWORD, authHeader, jsonRequest, makeTenant, withData } from "./helpers.mjs";

async function listenApp() {
  const server = createApp();
  const address = await listen(server, 0);
  return { server, port: address.port };
}

test("password policy matches Dano: length, weak list, identity substring", () => {
  assert.throws(() => validatePassword("short"), PasswordPolicyError);
  assert.throws(() => validatePassword("short1234"), /至少 12 位/);
  assert.throws(() => validatePassword("password1234"), /过于常见/);
  assert.throws(() => validatePassword("AcmeAcmeAcme", { username: "acme" }), /用户名/);
  assert.throws(() => validatePassword("contoso-contoso", { tenant: "contoso" }), /租户名/);
  validatePassword(PASSWORD, { username: "solo", tenant: "solo" });
  validatePassword("kf7-mq2x", { minLength: 8 });
  assert.throws(() => validatePassword("kf7-mq2", { minLength: 8 }), PasswordPolicyError);
});

test("scrypt hashes verify and reject a wrong password", () => {
  const stored = hashPassword(PASSWORD);
  assert.equal(verifyPassword(PASSWORD, stored), true);
  assert.equal(verifyPassword("definitely-wrong-here", stored), false);
  assert.equal(verifyPassword(PASSWORD, "not-a-hash"), false);
});

test("login, isolation, lockout and password change follow Dano routes", async () => {
  await withData(async (root) => {
    const { server, port } = await listenApp();
    try {
      const weak = await jsonRequest(port, "POST", "/tenants", {
        tenant: "weak",
        username: "weak",
        password: "short",
      });
      assert.equal(weak.status, 400);
      assert.match(String(weak.data.detail), /至少 12 位/);

      const nopass = await jsonRequest(port, "POST", "/tenants", { tenant: "nopass" });
      assert.equal(nopass.status, 200);
      assert.match(nopass.data.api_key, /^dk_/);
      assert.equal(nopass.data.password_hash, undefined);

      const first = await makeTenant(port, "solo");
      const again = await jsonRequest(port, "POST", "/tenants", {
        tenant: "solo",
        username: "solo",
        password: PASSWORD,
      });
      assert.equal(again.status, 200);
      assert.equal(again.data.api_key, first.api_key);

      const ok = await jsonRequest(port, "POST", "/auth/login", { username: "solo", password: PASSWORD });
      assert.equal(ok.status, 200);
      assert.equal(ok.data.api_key, first.api_key);
      assert.equal(ok.data.tenant, "solo");

      const missing = await jsonRequest(port, "POST", "/auth/login", {
        username: "nobody",
        password: PASSWORD,
      });
      const wrong = await jsonRequest(port, "POST", "/auth/login", {
        username: "solo",
        password: "definitely-wrong-here",
      });
      assert.equal(missing.status, 401);
      assert.equal(wrong.status, 401);
      assert.equal(missing.data.detail, wrong.data.detail);
      assert.match(String(wrong.data.detail), /用户名或密码错误/);

      const keyed = await jsonRequest(port, "GET", "/v1/skills", undefined, authHeader(first.api_key));
      assert.equal(keyed.status, 200);
      const noKey = await jsonRequest(port, "GET", "/v1/skills");
      assert.equal(noKey.status, 401);
      assert.equal(noKey.data.detail, "缺少 X-Tenant-Key");
      const badKey = await jsonRequest(port, "GET", "/v1/skills", undefined, authHeader("dk_deadbeef"));
      assert.equal(badKey.status, 401);
      assert.equal(badKey.data.detail, "X-Tenant-Key 无效");

      await mkdir(path.join(root, "skills", "app.rec_aaaa"), { recursive: true });
      await mkdir(path.join(root, "recordings", "rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), { recursive: true });
      await writeFile(path.join(root, "recordings", "rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "state.json"), "{}");
      await upsertCatalog({
        id: "rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        skillId: "app.rec_aaaa",
        tenant: "solo",
        subsystem: "app",
        title: "solo skill",
        skillDir: path.join(root, "skills", "app.rec_aaaa"),
      });
      await upsertCatalog({
        id: "rec_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        skillId: "app.rec_bbbb",
        tenant: "otherco",
        subsystem: "app",
        title: "other skill",
        skillDir: path.join(root, "skills", "app.rec_bbbb"),
      });
      const other = await makeTenant(port, "otherco");
      const soloList = await jsonRequest(port, "GET", "/v1/skills", undefined, authHeader(first.api_key));
      const otherList = await jsonRequest(port, "GET", "/v1/skills", undefined, authHeader(other.api_key));
      assert.deepEqual(soloList.data.items.map((row) => row.skill_id), ["app.rec_aaaa"]);
      assert.deepEqual(otherList.data.items.map((row) => row.skill_id), ["app.rec_bbbb"]);

      const stolen = await jsonRequest(
        port,
        "POST",
        "/v1/skills/app.rec_bbbb/freeze",
        {},
        authHeader(first.api_key),
      );
      assert.equal(stolen.status, 404);
      assert.match(String(stolen.data.detail), /本公司无此 Skill/);

      await writeTokenRecord("solo", "oa", { Authorization: "Bearer solo-secret-token" });
      await writeTokenRecord("otherco", "oa", { Authorization: "Bearer other-secret-token" });
      const ownToken = await jsonRequest(
        port,
        "GET",
        "/v1/settings/token?tenant=solo&subsystem=oa",
        undefined,
        authHeader(first.api_key),
      );
      assert.equal(ownToken.status, 200);
      assert.equal(ownToken.data.has_token, true);
      assert.doesNotMatch(JSON.stringify(ownToken.data.headers), /solo-secret-token/);
      const peek = await jsonRequest(
        port,
        "GET",
        "/v1/settings/token?tenant=otherco&subsystem=oa",
        undefined,
        authHeader(first.api_key),
      );
      assert.equal(peek.status, 403);
      assert.match(String(peek.data.detail), /不能读取其他租户的 token/);

      const changed = await jsonRequest(port, "POST", "/auth/change-password", {
        old_password: PASSWORD,
        new_password: "another-good-password",
      }, authHeader(first.api_key));
      assert.equal(changed.status, 200);
      const oldLogin = await jsonRequest(port, "POST", "/auth/login", { username: "solo", password: PASSWORD });
      assert.equal(oldLogin.status, 401);
      const newLogin = await jsonRequest(port, "POST", "/auth/login", {
        username: "solo",
        password: "another-good-password",
      });
      assert.equal(newLogin.status, 200);

      const weakPw = await jsonRequest(port, "POST", "/auth/change-password", {
        old_password: "another-good-password",
        new_password: "short12",
      }, authHeader(first.api_key));
      assert.equal(weakPw.status, 400);

      const locker = await makeTenant(port, "locked");
      for (let i = 0; i < AUTH_MAX_FAILURES; i += 1) {
        const fail = await jsonRequest(port, "POST", "/auth/login", {
          username: "locked",
          password: "definitely-wrong-here",
        });
        assert.equal(fail.status, 401);
      }
      const lockedOut = await jsonRequest(port, "POST", "/auth/login", {
        username: "locked",
        password: PASSWORD,
      });
      assert.equal(lockedOut.status, 401);
      assert.equal(locker.api_key.startsWith("dk_"), true);

      const taken = await jsonRequest(port, "POST", "/tenants", {
        tenant: "othername",
        username: "solo",
        password: PASSWORD,
      });
      assert.equal(taken.status, 409);
      assert.match(String(taken.data.detail), /用户名已被占用/);

      const nopassLogin = await jsonRequest(port, "POST", "/auth/login", {
        username: "nopass",
        password: PASSWORD,
      });
      assert.equal(nopassLogin.status, 401);
      assert.equal(nopassLogin.data.detail, wrong.data.detail);

      const noKeyPw = await jsonRequest(port, "POST", "/auth/change-password", {
        old_password: "another-good-password",
        new_password: "brand-new-password",
      });
      assert.equal(noKeyPw.status, 401);
      assert.equal(noKeyPw.data.detail, "X-Tenant-Key 无效");

      const nopassChange = await jsonRequest(port, "POST", "/auth/change-password", {
        old_password: PASSWORD,
        new_password: "brand-new-password",
      }, authHeader(nopass.data.api_key));
      assert.equal(nopassChange.status, 403);

      const stealToken = await jsonRequest(port, "POST", "/v1/settings/token", {
        tenant: "otherco",
        subsystem: "oa",
        headers: { Authorization: "Bearer stolen" },
      }, authHeader(first.api_key));
      assert.equal(stealToken.status, 403);

      const emptyToken = await jsonRequest(port, "POST", "/v1/settings/token", {
        subsystem: "oa",
      }, authHeader(first.api_key));
      assert.equal(emptyToken.status, 400);

      await writeTokenRecord("solo", "oa", {
        Authorization: "Bearer solo-secret-token",
        "Tenant-Id": "1",
      });
      const merged = await jsonRequest(port, "POST", "/v1/settings/token", {
        subsystem: "oa",
        token: "solo-next-token",
        header_name: "Authorization",
        token_prefix: "Bearer ",
      }, authHeader(first.api_key));
      assert.equal(merged.status, 200);
      const raw = await jsonRequest(
        port,
        "GET",
        "/v1/settings/token/raw?tenant=solo&subsystem=oa",
        undefined,
        authHeader(first.api_key),
      );
      assert.equal(raw.status, 200);
      assert.match(String(raw.data.headers.Authorization || raw.data.headers.authorization), /solo-next-token/);
      assert.equal(raw.data.headers["Tenant-Id"] || raw.data.headers["tenant-id"], "1");

      const options = await fetch(`http://127.0.0.1:${port}/v1/skills`, {
        method: "OPTIONS",
        headers: { origin: "http://127.0.0.1:19082" },
      });
      assert.equal(options.status, 204);
      assert.match(String(options.headers.get("access-control-allow-headers") || ""), /x-tenant-key/i);

      const health = await jsonRequest(port, "GET", "/api/health");
      assert.equal(health.status, 200);
      const recordings = await jsonRequest(port, "POST", "/api/recordings", {});
      assert.equal(recordings.status, 404);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});

test("lockout uses injected clock and username casefold shares the throttle key", async () => {
  await withData(async () => {
    const now = 1_700_000_000;
    await registerTenant({ tenant: "acme", username: "acme", password: PASSWORD });
    for (let i = 0; i < 3; i += 1) {
      await assert.rejects(() => login("ACME", "wrong-password-here", now), AuthError);
    }
    for (let i = 0; i < 2; i += 1) {
      await assert.rejects(() => login("acme", "wrong-password-here", now), AuthError);
    }
    await assert.rejects(() => login("acme", PASSWORD, now), /用户名或密码错误/);
    const opened = await login("acme", PASSWORD, now + 61);
    assert.equal(opened.tenant, "acme");
    assert.match(opened.api_key, /^dk_/);
  });
});

test("successful login clears failures before the lock threshold", async () => {
  await withData(async () => {
    const now = 1_700_000_000;
    await registerTenant({ tenant: "acme", username: "acme", password: PASSWORD });
    for (let i = 0; i < 3; i += 1) {
      await assert.rejects(() => login("acme", "wrong-password-here", now), AuthError);
    }
    await login("acme", PASSWORD, now);
    for (let i = 0; i < 4; i += 1) {
      await assert.rejects(() => login("acme", "wrong-password-here", now), AuthError);
    }
    const still = await login("acme", PASSWORD, now);
    assert.equal(still.tenant, "acme");
  });
});

test("changePassword rejects a wrong old password", async () => {
  await withData(async () => {
    const rec = await registerTenant({ tenant: "acme", username: "acme", password: PASSWORD });
    await assert.rejects(
      () => changePassword(rec.api_key, "not-the-old-password", "another-good-password"),
      (error) => error instanceof AuthError && error.status === 401,
    );
  });
});
