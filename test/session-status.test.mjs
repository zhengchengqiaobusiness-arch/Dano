import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { recordingDir } from "../src/paths.mjs";
import { appendSessionThought, applyVerifyToRecording, loadRecording, persistThoughts, publicSessionStatus } from "../src/session.mjs";

test("the session only publishes recording, skill ready, or failed", () => {
  assert.equal(publicSessionStatus({ status: "recording" }), "recording");
  assert.equal(publicSessionStatus({ status: "waiting_operator" }), "recording");
  assert.equal(publicSessionStatus({ status: "skill_ready" }), "skill_ready");
  assert.equal(publicSessionStatus({ status: "skill_written_needs_auth" }), "skill_ready");
  assert.equal(publicSessionStatus({ status: "verify_failed" }), "failed");
  assert.equal(publicSessionStatus({ status: "failed" }), "failed");
  assert.equal(publicSessionStatus({ status: "stopped" }), "failed");
  const recording = { status: "recording", finished: false };
  applyVerifyToRecording(recording, { status: "verify_failed", errors: [{ code: "command_not_run" }] }, "/tmp/skill");
  assert.equal(recording.status, "recording");
  assert.equal(recording.finished, false);
  assert.equal(publicSessionStatus(recording), "recording");
  applyVerifyToRecording(recording, { status: "skill_written_needs_auth", errors: [] }, "/tmp/skill");
  assert.equal(recording.status, "skill_ready");
  assert.equal(recording.finished, true);
  assert.equal(publicSessionStatus(recording), "skill_ready");
});

test("a finished recording on disk can be attached without a live map entry", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-session-"));
  const id = "rec_attach_disk";
  await mkdir(recordingDir(id), { recursive: true });
  await writeFile(path.join(recordingDir(id), "state.json"), JSON.stringify({
    id,
    status: "skill_ready",
    skillId: "app.rec_attach_disk",
    startUrl: "http://127.0.0.1/",
  }));
  const loaded = await loadRecording(id);
  assert.equal(loaded.status, "skill_ready");
  assert.equal(loaded.finished, true);
  assert.equal(publicSessionStatus(loaded), "skill_ready");
  assert.ok(loaded.listeners instanceof Set);
  loaded.revision += 1;
  assert.equal(loaded.revision, 1);
});

test("thoughts on disk are restored when a recording is loaded", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-thoughts-"));
  const id = "rec_thoughts_disk";
  await mkdir(recordingDir(id), { recursive: true });
  await writeFile(path.join(recordingDir(id), "state.json"), JSON.stringify({
    id,
    status: "skill_ready",
    skillId: "app.rec_thoughts_disk",
    startUrl: "http://127.0.0.1/",
  }));
  await writeFile(path.join(recordingDir(id), "thoughts.json"), JSON.stringify([
    { kind: "tool", phase: "end", ok: true, text: "browser_act click f0:e1@1 完成" },
    { kind: "text", text: "结构已过 鉴权过期" },
  ]));
  const loaded = await loadRecording(id);
  assert.equal(loaded.thoughts.length, 2);
  assert.match(loaded.thoughts[0].text, /browser_act/);
  assert.match(loaded.thoughts[1].text, /结构已过/);
});

test("appendSessionThought coalesces streams and writes thoughts.json", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-thoughts-live-"));
  const recording = { id: "rec_thoughts_live", thoughts: [] };
  await mkdir(recordingDir(recording.id), { recursive: true });
  appendSessionThought(recording, { kind: "thinking", text: "先", stream: true });
  appendSessionThought(recording, { kind: "thinking", text: "点", stream: true });
  appendSessionThought(recording, { kind: "tool", phase: "start", text: "browser_act" });
  appendSessionThought(recording, { kind: "tool", phase: "end", ok: true, text: "browser_act 完成" });
  await persistThoughts(recording);
  assert.equal(recording.thoughts.length, 2);
  assert.equal(recording.thoughts[0].text, "先点");
  assert.equal(recording.thoughts[1].ok, true);
  const disk = JSON.parse(await readFile(path.join(recordingDir(recording.id), "thoughts.json"), "utf8"));
  assert.equal(disk.length, 2);
  assert.equal(disk[0].text, "先点");
});
