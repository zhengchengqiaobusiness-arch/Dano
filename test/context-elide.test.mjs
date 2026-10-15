import assert from "node:assert/strict";
import test from "node:test";
import { elideOldPageResults } from "../src/agent/pi-session.mjs";

const KEEP_KEYS = ["ok", "error", "clicked", "filled", "uploaded", "in_overlay", "layer_idle", "page_unchanged", "evidence_id", "hint", "continued", "note"];

function pageResult(toolName, body, extraContent = []) {
  return {
    role: "toolResult",
    toolName,
    content: [{ type: "text", text: JSON.stringify(body) }, ...extraContent],
  };
}

test("only the latest page result keeps the tree; earlier page results are elided in a copy", () => {
  const earlier = pageResult("browser_act", {
    ok: true,
    clicked: "button save",
    filled: "title",
    snapshot: { text: "old-tree", refs: ["f0:e1@1"] },
    overlay_fields: [{ name: "title", empty: false }],
    overlay_editable: [{ name: "title", ref: "f0:e1@1" }],
    requests: [{ id: "req_1", method: "POST", path: "/save", keys: ["title"] }],
    hint: "ok",
  }, [{ type: "image", data: "abc" }]);
  const later = pageResult("browser_snapshot", {
    ok: true,
    snapshot: { text: "new-tree", refs: ["f0:e2@2"] },
    overlay_editable: [{ name: "note", ref: "f0:e2@2" }],
  });
  const other = {
    role: "toolResult",
    toolName: "network_get",
    content: [{ type: "text", text: JSON.stringify({ id: "req_1", post_data: "{}" }) }],
  };
  const messages = [
    { role: "user", content: [{ type: "text", text: "go" }] },
    earlier,
    other,
    later,
  ];
  const snapshot = structuredClone(messages);
  const out = elideOldPageResults(messages);
  assert.deepEqual(messages, snapshot);
  assert.equal(out[1] === earlier, false);
  assert.equal(out[2], other);
  assert.equal(out[3], later);
  const elided = JSON.parse(out[1].content[0].text);
  assert.equal(elided.clicked, "button save");
  assert.equal(elided.filled, "title");
  assert.equal(elided.ok, true);
  assert.equal(elided.hint, "ok");
  assert.equal(elided.snapshot, undefined);
  assert.equal(elided.overlay_fields, undefined);
  assert.deepEqual(elided.requests, [{ id: "req_1", method: "POST", path: "/save" }]);
  assert.match(elided.elided, /较早的页面结果/);
  for (const key of Object.keys(elided)) {
    assert.ok(KEEP_KEYS.includes(key) || key === "requests" || key === "elided", key);
  }
  assert.equal(out[1].content[1].type, "text");
  assert.match(out[1].content[1].text, /较早的截图/);
  assert.match(out[3].content[0].text, /new-tree/);
  assert.equal(other.content[0].text.includes("post_data"), true);
});
