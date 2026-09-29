import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { appendEvidence, appliedValues, getEvidence, listEvidence, sideChanges } from "../src/evidence/store.mjs";

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
  const kept = appliedValues({
    filled_value: [{ label: "textbox \"编码\"", value: "AB" }],
    focused_value: [{ label: "combobox \"类别\"", value: "文具" }],
    snapshot: { text: "x".repeat(1000) },
  });
  assert.deepEqual(kept, [
    { label: "textbox \"编码\"", value: "AB" },
    { label: "combobox \"类别\"", value: "文具" },
  ]);
  assert.deepEqual(appliedValues({ filled_value: [{ label: "textbox \"名称\"", value: "甲", popup: "提示", row: "甲" }] }), [
    { label: "textbox \"名称\"", value: "甲", popup: "提示", row: "甲" },
  ]);
  const side = sideChanges({
    filled: ["spinbutton \"库存数量\" popup=\"dialog\""],
    changed: [
      { label: "spinbutton \"参考单价\" popup=\"dialog\"", before: "1", after: "2" },
      { label: "spinbutton \"库存数量\" popup=\"dialog\"", before: "", after: "3" },
      { label: "button \"确定\"", after: "" },
    ],
  });
  assert.deepEqual(side, ["spinbutton \"参考单价\" popup=\"dialog\""]);
  assert.deepEqual(sideChanges({
    clicked: "option \"文具\"",
    changed: [
      { label: "combobox \"类别\" popup=\"dialog\"", before: "", after: "文具" },
      { label: "cell \"18\"", before: "1", after: "2" },
    ],
  }), ["combobox \"类别\" popup=\"dialog\""]);
  const noted = await appendEvidence(id, {
    kind: "action",
    summary: "fill:spinbutton \"库存数量\"",
    body: {
      filled: ["spinbutton \"库存数量\" popup=\"dialog\""],
      changed: [{ label: "spinbutton \"参考单价\" popup=\"dialog\"", before: "1", after: "2" }],
    },
  });
  assert.deepEqual(noted.also_changed, ["spinbutton \"参考单价\" popup=\"dialog\""]);
  const action = await appendEvidence(id, {
    kind: "action",
    summary: "fill:textbox \"编码\"",
    body: { filled_value: [{ label: "textbox \"编码\"", value: "AB" }], snapshot: { text: "snap" } },
  });
  assert.deepEqual(action.filled_value, [{ label: "textbox \"编码\"", value: "AB" }]);
  const listed = await listEvidence(id, { kinds: ["action"], limit: 0 });
  assert.deepEqual(listed.at(-1).filled_value, [{ label: "textbox \"编码\"", value: "AB" }]);
  assert.equal(listed.at(-1).snapshot, undefined);
});
