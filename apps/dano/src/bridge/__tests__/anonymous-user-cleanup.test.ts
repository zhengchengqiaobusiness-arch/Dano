import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAnonymousUserContextResolver } from "../anonymous-user-context.js";
import { AnonymousUserCleanup } from "../anonymous-user-cleanup.js";
import { BridgeEventBus } from "../bridge-event-bus.js";
import {
  BridgeServer,
  type AuthHttpHandler,
} from "../server.js";
import { DEFAULT_BRIDGE_CONFIG } from "../types.js";
import { UserRuntimeRegistry } from "../user-runtime-registry.js";

const runtimeRoots: string[] = [];

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  for (const root of runtimeRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

describe("Anonymous User cleanup store", () => {
  it("renews valid activity and removes an expired orphan through its owner", async () => {
    let now = 1_000;
    const runtimeRootPath = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-anonymous-cleanup-"),
    );
    runtimeRoots.push(runtimeRootPath);
    const anonymousUsers = createAnonymousUserContextResolver({
      runtimeRootPath,
      secureCookie: false,
      now: () => now,
    });
    const created = await anonymousUsers.resolveForClient!({});
    const userId = created!.userContext.user.id;

    now = 1_900;
    await expect(anonymousUsers.touchAnonymousUser(userId)).resolves.toBe(true);
    now = 2_500;
    const beforeExpiry = await anonymousUsers.sweepExpired({
      idleTtlMs: 1_000,
      beginCleanup: () => () => {},
      cleanupUser: async () => {},
    });
    expect(beforeExpiry).toEqual({ removed: 0, skipped: 0, failed: 0 });

    now = 2_901;
    const cleanedOwners: string[] = [];
    const afterExpiry = await anonymousUsers.sweepExpired({
      idleTtlMs: 1_000,
      beginCleanup: () => () => {},
      cleanupUser: async userContext => {
        cleanedOwners.push(userContext.user.id);
      },
    });

    expect(afterExpiry).toEqual({ removed: 1, skipped: 0, failed: 0 });
    expect(cleanedOwners).toEqual([userId]);
    expect(
      await anonymousUsers.resolveAnonymous!({
        cookie: created!.setCookie!.split(";", 1)[0],
      }),
    ).toBeNull();
  });

  it("uses one sweep for startup and periodic cleanup and stops its timer", async () => {
    vi.useFakeTimers();
    let now = 10_000;
    const runtimeRootPath = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-anonymous-scheduler-"),
    );
    runtimeRoots.push(runtimeRootPath);
    const anonymousUsers = createAnonymousUserContextResolver({
      runtimeRootPath,
      secureCookie: false,
      now: () => now,
    });
    const startupGuest = await anonymousUsers.resolveForClient!({});
    now = 11_001;
    const removed: string[] = [];
    const cleanup = new AnonymousUserCleanup(anonymousUsers, {
      idleTtlMs: 1_000,
      intervalMs: 100,
      beginCleanup: () => () => {},
      cleanupUser: async user => {
        removed.push(user.user.id);
      },
    });

    await cleanup.start();
    expect(removed).toEqual([startupGuest!.userContext.user.id]);

    const periodicGuest = await anonymousUsers.resolveForClient!({});
    now = 12_002;
    await vi.advanceTimersByTimeAsync(100);
    await cleanup.sweep();
    expect(removed).toContain(periodicGuest!.userContext.user.id);

    cleanup.dispose();
    const retainedGuest = await anonymousUsers.resolveForClient!({});
    now = 13_003;
    await vi.advanceTimersByTimeAsync(500);
    expect(removed).not.toContain(retainedGuest!.userContext.user.id);
  });

  it("keeps the owner mapping after a failed deletion and retries safely", async () => {
    let now = 20_000;
    const runtimeRootPath = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-anonymous-retry-"),
    );
    runtimeRoots.push(runtimeRootPath);
    const anonymousUsers = createAnonymousUserContextResolver({
      runtimeRootPath,
      secureCookie: false,
      now: () => now,
    });
    const created = await anonymousUsers.resolveForClient!({});
    const cookie = created!.setCookie!.split(";", 1)[0];
    now = 21_001;
    let attempts = 0;
    const sweepOptions = {
      idleTtlMs: 1_000,
      beginCleanup: () => () => {},
      cleanupUser: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("injected cleanup failure");
      },
    };

    await expect(anonymousUsers.sweepExpired(sweepOptions)).resolves.toEqual({
      removed: 0,
      skipped: 0,
      failed: 1,
    });
    expect(await anonymousUsers.resolveAnonymous!({ cookie })).not.toBeNull();
    await expect(anonymousUsers.sweepExpired(sweepOptions)).resolves.toEqual({
      removed: 1,
      skipped: 0,
      failed: 0,
    });
    expect(await anonymousUsers.resolveAnonymous!({ cookie })).toBeNull();
  });

  it("does not let a concurrent activity write restore a revoked guest mapping", async () => {
    let now = 25_000;
    const runtimeRootPath = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-anonymous-revoke-race-"),
    );
    runtimeRoots.push(runtimeRootPath);
    const anonymousUsers = createAnonymousUserContextResolver({
      runtimeRootPath,
      secureCookie: false,
      now: () => now,
      activityWriteIntervalMs: 0,
    });
    const created = await anonymousUsers.resolveForClient!({});
    if (!created?.setCookie) throw new Error("Expected Anonymous User Cookie");
    const cookie = created.setCookie.split(";", 1)[0]!;
    const userId = created.userContext.user.id;
    const recordPath = path.join(
      runtimeRootPath,
      "anonymous-sessions",
      fs.readdirSync(path.join(runtimeRootPath, "anonymous-sessions"))[0]!,
    );
    const readFile = fs.promises.readFile.bind(fs.promises);
    let recordReads = 0;
    let releaseStaleRead!: () => void;
    const staleReadGate = new Promise<void>(resolve => {
      releaseStaleRead = resolve;
    });
    let staleReadStarted!: () => void;
    const staleReadStart = new Promise<void>(resolve => {
      staleReadStarted = resolve;
    });
    vi.spyOn(fs.promises, "readFile").mockImplementation(
      (async (...args: Parameters<typeof fs.promises.readFile>) => {
        const value = await readFile(...args);
        if (path.resolve(String(args[0])) === recordPath) {
          recordReads += 1;
          if (recordReads === 2) {
            staleReadStarted();
            await staleReadGate;
          }
        }
        return value;
      }) as typeof fs.promises.readFile,
    );

    now = 25_001;
    const touching = anonymousUsers.touchAnonymousUser(userId);
    await staleReadStart;
    const revoking = anonymousUsers.revokeAnonymous!({ cookie }, userId);
    releaseStaleRead();
    await touching;
    await expect(revoking).resolves.toBe(true);

    await expect(
      anonymousUsers.resolveAnonymous!({ cookie }),
    ).resolves.toBeNull();
  });

  it("renews activity in memory without writing the guest record per request", async () => {
    let now = 30_000;
    const runtimeRootPath = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-anonymous-touch-"),
    );
    runtimeRoots.push(runtimeRootPath);
    const anonymousUsers = createAnonymousUserContextResolver({
      runtimeRootPath,
      secureCookie: false,
      now: () => now,
    });
    const created = await anonymousUsers.resolveForClient!({});
    const userId = created!.userContext.user.id;
    const recordPath = path.join(
      runtimeRootPath,
      "anonymous-sessions",
      fs.readdirSync(path.join(runtimeRootPath, "anonymous-sessions"))[0]!,
    );

    now = 31_000;
    await anonymousUsers.touchAnonymousUser(userId);
    now = 32_000;
    await anonymousUsers.touchAnonymousUser(userId);
    expect(JSON.parse(fs.readFileSync(recordPath, "utf8"))).toMatchObject({
      userId,
      lastActiveAt: 30_000,
    });

    now = 90_001;
    await anonymousUsers.touchAnonymousUser(userId);
    expect(JSON.parse(fs.readFileSync(recordPath, "utf8"))).toMatchObject({
      userId,
      lastActiveAt: 90_001,
    });
  });

  it("waits for an accepted command after its SSE stream is lost", async () => {
    let now = 100_000;
    const runtimeRootPath = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-anonymous-active-command-"),
    );
    runtimeRoots.push(runtimeRootPath);
    const anonymousUsers = createAnonymousUserContextResolver({
      runtimeRootPath,
      secureCookie: false,
      now: () => now,
      activityWriteIntervalMs: 100,
    });
    const guest = await anonymousUsers.resolveForClient!({});
    if (!guest) throw new Error("Expected Anonymous User");
    const guestContext = guest.userContext;
    const cookie = guest.setCookie!.split(";", 1)[0];
    const workspacePath = path.join(
      guestContext.folderPath,
      "workspaces",
      "default",
    );
    fs.mkdirSync(workspacePath, { recursive: true });
    const retainedPath = path.join(workspacePath, "active-command.txt");
    fs.writeFileSync(retainedPath, "still active", "utf8");
    let finishCommand!: () => void;
    const commandGate = new Promise<void>(resolve => {
      finishCommand = resolve;
    });
    const registry = new UserRuntimeRegistry(async () => {
      throw new Error("cleanup test does not create a backend");
    });
    const server = new BridgeServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        upload: {
          ...DEFAULT_BRIDGE_CONFIG.upload,
          uploadDir: path.join(runtimeRootPath, "uploads"),
        },
      },
      ctx => ({
        defaultWorkspacePath: workspacePath,
        currentGitCwd: () => workspacePath,
        handleClientMessage: () => {
          const finishOperation = ctx.beginUserOperation();
          void commandGate.finally(finishOperation);
        },
        dispose() {},
      }),
      new BridgeEventBus(DEFAULT_BRIDGE_CONFIG),
      () => {},
      anonymousUsers,
      undefined,
      registry,
      anonymousUsers,
      { idleTtlMs: 1_000, intervalMs: 10 },
    );
    try {
      const address = await server.start();
      const origin = `http://127.0.0.1:${address.port}`;
      const createdResponse = await fetch(`${origin}/api/clients`, {
        method: "POST",
        headers: { Cookie: cookie, "Content-Type": "application/json" },
        body: "{}",
      });
      const created = (await createdResponse.json()) as {
        client: { id: string };
        eventsUrl: string;
        messagesUrl: string;
      };
      const stream = http.get(`${origin}${created.eventsUrl}`, {
        headers: { Cookie: cookie },
      });
      await new Promise<void>((resolve, reject) => {
        stream.once("response", () => resolve());
        stream.once("error", reject);
      });
      const accepted = await fetch(`${origin}${created.messagesUrl}`, {
        method: "POST",
        headers: { Cookie: cookie, "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "command",
          payload: { id: "held-command", type: "get_state" },
        }),
      });
      expect(accepted.status).toBe(202);
      stream.destroy();
      await new Promise(resolve => setTimeout(resolve, 10));

      now = 101_001;
      await new Promise(resolve => setTimeout(resolve, 30));
      expect(fs.existsSync(retainedPath)).toBe(true);

      finishCommand();
      await waitUntil(() => !fs.existsSync(retainedPath));
    } finally {
      await server.stop();
      await registry.dispose();
    }
  });

  it("serializes an expired guest sweep with Anonymous User owner transfer", async () => {
    let now = 200_000;
    const runtimeRootPath = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-anonymous-transfer-race-"),
    );
    runtimeRoots.push(runtimeRootPath);
    const anonymousUsers = createAnonymousUserContextResolver({
      runtimeRootPath,
      secureCookie: false,
      now: () => now,
      activityWriteIntervalMs: 100,
    });
    const guest = await anonymousUsers.resolveForClient!({});
    if (!guest) throw new Error("Expected Anonymous User");
    const guestContext = guest.userContext;
    const cookie = guest.setCookie!.split(";", 1)[0];
    const target = {
      user: { id: "authenticated-transfer-target", username: "Target" },
      folderPath: path.join(runtimeRootPath, "users", "authenticated-target"),
    };
    fs.mkdirSync(target.folderPath, { recursive: true });
    const transferStarted = deferred<void>();
    const finishTransfer = deferred<void>();
    const retireUser = vi.fn(async () => {});
    const registry = {
      async transferOwnership(
        source: typeof guestContext,
        destination: typeof target,
        options: {
          assertIdle(): void;
          commitOwnership(paths: {
            sourceUserId: string;
            targetUserId: string;
            sourceWorkspacePath: string;
            targetWorkspacePath: string;
            mapUserPath(path: string): string;
          }): Promise<void>;
        },
      ) {
        options.assertIdle();
        transferStarted.resolve();
        await finishTransfer.promise;
        await options.commitOwnership({
          sourceUserId: source.user.id,
          targetUserId: destination.user.id,
          sourceWorkspacePath: source.folderPath,
          targetWorkspacePath: destination.folderPath,
          mapUserPath: candidatePath => candidatePath,
        });
      },
      retireUser,
    } as unknown as UserRuntimeRegistry;
    const authHandler: AuthHttpHandler = {
      async handle(req, res, url, lifecycle) {
        if (url.pathname !== "/api/test-transfer") return false;
        await lifecycle.transferAnonymousUser(
          req.headers,
          guestContext.user.id,
          target,
        );
        res.writeHead(204).end();
        return true;
      },
    };
    const server = cleanupRaceServer(
      runtimeRootPath,
      anonymousUsers,
      registry,
      authHandler,
    );
    try {
      const address = await server.start();
      const origin = `http://127.0.0.1:${address.port}`;
      const transferring = fetch(`${origin}/api/test-transfer`, {
        method: "POST",
        headers: { Cookie: cookie },
      });
      await transferStarted.promise;
      now = 201_001;
      await new Promise(resolve => setTimeout(resolve, 30));
      expect(retireUser).not.toHaveBeenCalled();

      finishTransfer.resolve();
      expect((await transferring).status).toBe(204);
      await new Promise(resolve => setTimeout(resolve, 30));
      expect(retireUser).toHaveBeenCalledTimes(1);
      expect(await anonymousUsers.resolveAnonymous!({ cookie })).toBeNull();
      expect(fs.existsSync(target.folderPath)).toBe(true);
    } finally {
      await server.stop();
    }
  });

  it("revalidates ownership after waiting for the cleanup gate", async () => {
    let now = 300_000;
    const runtimeRootPath = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-anonymous-cleanup-race-"),
    );
    runtimeRoots.push(runtimeRootPath);
    const anonymousUsers = createAnonymousUserContextResolver({
      runtimeRootPath,
      secureCookie: false,
      now: () => now,
    });
    const guest = await anonymousUsers.resolveForClient!({});
    if (!guest) throw new Error("Expected Anonymous User");
    const guestContext = guest.userContext;
    const cookie = guest.setCookie!.split(";", 1)[0];
    const cleanupStarted = deferred<void>();
    const finishCleanup = deferred<void>();
    const target = {
      user: { id: "authenticated-cleanup-target", username: "Target" },
      folderPath: path.join(runtimeRootPath, "users", "authenticated-target"),
    };
    const registry = {
      async transferOwnership() {
        throw new Error("transfer must not enter while cleanup owns the gate");
      },
      async retireUser() {
        cleanupStarted.resolve();
        await finishCleanup.promise;
      },
    } as unknown as UserRuntimeRegistry;
    const transferStarted = deferred<void>();
    const authHandler: AuthHttpHandler = {
      async handle(req, res, url, lifecycle) {
        if (url.pathname !== "/api/test-transfer") return false;
        transferStarted.resolve();
        await lifecycle.transferAnonymousUser(
          req.headers,
          guestContext.user.id,
          target,
        );
        res.writeHead(204).end();
        return true;
      },
    };
    const server = cleanupRaceServer(
      runtimeRootPath,
      anonymousUsers,
      registry,
      authHandler,
    );
    try {
      const address = await server.start();
      const origin = `http://127.0.0.1:${address.port}`;
      now = 301_001;
      await cleanupStarted.promise;
      const pending = fetch(`${origin}/api/test-transfer`, {
        method: "POST",
        headers: { Cookie: cookie },
      });
      await transferStarted.promise;

      finishCleanup.resolve();
      expect((await pending).status).toBe(401);
      await vi.waitFor(async () => {
        expect(await anonymousUsers.resolveAnonymous!({ cookie })).toBeNull();
      });
      const stale = await fetch(`${origin}/api/test-transfer`, {
        method: "POST",
        headers: { Cookie: cookie },
      });
      expect(stale.status).toBe(401);
    } finally {
      finishCleanup.resolve();
      await server.stop();
    }
  });
});

function cleanupRaceServer(
  runtimeRootPath: string,
  anonymousUsers: ReturnType<typeof createAnonymousUserContextResolver>,
  registry: UserRuntimeRegistry,
  authHandler: AuthHttpHandler,
): BridgeServer {
  return new BridgeServer(
    {
      ...DEFAULT_BRIDGE_CONFIG,
      host: "127.0.0.1",
      port: 0,
      upload: {
        ...DEFAULT_BRIDGE_CONFIG.upload,
        uploadDir: path.join(runtimeRootPath, "uploads"),
      },
    },
    () => {
      throw new Error("cleanup race does not create a Bridge Client");
    },
    new BridgeEventBus(DEFAULT_BRIDGE_CONFIG),
    () => {},
    anonymousUsers,
    authHandler,
    registry,
    anonymousUsers,
    { idleTtlMs: 1_000, intervalMs: 10 },
  );
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(settle => {
    resolve = settle;
  });
  return { promise, resolve };
}

async function waitUntil(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 2_000;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for cleanup");
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
