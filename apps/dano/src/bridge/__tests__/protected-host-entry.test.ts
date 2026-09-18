import { expect, it } from "vitest";
import { parseProtectedHostProfile } from "../protected-host-entry.js";

const profile = { hostUid: 1000, hostGid: 1000, startupTimeoutMs: 1000, operationTimeoutMs: 1000,
  maxConcurrentOperations: 4, maxMessageBytes: 4096, trustedSkillPaths: ["/app/skills"],
  providerPythonModuleDirectory: "/app/python" };

it("projects only the launcher's fixed profile fields", () => {
  const parsed = parseProtectedHostProfile({ ...profile, env: { TOKEN: "not forwarded" }, module: "/untrusted" });
  expect(parsed).toEqual(profile);
  expect(parsed.trustedSkillPaths).not.toBe(profile.trustedSkillPaths);
});

it("rejects privileged IDs, invalid limits and relative resource paths", () => {
  for (const invalid of [null, [], { ...profile, hostUid: 0 }, { ...profile, hostGid: 2 ** 32 - 1 },
    { ...profile, maxConcurrentOperations: -1 }, { ...profile, startupTimeoutMs: "1000" },
    { ...profile, trustedSkillPaths: ["workspace/skill"] }, { ...profile, providerPythonModuleDirectory: "./python" }]) {
    expect(() => parseProtectedHostProfile(invalid)).toThrow("INVALID_PROTECTED_HOST_PROFILE");
  }
});
