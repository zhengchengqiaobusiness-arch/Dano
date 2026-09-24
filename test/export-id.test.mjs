import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createApp, listen } from "../src/server.mjs";

async function post(address, target) {
  const response = await fetch(`http://127.0.0.1:${address.port}${target}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  });
  return { status: response.status, body: await response.json() };
}

test("export rejects ids that are not ready recordings", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-export-"));
  const server = createApp();
  const address = await listen(server, 0);
  const health = await fetch(`http://127.0.0.1:${address.port}/api/health`);
  assert.deepEqual(await health.json(), { ok: true, service: "playwright-cabp" });
  const bad = await post(address, "/v1/pi-recordings/not-rec/export-skill");
  assert.equal(bad.status, 400);
  const early = await post(address, "/v1/pi-recordings/rec_abc/export-skill");
  assert.equal(early.body.status, "not_ready");
  await new Promise((resolve) => server.close(resolve));
});
