import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UserContext } from "../user-context.js";
import { UserRuntimeRegistry } from "../user-runtime-registry.js";
import type { ProtectedSessionTools } from "../protected-session-tools.js";

const runtimeRoots: string[] = [];

afterEach(() => {
  for (const root of runtimeRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

describe("UserRuntimeRegistry owner transfer", () => {
  it("keeps a newly transferred protected session root private for collection", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-private-session-transfer-"));
    runtimeRoots.push(root);
    const source = userContext(root, "anonymous-source");
    const target = userContext(root, "authenticated-target");
    const sessionsRootPath = path.join(root, "protected-sessions");
    const sourceRoot = path.join(sessionsRootPath, path.basename(source.folderPath));
    const targetRoot = path.join(sessionsRootPath, path.basename(target.folderPath));
    writeText(path.join(sourceRoot, "session.jsonl"), "synthetic session\n");
    const registry = new UserRuntimeRegistry(async () => {
      throw new Error("transfer must not create a backend");
    }, { sessionsRootPath });
    await registry.transferOwnership(source, target, {
      assertIdle() {}, async commitOwnership() {},
    });
    expect(fs.statSync(targetRoot).mode & 0o777).toBe(0o700);
    expect(fs.readFileSync(path.join(targetRoot, "session.jsonl"), "utf8")).toBe("synthetic session\n");
    await registry.dispose();
  });

  it("binds each server user context to its own protected backend profile", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-protected-users-"));
    runtimeRoots.push(root);
    const alice = userContext(root, "alice");
    const bob = userContext(root, "bob");
    const profiles = new Map<string, ProtectedSessionTools>();
    const protectedToolsForUser = vi.fn(async (context: UserContext) => {
      const profile: ProtectedSessionTools = {
        agentDir: path.join(root, "private", context.user.id), trustedSkillPaths: [],
        async resolveWorker() { throw new Error("not executed by the registry"); },
      };
      profiles.set(context.user.id, profile);
      return profile;
    });
    const dispose = vi.fn(async () => {});
    const backend = vi.fn(async () => ({ dispose }) as never);
    const registry = new UserRuntimeRegistry(backend, { protectedToolsForUser });
    try {
      const [a, b, repeated] = await Promise.all([registry.get(alice), registry.get(bob), registry.get(alice)]);
      expect(repeated).toBe(a);
      expect(b).not.toBe(a);
      expect(protectedToolsForUser).toHaveBeenCalledTimes(2);
      expect(protectedToolsForUser).toHaveBeenCalledWith(alice, { sessionsRootPath: path.join(alice.folderPath, "sessions") });
      expect(protectedToolsForUser).toHaveBeenCalledWith(bob, { sessionsRootPath: path.join(bob.folderPath, "sessions") });
      for (const context of [alice, bob]) {
        expect(backend).toHaveBeenCalledWith(expect.objectContaining({
          cwd: path.join(context.folderPath, "workspaces", "default"),
          credentialBrokerScope: context.user.id,
          protectedTools: profiles.get(context.user.id),
        }));
      }
    } finally {
      await registry.dispose();
    }
    expect(dispose).toHaveBeenCalledTimes(2);
  });

  it("merges preference objects while preserving unrelated file conflicts", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-owner-transfer-"),
    );
    runtimeRoots.push(runtimeRoot);
    const source = userContext(runtimeRoot, "anonymous-source");
    const target = userContext(runtimeRoot, "authenticated-target");
    writeJson(path.join(source.folderPath, "preferences", "layout.json"), {
      sourceOnly: "migrated",
      shared: "anonymous",
    });
    writeJson(path.join(target.folderPath, "preferences", "layout.json"), {
      targetOnly: "retained",
      shared: "authenticated",
    });
    writeText(
      path.join(source.folderPath, "workspaces", "default", "shared.txt"),
      "anonymous content",
    );
    writeText(
      path.join(target.folderPath, "workspaces", "default", "shared.txt"),
      "authenticated content",
    );
    const registry = new UserRuntimeRegistry(async () => {
      throw new Error("test backend should not be created");
    });

    await registry.transferOwnership(source, target, {
      assertIdle() {},
      async commitOwnership() {},
    });

    expect(
      JSON.parse(
        fs.readFileSync(
          path.join(target.folderPath, "preferences", "layout.json"),
          "utf8",
        ),
      ),
    ).toEqual({
      sourceOnly: "migrated",
      targetOnly: "retained",
      shared: "authenticated",
    });
    expect(
      fs.existsSync(
        path.join(target.folderPath, "preferences", "layout.anonymous-1.json"),
      ),
    ).toBe(false);
    expect(
      fs.readFileSync(
        path.join(target.folderPath, "workspaces", "default", "shared.txt"),
        "utf8",
      ),
    ).toBe("authenticated content");
    expect(
      fs.readFileSync(
        path.join(
          target.folderPath,
          "workspaces",
          "default",
          "shared.anonymous-1.txt",
        ),
        "utf8",
      ),
    ).toBe("anonymous content");
  });
});

