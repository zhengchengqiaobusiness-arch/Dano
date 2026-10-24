import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  handleScheduledTaskHttp,
  recurrenceIntervalMs,
  ScheduledTaskScheduler,
} from "../scheduled-task.js";
import { createScheduleTaskTool } from "../scheduled-task-tool.js";
import type { UserContext } from "../user-context.js";

const roots: string[] = [];

afterEach(() => {
  vi.restoreAllMocks();
  for (const root of roots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

function tempRoot(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-scheduled-task-"));
  roots.push(root);
  return root;
}

function userFolder(root: string, userId: string): string {
  const folderPath = path.join(root, "users", userId);
  fs.mkdirSync(path.join(folderPath, "workspaces", "default"), { recursive: true });
  return folderPath;
}

function futureIso(now: number, offsetMs = 60_000): string {
  return new Date(now + offsetMs).toISOString();
}

function writeSession(filePath: string): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, '{"type":"session"}\n');
}

function mockRegistry(session: {
  sessionId: string;
  sessionFile?: string;
  prompt: ReturnType<typeof vi.fn>;
  abort?: ReturnType<typeof vi.fn>;
  streaming?: boolean;
}) {
  const ensureSession = vi.fn(async () => session);
  const createSession = vi.fn(() => ({ ensureSession }));
  return {
    isSessionRunning: vi.fn(() => session.streaming === true),
    ensureSession,
    createSession,
    removeSession: vi.fn(async () => {}),
  };
}

describe("ScheduledTaskScheduler", () => {
  it("rejects an invalid nextRunAt and stores a legal ISO timestamp unchanged", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "alice");
    const now = 1_700_000_000_000;
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    const userContext: UserContext = {
      user: { id: "alice", username: "Alice" },
      folderPath,
    };
    await expect(
      scheduler.create(
        {
          taskName: "bad",
          userInput: "run skill",
          nextRunAt: "下周三三点",
          destination: "standalone",
        },
        { userContext, defaultWorkspacePath: path.join(folderPath, "workspaces", "default") },
      ),
    ).rejects.toMatchObject({ status: 400 });

    const nextRunAt = futureIso(now);
    const created = await scheduler.create(
      {
        taskName: "ok",
        userInput: "run skill",
        nextRunAt,
        destination: "standalone",
      },
      { userContext, defaultWorkspacePath: path.join(folderPath, "workspaces", "default") },
    );
    const stored = JSON.parse(
      fs.readFileSync(
        path.join(folderPath, "scheduled-tasks", `${created.taskId}.json`),
        "utf8",
      ),
    ) as { nextRunAt: string; ownerUsername?: string };
    expect(stored.nextRunAt).toBe(nextRunAt);
    expect(stored.ownerUsername).toBe("Alice");
    expect(created).not.toHaveProperty("loginSessionId");
    expect(created.hasLoginSession).toBe(false);
  });

  it("advances a daily task from the send time and prompts a missed run only once", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "alice");
    let now = 1_700_000_000_000;
    const sessionPath = path.join(folderPath, "sessions", "chat.jsonl");
    writeSession(sessionPath);
    const prompt = vi.fn(async () => {});
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const session = {
      sessionId: "agent-1",
      sessionFile: sessionPath,
      prompt,
      abort: vi.fn(async () => {}),
    };
    const registry = mockRegistry(session);
    const captured: UserContext[] = [];
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    scheduler.attach({
      userRuntimeRegistry: {
        get: async (owner: UserContext) => {
          captured.push(owner);
          return {
            userId: owner.user.id,
            backend: { sessionRegistry: registry },
            defaultWorkspacePath: path.join(folderPath, "workspaces", "default"),
            sessionsRootPath: path.join(folderPath, "sessions"),
          };
        },
      } as never,
    });
    const created = await scheduler.create(
      {
        taskName: "daily skill",
        userInput: "执行 xxx skill",
        nextRunAt: futureIso(now),
        repeat: "daily",
        destination: "conversation",
        sessionPath,
      },
      {
        userContext: { user: { id: "alice", username: "Alice" }, folderPath },
        loginSessionId: "login-keep",
      },
    );
    now += 2 * 86400e3;
    await scheduler.deliver(created.taskId, "schedule");
    expect(prompt).toHaveBeenCalledOnce();
    expect(prompt).toHaveBeenCalledWith("执行 xxx skill", { source: "rpc" });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(registry.createSession).not.toHaveBeenCalled();
    expect(captured[0]?.user).toEqual({ id: "alice", username: "Alice" });
    const stored = JSON.parse(
      fs.readFileSync(
        path.join(folderPath, "scheduled-tasks", `${created.taskId}.json`),
        "utf8",
      ),
    ) as { nextRunAt: string; loginSessionId?: string; intervalMs?: number };
    expect(Date.parse(stored.nextRunAt)).toBe(now + 86400e3);
    expect(stored.loginSessionId).toBe("login-keep");
    expect(stored.intervalMs).toBe(86400e3);
  });

  it("advances a 10-minute task from the send time", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "alice");
    let now = 1_700_000_000_000;
    const sessionPath = path.join(folderPath, "sessions", "chat.jsonl");
    writeSession(sessionPath);
    const prompt = vi.fn(async () => {});
    const session = {
      sessionId: "agent-10m",
      sessionFile: sessionPath,
      prompt,
      abort: vi.fn(async () => {}),
    };
    const registry = mockRegistry(session);
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    scheduler.attach({
      userRuntimeRegistry: {
        get: async () => ({
          userId: "alice",
          backend: { sessionRegistry: registry },
          defaultWorkspacePath: path.join(folderPath, "workspaces", "default"),
          sessionsRootPath: path.join(folderPath, "sessions"),
        }),
      } as never,
    });
    const created = await scheduler.create(
      {
        taskName: "every ten minutes",
        userInput: "发一位三国人物",
        nextRunAt: futureIso(now),
        intervalMinutes: 10,
        destination: "conversation",
        sessionPath,
      },
      {
        userContext: { user: { id: "alice", username: "Alice" }, folderPath },
      },
    );
    expect(created.repeat).toBe("interval");
    expect(created.intervalMs).toBe(600_000);
    now += 600_000;
    await scheduler.deliver(created.taskId, "schedule");
    const stored = JSON.parse(
      fs.readFileSync(
        path.join(folderPath, "scheduled-tasks", `${created.taskId}.json`),
        "utf8",
      ),
    ) as { nextRunAt: string; intervalMs?: number };
    expect(Date.parse(stored.nextRunAt)).toBe(now + 600_000);
    expect(stored.intervalMs).toBe(600_000);
  });

  it("treats a legacy daily repeat without intervalMs as 24 hours", () => {
    expect(recurrenceIntervalMs({ repeat: "daily" })).toBe(86400e3);
    expect(recurrenceIntervalMs({ repeat: "interval", intervalMs: 600_000 })).toBe(600_000);
    expect(recurrenceIntervalMs({ repeat: "none" })).toBeUndefined();
  });

  it("fails conversation create without a session file and disables deliver without createSession", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "bob");
    const now = Date.now();
    const missingPath = path.join(folderPath, "sessions", "gone.jsonl");
    const createSession = vi.fn();
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    scheduler.attach({
      userRuntimeRegistry: {
        get: async () => ({
          userId: "bob",
          backend: {
            sessionRegistry: {
              isSessionRunning: () => false,
              ensureSession: vi.fn(),
              createSession,
            },
          },
        }),
      } as never,
    });
    await expect(
      scheduler.create(
        {
          taskName: "missing",
          userInput: "do it",
          nextRunAt: futureIso(now),
          destination: "conversation",
          sessionPath: missingPath,
        },
        { userContext: { user: { id: "bob" }, folderPath } },
      ),
    ).rejects.toMatchObject({ status: 400 });

    writeSession(missingPath);
    const created = await scheduler.create(
      {
        taskName: "will vanish",
        userInput: "do it",
        nextRunAt: futureIso(now),
        destination: "conversation",
        sessionPath: missingPath,
      },
      { userContext: { user: { id: "bob" }, folderPath } },
    );
    fs.rmSync(missingPath);
    await scheduler.deliver(created.taskId, "schedule");
    const stored = JSON.parse(
      fs.readFileSync(
        path.join(folderPath, "scheduled-tasks", `${created.taskId}.json`),
        "utf8",
      ),
    ) as { enabled: boolean };
    expect(stored.enabled).toBe(false);
    expect(createSession).not.toHaveBeenCalled();
  });

  it("skips a streaming conversation without changing nextRunAt", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "carol");
    const now = 1_700_000_000_000;
    const sessionPath = path.join(folderPath, "sessions", "live.jsonl");
    writeSession(sessionPath);
    const prompt = vi.fn(async () => {});
    const nextRunAt = futureIso(now);
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    scheduler.attach({
      userRuntimeRegistry: {
        get: async () => ({
          userId: "carol",
          backend: {
            sessionRegistry: mockRegistry({
              sessionId: "live",
              sessionFile: sessionPath,
              prompt,
              streaming: true,
            }),
          },
        }),
      } as never,
    });
    const created = await scheduler.create(
      {
        taskName: "busy",
        userInput: "later",
        nextRunAt,
        destination: "conversation",
        sessionPath,
      },
      { userContext: { user: { id: "carol" }, folderPath } },
    );
    await scheduler.deliver(created.taskId, "schedule");
    expect(prompt).not.toHaveBeenCalled();
    expect(scheduler.get(created.taskId)?.nextRunAt).toBe(nextRunAt);
  });

  it("queues the stored Login Session before prompting", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "dave");
    const now = Date.now();
    const sessionPath = path.join(folderPath, "sessions", "oa.jsonl");
    writeSession(sessionPath);
    const prompt = vi.fn(async () => {});
    const queueAssistantTurn = vi.fn();
    const session = {
      sessionId: "oa-agent",
      sessionFile: sessionPath,
      prompt,
      abort: vi.fn(async () => {}),
    };
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    scheduler.attach({
      credentialBroker: { queueAssistantTurn } as never,
      userRuntimeRegistry: {
        get: async () => ({
          userId: "dave",
          backend: { sessionRegistry: mockRegistry(session) },
        }),
      } as never,
    });
    const created = await scheduler.create(
      {
        taskName: "oa",
        userInput: "提交请假",
        nextRunAt: futureIso(now),
        destination: "conversation",
        sessionPath,
      },
      {
        userContext: { user: { id: "dave", username: "Dave" }, folderPath },
        loginSessionId: "login-oa",
      },
    );
    await scheduler.deliver(created.taskId, "schedule");
    expect(queueAssistantTurn).toHaveBeenCalledWith(
      "dave",
      "oa-agent",
      "login-oa",
      "authenticated",
    );
    expect(prompt).toHaveBeenCalledOnce();
  });
});

