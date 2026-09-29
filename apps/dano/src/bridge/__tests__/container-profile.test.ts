import { expect, it } from "vitest";
import { containerProfile } from "../container-profile.js";
import { workspaceSessionDirectoryPath } from "../runtime-layout.js";

it("keeps the old runtime and encoded session path when memory is enabled", () => {
  const env = { DANO_RUNTIME_DIR: "/existing/runtime", DANO_SESSIONS_ROOT: "/existing/sessions" };
  const plain = containerProfile(env, "/app");
  const memory = containerProfile({ ...env, DANO_MEMORY_CONFIG_DIR: "/private/memory" }, "/app");
  expect(memory.runtimeRoot).toBe(plain.runtimeRoot);
  expect(memory.sessionsRoot).toBe(plain.sessionsRoot);
  expect(workspaceSessionDirectoryPath(memory.sessionsRoot, "/existing/runtime/users/alice/workspaces/default"))
    .toBe("/existing/sessions/--existing-runtime-users-alice-workspaces-default--");
  expect(memory.hostStateRoot).toBe("/var/lib/dano-host/state");
  expect(memory.memoryRecoveryDirectory).toBe("/var/lib/dano-memory-recovery");
});

it("retains the normal default, legacy environment alias and CLI precedence", () => {
  expect(containerProfile({ DANO_RUNTIME_DIR: "/data" }, "/app").sessionsRoot).toBe("/data/.dano/sessions");
  expect(containerProfile({ PI_WEB_SESSIONS_ROOT: "/legacy" }, "/app").sessionsRoot).toBe("/legacy");
  expect(containerProfile({ DANO_SESSIONS_ROOT: "/env" }, "/app", ["--sessions-root", "/cli"]).sessionsRoot).toBe("/cli");
  expect(() => containerProfile({}, "/app", ["--sessions-root"])).toThrow("Missing value");
  expect(() => containerProfile({ DANO_WORKER_FIRST_UID: "0" }, "/app")).toThrow();
});
