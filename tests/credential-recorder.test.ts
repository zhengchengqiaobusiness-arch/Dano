import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { BrowserRecorder } from "../src/browser/recorder.js";
import { materializeSkillCredentials } from "../src/credentials/credential-store.js";
import { readJsonl } from "../src/utils.js";
import type { EvidenceEvent } from "../src/domain.js";

test("an authenticated browser request feeds the external skill credential profile while evidence stays redacted", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "business-skill-auth-recording-"));
  const dataDir = path.join(temporary, "data");
  const outputRoot = path.join(temporary, ".agents", "skills");
  const server = http.createServer((request, response) => {
    if (request.url === "/api") {
      request.resume();
      response.setHeader("content-type", "application/json");
      response.end('{"success":true}');
      return;
    }
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.setHeader("set-cookie", "sid=logged-in; Path=/; HttpOnly");
    response.end(`<!doctype html><button id="call" onclick="fetch('/api', {
      headers: { authorization: 'Bearer captured-login', 'x-tenant-id': 'tenant-11' }
    })">调用</button>`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const origin = `http://127.0.0.1:${address.port}`;
  const recorder = new BrowserRecorder({
    rootDir: temporary,
    dataDir,
    recordingsDir: path.join(dataDir, "recordings"),
    catalogDir: path.join(dataDir, "catalog"),
    profileDir: path.join(temporary, "profile"),
    maxResponseBytes: 32_768,
    headless: true,
    openaiModel: "test"
  });
  try {
    const session = await recorder.start(origin, "authenticated-page");
    await recorder.control({ action: "click", selector: "#call" });
    await recorder.control({ action: "wait", ms: 150 });
    await recorder.stop();

    const events = await readJsonl<EvidenceEvent>(session.eventsFile);
    const request = events.find(event => event.kind === "network" && event.request.url === `${origin}/api`);
    assert.equal(request?.kind, "network");
    if (request?.kind === "network") {
      assert.equal(request.request.headers.authorization, "[REDACTED]");
    }

    const credentialFile = await materializeSkillCredentials(dataDir, outputRoot, "recorded-sk_test", [origin]);
    const profile = JSON.parse(await readFile(credentialFile!, "utf8"));
    assert.deepEqual(profile.origins[origin], {
      authorization: "Bearer captured-login",
      cookie: "sid=logged-in",
      "x-tenant-id": "tenant-11"
    });
  } finally {
    if (recorder.isActive()) await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(temporary, { recursive: true, force: true });
  }
});
