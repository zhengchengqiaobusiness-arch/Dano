import assert from "node:assert/strict";
import test from "node:test";
import { acceptFill } from "../src/browser/actions.mjs";

test("a fill sticks when the control keeps the typed text", () => {
  assert.deepEqual(acceptFill("", "甲", "甲"), { ok: true, value: "甲" });
});

test("a fill sticks when the control rewrites the text", () => {
  assert.deepEqual(acceptFill("", "AB", "ab"), { ok: true, value: "AB" });
});

test("a fill does not stick when the control keeps the previous text", () => {
  assert.deepEqual(acceptFill("locked", "locked", "changed"), { ok: false, value: "locked" });
});

test("clearing a control is the value it kept", () => {
  assert.deepEqual(acceptFill("甲", "", ""), { ok: true, value: "" });
});
