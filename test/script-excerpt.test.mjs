import assert from "node:assert/strict";
import test from "node:test";
import { scriptExcerpts } from "../src/browser/network.mjs";

test("a script excerpt is the text around the requested key", () => {
  const source = `${"x".repeat(400)}function send(row){ row.owner = row.owner }${"y".repeat(400)}`;
  const excerpts = scriptExcerpts(source, "owner");
  assert.equal(excerpts.length, 2);
  assert.match(excerpts[0], /row\.owner/);
  assert.ok(excerpts[0].length < source.length);
});

test("a short find does not return the whole script", () => {
  assert.deepEqual(scriptExcerpts("owner", "o"), []);
});
