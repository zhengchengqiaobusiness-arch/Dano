import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { appendEvidence, getEvidence } from "../src/evidence/store.mjs";

test("evidence body is stored whole", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-ev-"));
  process.env.CABP_DATA = root;
  const id = "rec_evidence";
  const body = "x".repeat(50 * 1024);
  const saved = await appendEvidence(id, { kind: "network", body, body_missing: false });
  const full = await getEvidence(id, saved.id);
  assert.equal(full.body.length, body.length);
  const missing = await appendEvidence(id, { kind: "network", body: null, body_missing: true });
  const empty = await getEvidence(id, missing.id);
  assert.equal(empty.body_missing, true);
});

test("a missing evidence id is missing, not a thrown path string", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-ev-miss-"));
  process.env.CABP_DATA = root;
  const id = "rec_evidence_miss";
  try {
    await getEvidence(id, "ev_does_not_exist");
    assert.fail("expected ENOENT");
  } catch (error) {
    assert.equal(error.code, "ENOENT");
  }
});
