import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { refreshVault, usableAuthHeaders, writeAuthVault } from "../src/auth-vault.mjs";
import { writeRuntimeConfig } from "../src/skillpack/files.mjs";
import { writeAuthLocalFile } from "../src/token-store.mjs";

async function withDataRoot(run) {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-auth-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  try {
    return await run(root);
  } finally {
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
    await rm(root, { recursive: true, force: true });
  }
}

test("usable headers keep tenant id next to authorization", () => {
  const headers = usableAuthHeaders({
    authorization: "Bearer test-token",
    "tenant-id": "1",
  });
  assert.equal(headers.Authorization, "Bearer test-token");
  assert.equal(headers["Tenant-Id"], "1");
});

test("auth.local.json written from the vault keeps tenant id", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "cabp-auth-local-"));
  try {
    await writeAuthLocalFile(dir, {
      Authorization: "Bearer test-token",
      "Tenant-Id": "1",
    }, { method: "POST", url: "https://example.test/refresh" });
    const payload = JSON.parse(await readFile(path.join(dir, "config", "auth.local.json"), "utf8"));
    assert.equal(payload.headers.Authorization, "Bearer test-token");
    assert.equal(payload.headers["Tenant-Id"], "1");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("a later authorization-only update does not drop tenant id from the vault", async () => {
  await withDataRoot(async () => {
    await writeAuthVault("rec_auth1", {
      Authorization: "Bearer old-token",
      "Tenant-Id": "1",
    });
    const next = await writeAuthVault("rec_auth1", { Authorization: "Bearer new-token" });
    assert.equal(next.headers.Authorization, "Bearer new-token");
    assert.equal(next.headers["Tenant-Id"], "1");
  });
});

test("refresh replay sends the vault headers", async () => {
  await withDataRoot(async () => {
    await writeAuthVault("rec_auth2", {
      Authorization: "Bearer test-token",
      "Tenant-Id": "1",
    }, {
      credential: { method: "POST", url: "https://example.test/refresh?refreshToken=r1" },
    });
    const sent = [];
    const previous = globalThis.fetch;
    globalThis.fetch = async (url, init = {}) => {
      sent.push({ url: String(url), method: init.method, headers: { ...init.headers } });
      return new Response("{}", { status: 401 });
    };
    try {
      await refreshVault("rec_auth2");
    } finally {
      globalThis.fetch = previous;
    }
    assert.equal(sent.length, 1);
    assert.equal(sent[0].method, "POST");
    assert.equal(sent[0].headers.Authorization, "Bearer test-token");
    assert.equal(sent[0].headers["Tenant-Id"], "1");
  });
});

test("runtime config copies tenant id from the vault into auth.local.json", async () => {
  await withDataRoot(async (root) => {
    await writeAuthVault("rec_auth3", {
      Authorization: "Bearer test-token",
      "Tenant-Id": "1",
    });
    await writeRuntimeConfig({ id: "rec_auth3", tenant: "", subsystem: "app", startUrl: "https://example.test/" }, "app.rec_auth3");
    const payload = JSON.parse(await readFile(path.join(root, "skills", "app.rec_auth3", "config", "auth.local.json"), "utf8"));
    assert.equal(payload.headers.Authorization, "Bearer test-token");
    assert.equal(payload.headers["Tenant-Id"], "1");
  });
});