describe("schedule_task tool", () => {
  it("has no destination or schedule object and binds the current conversation", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "erin");
    const now = Date.now();
    const sessionPath = path.join(folderPath, "sessions", "tool.jsonl");
    writeSession(sessionPath);
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    const tool = createScheduleTaskTool({
      scheduler,
      userContext: { user: { id: "erin", username: "Erin" }, folderPath },
      credentialBroker: {
        currentLoginSessionId: () => "login-from-turn",
      } as never,
      credentialBrokerScope: "erin",
    });
    expect(JSON.stringify(tool.parameters)).not.toMatch(/destination/);
    expect(JSON.stringify(tool.parameters)).not.toMatch(/"schedule"/);
    const result = await tool.execute(
      "call-1",
      {
        taskName: "from chat",
        userInput: "执行 xxx skill",
        nextRunAt: futureIso(now),
        repeat: "daily",
      },
      undefined,
      undefined,
      {
        sessionManager: {
          getSessionFile: () => sessionPath,
          getSessionId: () => "conv-1",
          getCwd: () => path.join(folderPath, "workspaces", "default"),
        },
      } as never,
    );
    expect(result.isError).toBeFalsy();
    const listed = scheduler.list("erin");
    expect(listed).toHaveLength(1);
    expect(listed[0]?.destination).toBe("conversation");
    expect(listed[0]?.payload.userInput).toBe("执行 xxx skill");
    expect(listed[0]?.sessionPath).toBe(sessionPath);
    expect(listed[0]?.hasLoginSession).toBe(true);
  });

  it("accepts a 10-minute interval without asking the model to use hourly or daily", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "erin");
    const now = Date.now();
    const sessionPath = path.join(folderPath, "sessions", "tool.jsonl");
    writeSession(sessionPath);
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    const tool = createScheduleTaskTool({
      scheduler,
      userContext: { user: { id: "erin", username: "Erin" }, folderPath },
    });
    const result = await tool.execute(
      "call-10m",
      {
        taskName: "三国人物",
        userInput: "发一位三国人物",
        nextRunAt: futureIso(now),
        intervalMinutes: "10",
      } as never,
      undefined,
      undefined,
      {
        sessionManager: {
          getSessionFile: () => sessionPath,
          getSessionId: () => "conv-10m",
          getCwd: () => path.join(folderPath, "workspaces", "default"),
        },
      } as never,
    );
    expect(result.isError).toBeFalsy();
    const listed = scheduler.list("erin");
    expect(listed[0]?.intervalMs).toBe(600_000);
    expect(listed[0]?.repeat).toBe("interval");
    expect(JSON.stringify(tool.parameters)).toMatch(/intervalMinutes/);
    expect(JSON.stringify(tool.promptGuidelines)).not.toMatch(/只支持/);
  });

  it("errors when the current session file is missing", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "frank");
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
    });
    const tool = createScheduleTaskTool({
      scheduler,
      userContext: { user: { id: "frank" }, folderPath },
    });
    const result = await tool.execute(
      "call-2",
      {
        taskName: "missing",
        userInput: "x",
        nextRunAt: futureIso(Date.now()),
      },
      undefined,
      undefined,
      {
        sessionManager: {
          getSessionFile: () => undefined,
          getSessionId: () => "none",
          getCwd: () => folderPath,
        },
      } as never,
    );
    expect(result.isError).toBe(true);
  });
});

