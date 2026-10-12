import assert from "node:assert/strict";
import test from "node:test";
import {
  beginFreshRecordingEntry,
  forgetLiveRecording,
  forgetRecordingResultId,
  rememberedLiveRecording,
  rememberedRecordingResultId,
  rememberLiveRecording,
  rememberRecordingResultId,
  selectRecordingResultToResume,
} from "./recordingResume.ts";

test("the completed result is selected again after a page reload", () => {
  const rows = [
    { id: "result-new", title: "新结果" },
    { id: "result-active", title: "刚产出的能力结果" },
  ];

  assert.equal(selectRecordingResultToResume("result-active", rows)?.id, "result-active");
});

test("a deleted result is not restored", () => {
  assert.equal(selectRecordingResultToResume("result-deleted", [{ id: "result-live" }]), undefined);
});

test("the active result id survives a same-tab reload and is cleared for a new recording", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };

  rememberRecordingResultId(storage, " result-active ");
  assert.equal(rememberedRecordingResultId(storage), "result-active");
  forgetRecordingResultId(storage);
  assert.equal(rememberedRecordingResultId(storage), "");
});

test("entering Recording V2 from the menu starts on recording setup", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
  rememberRecordingResultId(storage, "result-active");

  const navigationState = beginFreshRecordingEntry(storage);

  assert.equal(rememberedRecordingResultId(storage), "");
  assert.deepEqual(navigationState, { freshRecordingEntry: true });
});

test("a live recording id survives reload and is not treated as setup", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
  rememberLiveRecording(storage, { recordingId: "rec_abc", action: "act_1" });
  assert.deepEqual(rememberedLiveRecording(storage), { recordingId: "rec_abc", action: "act_1" });
  assert.equal(rememberedLiveRecording({
    getItem: () => JSON.stringify({ recordingId: "result-1" }),
  }), null);
  forgetLiveRecording(storage);
  assert.equal(rememberedLiveRecording(storage), null);
  rememberLiveRecording(storage, { action: "act_1" });
  assert.deepEqual(rememberedLiveRecording(storage), { action: "act_1" });
  rememberLiveRecording(storage, { recordingId: "rec_abc" });
  beginFreshRecordingEntry(storage);
  assert.equal(rememberedLiveRecording(storage), null);
});
