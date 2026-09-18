import { expect, it } from "vitest";
import { parseProtectedSupervisorProfile } from "../protected-supervisor-profile.js";
function profile() {
  return { runtimeRoot: "/runtime", sessionsRoot: "/sessions", hostStateRoot: "/host-state", maxWorkers: 4,
    identities: { directory: "/identities", firstUid: 10001, firstGid: 10001, count: 4, lockTimeoutMs: 5000 },
    broker: { installationDir: "/app", hostUid: 1000, hostGid: 1000, piPackageContext: "/app/package.json",
      privilegeGuard: "/usr/bin/setpriv", path: "/usr/bin:/bin", startupTimeoutMs: 30000,
      operationTimeoutMs: 10000, shutdownTimeoutMs: 5000, maxConcurrentOperations: 4, maxResultBytes: 1048576 },
    host: { hostUid: 1000, hostGid: 1000, startupTimeoutMs: 30000, operationTimeoutMs: 40000,
      maxConcurrentOperations: 8, maxMessageBytes: 1048576, trustedSkillPaths: [], providerPythonModuleDirectory: "/app/python" } };
}
it("projects a complete administrator profile without applying hidden defaults", () => {
  const input = profile(), parsed = parseProtectedSupervisorProfile(input);
  expect(parsed).toEqual(input);
  expect(parsed).not.toBe(input);
  expect(parsed.broker).not.toBe(input.broker);
});
it("rejects injected environment and credential fields at every profile level", () => {
  for (const level of ["root", "broker", "host", "identities"] as const) {
    const input = profile();
    Object.assign(level === "root" ? input : input[level], { apiKey: "PRIVATE_KEY", env: { NODE_OPTIONS: "unsafe" } });
    expect(() => parseProtectedSupervisorProfile(input)).toThrow("INVALID_PROTECTED_SUPERVISOR_PROFILE");
  }
});
it("rejects missing limits, inconsistent host identities and relative executable search paths", () => {
  for (const change of [
    (input: any) => { delete input.broker.shutdownTimeoutMs; },
    (input: any) => { input.host.hostUid = 0; },
    (input: any) => { input.broker.hostUid = 2000; },
    (input: any) => { input.broker.path = "/usr/bin:"; },
    (input: any) => { input.broker.path = ".:/usr/bin"; },
    (input: any) => { input.runtimeRoot = "/tmp/../runtime"; },
    (input: any) => { input.identities.count = 0; },
    (input: any) => { input.maxWorkers = 1.5; },
  ]) {
    const input = profile(); change(input);
    expect(() => parseProtectedSupervisorProfile(input)).toThrow();
  }
});
