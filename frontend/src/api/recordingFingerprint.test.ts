import assert from "node:assert/strict";
import test from "node:test";
import { preferStableDraftFingerprint } from "./recordingFingerprint.ts";

test("定稿后 PI 再用 recordingId 当指纹时，保留已经对上的内容哈希", () => {
  assert.equal(
    preferStableDraftFingerprint("abc123hash", "rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    "abc123hash",
  );
});

test("打开历史结果时仍接受内容哈希或 recordingId", () => {
  assert.equal(preferStableDraftFingerprint("", "rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), "rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  assert.equal(preferStableDraftFingerprint("", "abc123hash"), "abc123hash");
  assert.equal(preferStableDraftFingerprint("abc123hash", "def456hash"), "def456hash");
});
