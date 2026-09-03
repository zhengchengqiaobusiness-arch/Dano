import test from "node:test";
import assert from "node:assert/strict";
import { isPageSessionId } from "../src/web/workbench-page.ts";

test("page session ids are accepted only in the workbench format", () => {
  assert.equal(isPageSessionId("page_abcdef12"), true);
  assert.equal(isPageSessionId("page_4f2c9b1e8a704c0db2c1e6a7f3d5c819"), true);
  assert.equal(isPageSessionId(""), false);
  assert.equal(isPageSessionId("page_"), false);
  assert.equal(isPageSessionId("shared"), false);
  assert.equal(isPageSessionId("page_***"), false);
});
