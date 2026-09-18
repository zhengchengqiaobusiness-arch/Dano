import { expect, it } from "vitest";
import { assertUnusedWorkerRange } from "../protected-supervisor.js";
const range = { directory: "/private/identities", firstUid: 10001, firstGid: 20001, count: 3, lockTimeoutMs: 1000 };

it("rejects collisions in NSS users, groups and users with unlisted primary groups", () => {
  expect(() => assertUnusedWorkerRange("root:x:0:0:root:/root:/bin/sh\nnode:x:1000:1000::/:/bin/sh\n", "root:x:0:\nnode:x:1000:\n", range)).not.toThrow();
  for (const [passwd, group] of [
    ["tool:x:10001:1000::/:/bin/sh", ""],
    ["", "tool:x:20003:"],
    ["tool:x:5000:20002::/:/bin/sh", ""],
  ]) expect(() => assertUnusedWorkerRange(passwd!, group!, range)).toThrow("SYSTEM_COLLISION");
});

it("rejects malformed NSS identity records", () => {
  expect(() => assertUnusedWorkerRange("invalid", "", range)).toThrow("UNSAFE");
  expect(() => assertUnusedWorkerRange("tool:x:1000:unknown::/:/bin/sh", "", range)).toThrow("UNSAFE");
});