describe("scheduled task HTTP", () => {
  it("returns 400 for an illegal nextRunAt and ignores loginSessionId in the body", async () => {
    const root = tempRoot();
    const folderPath = userFolder(root, "guest");
    const now = Date.now();
    const scheduler = new ScheduledTaskScheduler({
      usersRoot: path.join(root, "users"),
      now: () => now,
    });
    const invalid = await handleScheduledTaskHttp({
      method: "POST",
      pathname: "/api/clients/c1/scheduled-tasks",
      userContext: { user: { id: "guest" }, folderPath },
      loginSessionId: "from-connection",
      defaultWorkspacePath: path.join(folderPath, "workspaces", "default"),
      scheduler,
      readJsonBody: async () => ({
        taskName: "n",
        userInput: "do",
        nextRunAt: "not-iso",
        destination: "standalone",
        loginSessionId: "from-body",
      }),
    });
    expect(invalid).toEqual({
      status: 400,
      body: { error: "nextRunAt must be an ISO timestamp" },
    });

    const created = await handleScheduledTaskHttp({
      method: "POST",
      pathname: "/api/clients/c1/scheduled-tasks",
      userContext: { user: { id: "guest" }, folderPath },
      loginSessionId: "from-connection",
      defaultWorkspacePath: path.join(folderPath, "workspaces", "default"),
      scheduler,
      readJsonBody: async () => ({
        taskName: "n",
        userInput: "do",
        nextRunAt: futureIso(now),
        destination: "standalone",
        loginSessionId: "from-body",
      }),
    });
    expect(created?.status).toBe(201);
    const taskId = (created?.body as { taskId: string }).taskId;
    const stored = JSON.parse(
      fs.readFileSync(path.join(folderPath, "scheduled-tasks", `${taskId}.json`), "utf8"),
    ) as { loginSessionId?: string };
    expect(stored.loginSessionId).toBe("from-connection");
  });
});
