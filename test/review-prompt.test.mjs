import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { reviewPrompt } from "../src/agent/pi-session.mjs";
import { ASK_PROTOCOL, verifySkill } from "../src/skillpack/verify.mjs";
import { GUIDE_NAMES } from "../src/agent/guides.mjs";
import { appendEvidence } from "../src/evidence/store.mjs";
import { skillDir } from "../src/paths.mjs";

function fakeReviewTurn(recording, facts, sent) {
  const ready = recording?.skillReady || recording?.status === "skill_ready" || recording?.status === "skill_written_needs_auth";
  if (ready && recording && !recording.reviewed) {
    recording.reviewed = true;
    const review = reviewPrompt({ observations: recording.verify?.observations, goalUnclicked: facts.goal_named_unclicked });
    if (review) sent.push(review);
  }
}

test("reviewPrompt is null when both lists are empty", () => {
  assert.equal(reviewPrompt({}), null);
  assert.equal(reviewPrompt({ observations: [], goalUnclicked: [] }), null);
  const text = reviewPrompt({ observations: [{ code: "ask_coverage" }], goalUnclicked: ["upload"] });
  assert.match(text, /goal_named_unclicked/);
  assert.match(text, /ask_coverage/);
  assert.match(text, /upload/);
});

test("fake pi reviews once after skill_ready and again after the user speaks", () => {
  const recording = {
    skillReady: true,
    status: "skill_ready",
    reviewed: false,
    verify: { observations: [{ code: "required_empty_at_write", field: "kind" }] },
  };
  const facts = { goal_named_unclicked: ["upload"] };
  const sent = [];
  fakeReviewTurn(recording, facts, sent);
  assert.equal(sent.length, 1);
  fakeReviewTurn(recording, facts, sent);
  assert.equal(sent.length, 1);
  recording.reviewed = false;
  fakeReviewTurn(recording, facts, sent);
  assert.equal(sent.length, 2);
});

test("required_empty_at_write and question_not_on_page stay in observations", async () => {
  process.env.CABP_DATA = await mkdtemp(path.join(os.tmpdir(), "cabp-review-"));
  const rec = { id: "rec_review_obs", tenant: "", subsystem: "app", skillId: "app.rec_review_obs", startUrl: "http://127.0.0.1/" };
  const dir = skillDir(rec.skillId);
  await mkdir(path.join(dir, "scripts"), { recursive: true });
  await mkdir(path.join(dir, "references"), { recursive: true });
  await mkdir(path.join(dir, "config"), { recursive: true });
  await writeFile(path.join(dir, "SKILL.md"), [
    "---",
    "name: save-item",
    "description: save one item",
    "---",
    JSON.stringify({
      title: "save",
      questions: [
        { id: "title", question: "title", inputType: "text", required: true, default: " " },
        { id: "owner", question: "owner id", inputType: "text", required: true, default: " " },
      ],
    }),
    "python scripts/client.py save --confirm",
    ASK_PROTOCOL,
    "",
  ].join("\n"));
  await writeFile(path.join(dir, "scripts/client.py"), "def save():\n    method = \"POST\"\n    path = \"/api/items\"\n    body[\"title\"] = args.title\n");
  await writeFile(path.join(dir, "references/api.md"), "## 字段\n- page_name: title\n- source: caller\n- evidence_ids: req_save\n\n## 绑定\n\n## 未解决\n\n## 已验证读命令\n");
  await writeFile(path.join(dir, "config/runtime.json"), "{}\n");
  await writeFile(path.join(dir, "config/auth.local.json"), "{\"headers\":{\"Authorization\":\"Bearer tok\"}}\n");
  for (const name of GUIDE_NAMES) {
    await appendEvidence(rec.id, { kind: "guide", summary: name, body: name });
  }
  await appendEvidence(rec.id, {
    id: "req_save",
    kind: "network",
    summary: "POST /api/items",
    body: { method: "POST", path: "/api/items", post_data: JSON.stringify({ title: "a" }), response_body: "{}" },
  });
  await appendEvidence(rec.id, {
    kind: "action",
    summary: "click:save",
    body: {
      overlay_editable: [{ name: "textbox \"title\"", role: "textbox" }],
      required_empty_overlay: [{ name: "combobox \"kind\"", role: "combobox" }],
      requests: [{ method: "POST", path: "/api/items" }],
    },
  });
  const before = await verifySkill(dir, rec.id, rec);
  const errorCodes = before.errors.map((item) => item.code).sort();
  assert.ok(before.observations.some((item) => item.code === "required_empty_at_write" && item.field === "kind"));
  assert.ok(before.observations.some((item) => item.code === "question_not_on_page" && item.field === "owner"));
  assert.equal(before.errors.some((item) => item.code === "required_empty_at_write"), false);
  assert.equal(before.errors.some((item) => item.code === "question_not_on_page"), false);
  const after = await verifySkill(dir, rec.id, rec);
  assert.deepEqual(after.errors.map((item) => item.code).sort(), errorCodes);
});
