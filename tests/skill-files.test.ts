import test from "node:test";
import assert from "node:assert/strict";
import { isRetryableFsError, moveDirectory } from "../src/catalog/skill-files.js";

test("windows lock errors are retryable and fall back to copy", async () => {
  assert.equal(isRetryableFsError(Object.assign(new Error("EPERM"), { code: "EPERM" })), true);
  assert.equal(isRetryableFsError(Object.assign(new Error("missing"), { code: "ENOENT" })), false);

  let renamed = 0;
  let copied = 0;
  let removed = 0;
  await moveDirectory("from", "to", {
    ensureDir: async () => {},
    rename: async () => {
      renamed += 1;
      throw Object.assign(new Error("EPERM: operation not permitted, rename"), { code: "EPERM" });
    },
    cp: async () => {
      copied += 1;
    },
    rm: async () => {
      removed += 1;
    }
  });
  assert.ok(renamed >= 2);
  assert.equal(copied, 1);
  assert.equal(removed, 1);
});

test("cross-device moves immediately fall back to copy and remove", async () => {
  assert.equal(isRetryableFsError(Object.assign(new Error("EXDEV"), { code: "EXDEV" })), true);
  let renamed = 0;
  let copied = 0;
  let removed = 0;

  await moveDirectory("from", "to", {
    ensureDir: async () => {},
    rename: async () => {
      renamed += 1;
      throw Object.assign(new Error("EXDEV: cross-device link"), { code: "EXDEV" });
    },
    cp: async () => { copied += 1; },
    rm: async () => { removed += 1; }
  });

  assert.equal(renamed, 1);
  assert.equal(copied, 1);
  assert.equal(removed, 1);
});