function userContext(runtimeRoot: string, userId: string): UserContext {
  return {
    user: { id: userId },
    folderPath: path.join(runtimeRoot, "users", userId),
  };
}

function writeJson(filePath: string, value: Record<string, unknown>): void {
  writeText(filePath, `${JSON.stringify(value)}\n`);
}

function writeText(filePath: string, value: string): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, value, "utf8");
}

function protectedProfile(dispose: () => Promise<void>): ProtectedSessionTools {
  return { agentDir: "/private/agent", trustedSkillPaths: [], dispose,
    async resolveWorker() { throw new Error("not used in lifecycle test"); } };
}
function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function lifecycleUser(): UserContext {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-runtime-lifecycle-"));
  runtimeRoots.push(root);
  return userContext(root, "alice");
}

it("releases the protected profile when backend initialization fails", async () => {
  const release = vi.fn(async () => {});
  const registry = new UserRuntimeRegistry(async () => { throw new Error("START_FAILED"); }, {
    protectedToolsForUser: async () => protectedProfile(release),
  });
  await expect(registry.get(lifecycleUser())).rejects.toThrow("START_FAILED");
  expect(release).toHaveBeenCalledTimes(1);
  await registry.dispose();
});

it("stops sessions before workers and releases workers even if session shutdown fails", async () => {
  const calls: string[] = [];
  const registry = new UserRuntimeRegistry(async () => ({
    async dispose() { calls.push("sessions"); throw new Error("SESSION_CLOSE_FAILED"); },
  }) as never, { protectedToolsForUser: async () => protectedProfile(async () => { calls.push("workers"); }) });
  await registry.get(lifecycleUser());
  await expect(registry.dispose()).rejects.toThrow("DISPOSAL_FAILED");
  await expect(registry.dispose()).rejects.toThrow("DISPOSAL_FAILED");
  expect(calls).toEqual(["sessions", "workers"]);
});

it("waits for initialization during shutdown and forbids new runtime acquisition", async () => {
  const entered = pending<void>();
  const start = pending<void>();
  const release = vi.fn(async () => {});
  const backendDispose = vi.fn(async () => {});
  const registry = new UserRuntimeRegistry(async () => {
    entered.resolve(); await start.promise; return { dispose: backendDispose } as never;
  }, { protectedToolsForUser: async () => protectedProfile(release) });
  const user = lifecycleUser();
  const creating = registry.get(user);
  await entered.promise;
  let done = false;
  const closing = registry.dispose().then(() => { done = true; });
  await expect(registry.get(user)).rejects.toThrow("CLOSED");
  expect(done).toBe(false);
  start.resolve();
  await Promise.all([creating, closing]);
  expect(backendDispose).toHaveBeenCalledTimes(1);
  expect(release).toHaveBeenCalledTimes(1);
});

it("waits for retiring workers before deleting user files or completing shutdown", async () => {
  const entered = pending<void>();
  const stop = pending<void>();
  const release = vi.fn(async () => { entered.resolve(); await stop.promise; });
  const registry = new UserRuntimeRegistry(async () => ({ async dispose() {} }) as never, {
    protectedToolsForUser: async () => protectedProfile(release),
  });
  const user = lifecycleUser();
  await registry.get(user);
  const retiring = registry.retireUser(user);
  await entered.promise;
  expect(fs.existsSync(user.folderPath)).toBe(true);
  await expect(registry.get(user)).rejects.toThrow("CLOSED");
  let done = false;
  const closing = registry.dispose().then(() => { done = true; });
  await Promise.resolve();
  expect(done).toBe(false);
  stop.resolve();
  await Promise.all([retiring, closing, registry.retireUser(user)]);
  expect(release).toHaveBeenCalledTimes(1);
  expect(fs.existsSync(user.folderPath)).toBe(false);
});

it("keeps authenticated files and cleanup capability when remote memory retirement needs retry", async () => {
  const base = lifecycleUser();
  const user: UserContext = { ...base, user: { id: base.user.id, username: "Alice" } };
  let available = false;
  const retire = vi.fn(async () => { if (!available) throw new Error("MEMORY_RETIREMENT_PENDING"); });
  const finalizeRetirement = vi.fn(async () => {});
  const release = vi.fn(async () => {});
  const registry = new UserRuntimeRegistry(async () => ({ async dispose() {} }) as never, {
    protectedToolsForUser: async () => ({ ...protectedProfile(release),
      memory: { retire, finalizeRetirement } as unknown as ProtectedSessionTools["memory"] }),
  });
  await registry.get(user);
  await expect(registry.retireUser(user)).rejects.toThrow("MEMORY_RETIREMENT_PENDING");
  expect(fs.existsSync(user.folderPath)).toBe(true);
  expect(release).not.toHaveBeenCalled();
  expect(finalizeRetirement).not.toHaveBeenCalled();
  await expect(registry.get(user)).rejects.toThrow("CLOSED");
  available = true;
  await registry.retireUser(user);
  expect(retire).toHaveBeenCalledTimes(2);
  expect(release).toHaveBeenCalledTimes(1);
  expect(finalizeRetirement).toHaveBeenCalledTimes(1);
  expect(fs.existsSync(user.folderPath)).toBe(false);
  await registry.dispose();
});

it("keeps authenticated files when memory failed to initialize and retries cleanup after recovery", async () => {
  const base = lifecycleUser();
  const user: UserContext = { ...base, user: { id: base.user.id, username: "Alice" } };
  let ready = false;
  const release = vi.fn(async () => {});
  const retire = vi.fn(async () => {});
  const finalizeRetirement = vi.fn(async () => {});
  const registry = new UserRuntimeRegistry(async () => ({ async dispose() {} }) as never, {
    protectedToolsForUser: async () => ({ ...protectedProfile(release), ...(ready
      ? { memory: { retire, finalizeRetirement } as unknown as ProtectedSessionTools["memory"] }
      : { memoryRetirementBlocked: true as const }) }),
  });
  await registry.get(user);
  await expect(registry.retireUser(user)).rejects.toThrow("MEMORY_RETIREMENT_PENDING");
  expect(fs.existsSync(user.folderPath)).toBe(true);
  expect(retire).not.toHaveBeenCalled();
  expect(release).toHaveBeenCalledOnce();
  ready = true;
  await registry.retireUser(user);
  expect(retire).toHaveBeenCalledOnce();
  expect(finalizeRetirement).toHaveBeenCalledOnce();
  expect(release).toHaveBeenCalledTimes(2);
  expect(fs.existsSync(user.folderPath)).toBe(false);
  await registry.dispose();
});

it("recreates a retirement runtime after initialization or blocked-profile disposal fails", async () => {
  const base = lifecycleUser();
  const user: UserContext = { ...base, user: { id: base.user.id, username: "Alice" } };
  let failInitialization = true, failDisposal = true;
  const retire = vi.fn(async () => {});
  const finalizeRetirement = vi.fn(async () => {});
  const registry = new UserRuntimeRegistry(async () => ({ async dispose() {} }) as never, {
    protectedToolsForUser: async () => {
      if (failInitialization) throw new Error("MEMORY_PROFILE_UNAVAILABLE");
      return { ...protectedProfile(async () => { if (failDisposal) throw new Error("PROFILE_RELEASE_FAILED"); }),
        ...(failDisposal ? { memoryRetirementBlocked: true as const }
          : { memory: { retire, finalizeRetirement } as unknown as ProtectedSessionTools["memory"] }) };
    },
  });
  await expect(registry.retireUser(user)).rejects.toThrow("MEMORY_PROFILE_UNAVAILABLE");
  expect(fs.existsSync(user.folderPath)).toBe(true);
  failInitialization = false;
  await expect(registry.retireUser(user)).rejects.toThrow("USER_RUNTIME_DISPOSAL_FAILED");
  expect(fs.existsSync(user.folderPath)).toBe(true);
  failDisposal = false;
  await registry.retireUser(user);
  expect(retire).toHaveBeenCalledOnce();
  expect(finalizeRetirement).toHaveBeenCalledOnce();
  expect(fs.existsSync(user.folderPath)).toBe(false);
  await registry.dispose();
});

it("retries a failed disposer after remote retirement without repeating completed cleanup", async () => {
  const base = lifecycleUser();
  const user: UserContext = { ...base, user: { id: base.user.id, username: "Alice" } };
  let releaseAvailable = false;
  const retire = vi.fn(async () => {});
  const finalizeRetirement = vi.fn(async () => {});
  const backendDispose = vi.fn(async () => {});
  const release = vi.fn(async () => { if (!releaseAvailable) throw new Error("PROFILE_RELEASE_FAILED"); });
  const registry = new UserRuntimeRegistry(async () => ({ dispose: backendDispose }) as never, {
    protectedToolsForUser: async () => ({ ...protectedProfile(release),
      memory: { retire, finalizeRetirement } as unknown as ProtectedSessionTools["memory"] }),
  });
  await registry.get(user);
  await expect(registry.retireUser(user)).rejects.toThrow("USER_RUNTIME_DISPOSAL_FAILED");
  expect(fs.existsSync(user.folderPath)).toBe(true);
  expect(finalizeRetirement).not.toHaveBeenCalled();
  releaseAvailable = true;
  await registry.retireUser(user);
  expect(backendDispose).toHaveBeenCalledOnce();
  expect(release).toHaveBeenCalledTimes(2);
  expect(finalizeRetirement).toHaveBeenCalledOnce();
  expect(fs.existsSync(user.folderPath)).toBe(false);
  await registry.dispose();
});

it("reports failed initialization cleanup again at shutdown", async () => {
  const registry = new UserRuntimeRegistry(async () => { throw new Error("START_FAILED"); }, {
    protectedToolsForUser: async () => protectedProfile(async () => { throw new Error("CLOSE_FAILED"); }),
  });
  await expect(registry.get(lifecycleUser())).rejects.toThrow("INITIALIZATION_FAILED");
  await expect(registry.dispose()).rejects.toThrow("DISPOSAL_FAILED");
});
