import { createHash, randomUUID } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { writeFile as writeFileAtomically } from "atomically";
import type { CredentialBroker } from "./credential-broker.js";
import { workspaceSessionDirectoryPath } from "./runtime-layout.js";
import { UserContextError, type UserContext } from "./user-context.js";
import type { UserRuntimeRegistry } from "./user-runtime-registry.js";

const TASKS_DIR = "scheduled-tasks";
const RUNS_DIR = "scheduled-runs";
const PROMPT_TIMEOUT_MS = 600_000;
const DEFAULT_INTERVAL_MS = 30_000;
const REPEAT_MS = {
  hourly: 3600e3,
  daily: 86400e3,
  weekly: 604800e3,
} as const;

export type ScheduledTaskDestination = "conversation" | "standalone";
export type ScheduledTaskRepeat =
  | "none"
  | "hourly"
  | "daily"
  | "weekly"
  | "interval";
export type ScheduledTaskTrigger = "schedule" | "manual";

export interface ScheduledTaskRecord {
  taskId: string;
  destination: ScheduledTaskDestination;
  enabled: boolean;
  nextRunAt: string;
  repeat: ScheduledTaskRepeat;
  intervalMs?: number;
  taskName: string;
  ownerUserId: string;
  ownerUsername?: string;
  conversationId?: string;
  sessionPath?: string;
  workspacePath: string;
  loginSessionId?: string;
  payload: { userInput: string };
  lastRunAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface ScheduledTaskPublic extends Omit<ScheduledTaskRecord, "loginSessionId"> {
  hasLoginSession: boolean;
}

export interface ScheduledTaskRunRecord {
  runId: string;
  taskId: string;
  trigger: ScheduledTaskTrigger;
  startedAt: string;
  finishedAt?: string;
  status: "ok" | "error" | "skipped";
  outputSessionPath?: string;
  error?: string;
}

export interface ScheduledTaskRecurrenceInput {
  repeat?: unknown;
  intervalMs?: unknown;
  intervalSeconds?: unknown;
  intervalMinutes?: unknown;
  intervalHours?: unknown;
}

export interface ScheduledTaskCreateInput extends ScheduledTaskRecurrenceInput {
  taskName: string;
  userInput: string;
  nextRunAt: string;
  destination: string;
  sessionPath?: string;
  conversationId?: string;
  workspacePath?: string;
}

export interface ScheduledTaskUpdateInput extends ScheduledTaskRecurrenceInput {
  enabled?: boolean;
  taskName?: string;
  userInput?: string;
  nextRunAt?: string;
  destination?: string;
  sessionPath?: string;
  conversationId?: string;
  workspacePath?: string;
}

export interface ScheduledTaskCreateContext {
  userContext: UserContext;
  loginSessionId?: string;
  defaultWorkspacePath?: string;
}

export interface ScheduledTaskRewriteMapping {
  ownerUserId: string;
  ownerUsername?: string;
  sourceFolderPath: string;
  targetFolderPath: string;
  sourceSessionsRoot: string;
  targetSessionsRoot: string;
}

export interface ScheduledTaskSchedulerOptions {
  readonly usersRoot: string;
  readonly now?: () => number;
  readonly intervalMs?: number;
  readonly promptTimeoutMs?: number;
  readonly onError?: (error: unknown) => void;
}

export class ScheduledTaskHttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export class ScheduledTaskScheduler {
  private readonly tasks = new Map<string, ScheduledTaskRecord>();
  private readonly delivering = new Set<string>();
  private readonly usersRoot: string;
  private readonly now: () => number;
  private readonly intervalMs: number;
  private readonly promptTimeoutMs: number;
  private interval: ReturnType<typeof setInterval> | undefined;
  private runningTick: Promise<void> | undefined;
  private disposed = false;
  private userRuntimeRegistry?: UserRuntimeRegistry;
  private credentialBroker?: CredentialBroker;

  constructor(private readonly options: ScheduledTaskSchedulerOptions) {
    this.usersRoot = path.resolve(options.usersRoot);
    this.now = options.now ?? Date.now;
    this.intervalMs = options.intervalMs ?? DEFAULT_INTERVAL_MS;
    this.promptTimeoutMs = options.promptTimeoutMs ?? PROMPT_TIMEOUT_MS;
    if (!Number.isSafeInteger(this.intervalMs) || this.intervalMs <= 0) {
      throw new Error("Scheduled task interval must be a positive integer");
    }
  }

  attach(deps: {
    userRuntimeRegistry?: UserRuntimeRegistry;
    credentialBroker?: CredentialBroker;
  }): void {
    this.userRuntimeRegistry = deps.userRuntimeRegistry;
    this.credentialBroker = deps.credentialBroker;
  }

  async restore(): Promise<void> {
    this.tasks.clear();
    let userDirs: fs.Dirent[];
    try {
      userDirs = await fs.promises.readdir(this.usersRoot, { withFileTypes: true });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
      throw error;
    }
    for (const userDir of userDirs) {
      if (!userDir.isDirectory()) continue;
      const folderPath = path.join(this.usersRoot, userDir.name);
      const taskDir = path.join(folderPath, TASKS_DIR);
      let files: string[];
      try {
        files = await fs.promises.readdir(taskDir);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
        throw error;
      }
      for (const name of files) {
        if (!name.endsWith(".json")) continue;
        const task = await readTaskFile(path.join(taskDir, name));
        if (task) this.tasks.set(task.taskId, task);
      }
    }
  }

  async start(): Promise<void> {
    if (this.interval || this.disposed) return;
    await this.runTick();
    this.interval = setInterval(() => {
      void this.runTick();
    }, this.intervalMs);
    this.interval.unref?.();
  }

  async dispose(): Promise<void> {
    if (this.disposed && !this.interval && !this.runningTick) return;
    this.disposed = true;
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = undefined;
    }
    await this.runningTick;
  }

  async create(
    input: ScheduledTaskCreateInput,
    context: ScheduledTaskCreateContext,
  ): Promise<ScheduledTaskPublic> {
    const task = this.buildTask(input, context);
    await this.writeTask(context.userContext.folderPath, task);
    this.tasks.set(task.taskId, task);
    return toPublicTask(task);
  }

  list(ownerUserId: string): ScheduledTaskPublic[] {
    return [...this.tasks.values()]
      .filter(task => task.ownerUserId === ownerUserId)
      .sort((left, right) => left.nextRunAt.localeCompare(right.nextRunAt))
      .map(toPublicTask);
  }

  get(taskId: string): ScheduledTaskPublic | undefined {
    const task = this.tasks.get(taskId);
    return task ? toPublicTask(task) : undefined;
  }

  async update(
    taskId: string,
    ownerUserId: string,
    folderPath: string,
    patch: ScheduledTaskUpdateInput,
  ): Promise<ScheduledTaskPublic> {
    const current = this.requireOwnedTask(taskId, ownerUserId);
    const nextRunAt =
      patch.nextRunAt !== undefined
        ? this.requireFutureIso(patch.nextRunAt)
        : current.nextRunAt;
    const destination =
      patch.destination !== undefined
        ? parseDestination(patch.destination)
        : current.destination;
    const sessionPath =
      patch.sessionPath !== undefined
        ? optionalString(patch.sessionPath)
        : current.sessionPath;
    const conversationId =
      patch.conversationId !== undefined
        ? optionalString(patch.conversationId)
        : current.conversationId;
    const workspacePath =
      patch.workspacePath !== undefined
        ? requiredString(patch.workspacePath, "workspacePath")
        : current.workspacePath;
    const recurrence = hasRecurrencePatch(patch)
      ? parseRecurrence(patch)
      : { repeat: current.repeat, intervalMs: current.intervalMs };
    const taskName =
      patch.taskName !== undefined
        ? requiredString(patch.taskName, "taskName")
        : current.taskName;
    const userInput =
      patch.userInput !== undefined
        ? requiredString(patch.userInput, "userInput")
        : current.payload.userInput;
    if (destination === "conversation") {
      await this.assertConversationSession(sessionPath, folderPath);
    }
    const updated: ScheduledTaskRecord = {
      ...current,
      enabled: patch.enabled ?? current.enabled,
      nextRunAt,
      destination,
      sessionPath,
      conversationId,
      workspacePath,
      repeat: recurrence.repeat,
      intervalMs: recurrence.intervalMs,
      taskName,
      payload: { userInput },
      updatedAt: new Date(this.now()).toISOString(),
    };
    if (!updated.intervalMs) delete updated.intervalMs;
    await this.writeTask(folderPath, updated);
    this.tasks.set(taskId, updated);
    return toPublicTask(updated);
  }

  async remove(taskId: string, ownerUserId: string, folderPath: string): Promise<void> {
    this.requireOwnedTask(taskId, ownerUserId);
    this.tasks.delete(taskId);
    await fs.promises.rm(path.join(folderPath, TASKS_DIR, `${taskId}.json`), {
      force: true,
    });
  }

  async listRuns(
    taskId: string,
    ownerUserId: string,
    folderPath: string,
  ): Promise<ScheduledTaskRunRecord[]> {
    this.requireOwnedTask(taskId, ownerUserId);
    const runDir = path.join(folderPath, RUNS_DIR, taskId);
    let files: string[];
    try {
      files = await fs.promises.readdir(runDir);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
    const runs: ScheduledTaskRunRecord[] = [];
    for (const name of files) {
      if (!name.endsWith(".json")) continue;
      const run = await readRunFile(path.join(runDir, name));
      if (run) runs.push(run);
    }
    return runs.sort((left, right) => right.startedAt.localeCompare(left.startedAt));
  }

  async runNow(taskId: string, ownerUserId: string): Promise<ScheduledTaskRunRecord | undefined> {
    this.requireOwnedTask(taskId, ownerUserId);
    return this.deliver(taskId, "manual");
  }

  async deliver(
    taskId: string,
    trigger: ScheduledTaskTrigger,
  ): Promise<ScheduledTaskRunRecord | undefined> {
    if (this.delivering.has(taskId)) return undefined;
    const task = this.tasks.get(taskId);
    if (!task || (trigger === "schedule" && !task.enabled)) return undefined;
    this.delivering.add(taskId);
    const startedAt = new Date(this.now()).toISOString();
    const runId = randomUUID();
    let standaloneSessionPath: string | undefined;
    let sessionRegistry: { removeSession(sessionPath: string): Promise<void> } | undefined;
    try {
      const folderPath = path.join(this.usersRoot, task.ownerUserId);
      const owner: UserContext = task.ownerUsername
        ? {
            user: { id: task.ownerUserId, username: task.ownerUsername },
            folderPath,
          }
        : { user: { id: task.ownerUserId }, folderPath };
      const runtime = await this.userRuntimeRegistry?.get(owner);
      const registry = runtime?.backend.sessionRegistry;
      sessionRegistry = registry;
      if (!runtime || !registry) {
        throw new Error("Scheduled task runtime is unavailable");
      }

      if (task.destination === "conversation") {
        if (!task.sessionPath || !fs.existsSync(task.sessionPath)) {
          const disabled = {
            ...task,
            enabled: false,
            updatedAt: new Date(this.now()).toISOString(),
          };
          await this.writeTask(folderPath, disabled);
          this.tasks.set(taskId, disabled);
          const run = await this.writeRun(folderPath, {
            runId,
            taskId,
            trigger,
            startedAt,
            finishedAt: new Date(this.now()).toISOString(),
            status: "error",
            error: "Conversation session file was not found",
          });
          return run;
        }
        if (registry.isSessionRunning(task.sessionPath)) {
          return undefined;
        }
      }

      const session =
        task.destination === "conversation"
          ? await registry.ensureSession(task.sessionPath!)
          : await registry
              .createSession({
                cwd: task.workspacePath,
                sessionDir: workspaceSessionDirectoryPath(
                  runtime.sessionsRootPath,
                  task.workspacePath,
                ),
              })
              .ensureSession();
      if (task.destination === "standalone") {
        standaloneSessionPath = session.sessionFile ?? undefined;
      }

      if (task.loginSessionId && this.credentialBroker) {
        this.credentialBroker.queueAssistantTurn(
          runtime.userId,
          session.sessionId,
          task.loginSessionId,
          "authenticated",
        );
      }

      await withTimeout(
        Promise.resolve(
          session.prompt(task.payload.userInput, { source: "rpc" }),
        ),
        this.promptTimeoutMs,
        () => {
          void session.abort();
        },
      );

      const finishedAt = new Date(this.now()).toISOString();
      if (trigger === "schedule") {
        const interval = recurrenceIntervalMs(task);
        const advanced: ScheduledTaskRecord = {
          ...task,
          lastRunAt: finishedAt,
          updatedAt: finishedAt,
          ...(interval
            ? {
                nextRunAt: new Date(this.now() + interval).toISOString(),
              }
            : { enabled: false }),
        };
        await this.writeTask(folderPath, advanced);
        this.tasks.set(taskId, advanced);
      } else {
        const touched = {
          ...task,
          lastRunAt: finishedAt,
          updatedAt: finishedAt,
        };
        await this.writeTask(folderPath, touched);
        this.tasks.set(taskId, touched);
      }

      return this.writeRun(folderPath, {
        runId,
        taskId,
        trigger,
        startedAt,
        finishedAt,
        status: "ok",
        outputSessionPath: session.sessionFile ?? task.sessionPath,
      });
    } catch (error) {
      const folderPath = path.join(this.usersRoot, task.ownerUserId);
      const run = await this.writeRun(folderPath, {
        runId,
        taskId,
        trigger,
        startedAt,
        finishedAt: new Date(this.now()).toISOString(),
        status: "error",
        error: error instanceof Error ? error.message : String(error),
      });
      this.options.onError?.(error);
      return run;
    } finally {
      if (standaloneSessionPath) {
        await sessionRegistry?.removeSession(standaloneSessionPath).catch(() => {});
      }
      this.delivering.delete(taskId);
    }
  }

  async rewriteCopiedTasks(
    folderPath: string,
    mapping: ScheduledTaskRewriteMapping,
  ): Promise<void> {
    const taskDir = path.join(folderPath, TASKS_DIR);
    let files: string[];
    try {
      files = await fs.promises.readdir(taskDir);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
      throw error;
    }
    for (const name of files) {
      if (!name.endsWith(".json")) continue;
      const filePath = path.join(taskDir, name);
      const task = await readTaskFile(filePath);
      if (!task) continue;
      const rewritten: ScheduledTaskRecord = {
        ...task,
        ownerUserId: mapping.ownerUserId,
        workspacePath: mapTransferredPath(task.workspacePath, mapping),
        sessionPath: task.sessionPath
          ? mapTransferredPath(task.sessionPath, mapping)
          : task.sessionPath,
        updatedAt: new Date(this.now()).toISOString(),
      };
      if (mapping.ownerUsername) rewritten.ownerUsername = mapping.ownerUsername;
      else delete rewritten.ownerUsername;
      await writeJsonFile(filePath, rewritten);
      this.tasks.set(rewritten.taskId, rewritten);
    }
  }

  unloadUser(userId: string): void {
    for (const [taskId, task] of this.tasks) {
      if (task.ownerUserId === userId) this.tasks.delete(taskId);
    }
  }

  hasEnabledTasks(userId: string): boolean {
    for (const task of this.tasks.values()) {
      if (task.ownerUserId === userId && task.enabled) return true;
    }
    const taskDir = path.join(this.usersRoot, userId, TASKS_DIR);
    let files: string[];
    try {
      files = fs.readdirSync(taskDir);
    } catch {
      return false;
    }
    for (const name of files) {
      if (!name.endsWith(".json")) continue;
      try {
        const task = JSON.parse(
          fs.readFileSync(path.join(taskDir, name), "utf8"),
        ) as Partial<ScheduledTaskRecord>;
        if (task.enabled === true) return true;
      } catch {
        // Ignore unreadable task files when deciding retention.
      }
    }
    return false;
  }

  retainedLoginSessionFileNames(): Set<string> {
    const names = new Set<string>();
    for (const task of this.tasks.values()) {
      if (task.enabled && task.loginSessionId) {
        names.add(loginSessionFileName(task.loginSessionId));
      }
    }
    return names;
  }

  private buildTask(
    input: ScheduledTaskCreateInput,
    context: ScheduledTaskCreateContext,
  ): ScheduledTaskRecord {
    const destination = parseDestination(input.destination);
    const recurrence = parseRecurrence(input);
    const taskName = requiredString(input.taskName, "taskName");
    const userInput = requiredString(input.userInput, "userInput");
    const nextRunAt = this.requireFutureIso(input.nextRunAt);
    const workspacePath =
      optionalString(input.workspacePath) ??
      context.defaultWorkspacePath ??
      path.join(context.userContext.folderPath, "workspaces", "default");
    const sessionPath = optionalString(input.sessionPath);
    const conversationId = optionalString(input.conversationId);
    if (destination === "conversation") {
      if (!sessionPath || !fs.existsSync(sessionPath)) {
        throw new ScheduledTaskHttpError(
          400,
          "Conversation session file is required",
        );
      }
    }
    const nowIso = new Date(this.now()).toISOString();
    const ownerUsername =
      "username" in context.userContext.user
        ? context.userContext.user.username
        : undefined;
    return {
      taskId: randomUUID(),
      destination,
      enabled: true,
      nextRunAt,
      repeat: recurrence.repeat,
      ...(recurrence.intervalMs ? { intervalMs: recurrence.intervalMs } : {}),
      taskName,
      ownerUserId: context.userContext.user.id,
      ...(ownerUsername ? { ownerUsername } : {}),
      ...(conversationId ? { conversationId } : {}),
      ...(sessionPath ? { sessionPath } : {}),
      workspacePath,
      ...(context.loginSessionId ? { loginSessionId: context.loginSessionId } : {}),
      payload: { userInput },
      createdAt: nowIso,
      updatedAt: nowIso,
    };
  }

  private requireOwnedTask(taskId: string, ownerUserId: string): ScheduledTaskRecord {
    const task = this.tasks.get(taskId);
    if (!task || task.ownerUserId !== ownerUserId) {
      throw new ScheduledTaskHttpError(404, "Scheduled task was not found");
    }
    return task;
  }

  private requireFutureIso(value: string): string {
    if (typeof value !== "string" || !value.trim()) {
      throw new ScheduledTaskHttpError(400, "nextRunAt must be an ISO timestamp");
    }
    const parsed = Date.parse(value);
    if (!Number.isFinite(parsed)) {
      throw new ScheduledTaskHttpError(400, "nextRunAt must be an ISO timestamp");
    }
    if (parsed <= this.now()) {
      throw new ScheduledTaskHttpError(400, "nextRunAt must be in the future");
    }
    return value;
  }

  private async assertConversationSession(
    sessionPath: string | undefined,
    _folderPath: string,
  ): Promise<void> {
    if (!sessionPath || !fs.existsSync(sessionPath)) {
      throw new ScheduledTaskHttpError(
        400,
        "Conversation session file is required",
      );
    }
  }

  private async writeTask(folderPath: string, task: ScheduledTaskRecord): Promise<void> {
    const taskDir = path.join(folderPath, TASKS_DIR);
    await fs.promises.mkdir(taskDir, { recursive: true });
    await writeJsonFile(path.join(taskDir, `${task.taskId}.json`), task);
  }

  private async writeRun(
    folderPath: string,
    run: ScheduledTaskRunRecord,
  ): Promise<ScheduledTaskRunRecord> {
    const runDir = path.join(folderPath, RUNS_DIR, run.taskId);
    await fs.promises.mkdir(runDir, { recursive: true });
    await writeJsonFile(path.join(runDir, `${run.runId}.json`), run);
    return run;
  }

  private runTick(): Promise<void> {
    if (this.runningTick) return this.runningTick;
    const tick = this.tick()
      .catch(error => {
        this.options.onError?.(error);
      })
      .finally(() => {
        if (this.runningTick === tick) this.runningTick = undefined;
      });
    this.runningTick = tick;
    return tick;
  }

  private async tick(): Promise<void> {
    const now = this.now();
    const due = [...this.tasks.values()].filter(
      task => task.enabled && Date.parse(task.nextRunAt) <= now,
    );
    for (const task of due) {
      await this.deliver(task.taskId, "schedule");
    }
  }
}

export async function handleScheduledTaskHttp(options: {
  method?: string;
  pathname: string;
  userContext: UserContext | undefined;
  loginSessionId?: string;
  defaultWorkspacePath?: string;
  scheduler: ScheduledTaskScheduler;
  readJsonBody: () => Promise<unknown>;
}): Promise<{ status: number; body: unknown } | null> {
  const listMatch = /^\/api\/clients\/([^/]+)\/scheduled-tasks$/.exec(
    options.pathname,
  );
  const runMatch = /^\/api\/clients\/([^/]+)\/scheduled-tasks\/([^/]+)\/run$/.exec(
    options.pathname,
  );
  const runsMatch = /^\/api\/clients\/([^/]+)\/scheduled-tasks\/([^/]+)\/runs$/.exec(
    options.pathname,
  );
  const itemMatch = /^\/api\/clients\/([^/]+)\/scheduled-tasks\/([^/]+)$/.exec(
    options.pathname,
  );
  if (!listMatch && !runMatch && !runsMatch && !itemMatch) return null;

  const userContext = options.userContext;
  if (!userContext) {
    throw new UserContextError(401, "Authenticated User is required");
  }
  const method = options.method ?? "GET";
  try {
    if (listMatch) {
      if (method === "GET") {
        return { status: 200, body: { tasks: options.scheduler.list(userContext.user.id) } };
      }
      if (method === "POST") {
        const body = stripLoginSessionId(await options.readJsonBody());
        const task = await options.scheduler.create(
          {
            taskName: asString(body.taskName),
            userInput: asString(body.userInput),
            nextRunAt: asString(body.nextRunAt),
            ...recurrenceFromBody(body),
            destination: asString(body.destination),
            sessionPath: asString(body.sessionPath) || undefined,
            conversationId: asString(body.conversationId) || undefined,
            workspacePath: asString(body.workspacePath) || undefined,
          },
          {
            userContext,
            loginSessionId: options.loginSessionId,
            defaultWorkspacePath: options.defaultWorkspacePath,
          },
        );
        return { status: 201, body: task };
      }
      throw new ScheduledTaskHttpError(405, "Method is not allowed");
    }
    if (runMatch?.[2] && method === "POST") {
      await options.readJsonBody();
      const run = await options.scheduler.runNow(
        decodeURIComponent(runMatch[2]),
        userContext.user.id,
      );
      if (!run) {
        return { status: 409, body: { error: "Scheduled task could not run now" } };
      }
      return { status: 200, body: run };
    }
    if (runsMatch?.[2] && method === "GET") {
      const runs = await options.scheduler.listRuns(
        decodeURIComponent(runsMatch[2]),
        userContext.user.id,
        userContext.folderPath,
      );
      return { status: 200, body: { runs } };
    }
    if (itemMatch?.[2]) {
      const taskId = decodeURIComponent(itemMatch[2]);
      if (method === "GET") {
        const task = options.scheduler.get(taskId);
        if (!task || task.ownerUserId !== userContext.user.id) {
          throw new ScheduledTaskHttpError(404, "Scheduled task was not found");
        }
        return { status: 200, body: task };
      }
      if (method === "PATCH") {
        const body = stripLoginSessionId(await options.readJsonBody());
        const task = await options.scheduler.update(
          taskId,
          userContext.user.id,
          userContext.folderPath,
          {
            enabled: typeof body.enabled === "boolean" ? body.enabled : undefined,
            taskName: asString(body.taskName) || undefined,
            userInput: asString(body.userInput) || undefined,
            nextRunAt: asString(body.nextRunAt) || undefined,
            ...recurrenceFromBody(body),
            destination: asString(body.destination) || undefined,
            sessionPath: asString(body.sessionPath) || undefined,
            conversationId: asString(body.conversationId) || undefined,
            workspacePath: asString(body.workspacePath) || undefined,
          },
        );
        return { status: 200, body: task };
      }
      if (method === "DELETE") {
        await options.readJsonBody();
        await options.scheduler.remove(
          taskId,
          userContext.user.id,
          userContext.folderPath,
        );
        return { status: 200, body: { ok: true } };
      }
      throw new ScheduledTaskHttpError(405, "Method is not allowed");
    }
    return null;
  } catch (error) {
    if (error instanceof ScheduledTaskHttpError) {
      return { status: error.status, body: { error: error.message } };
    }
    throw error;
  }
}

export function toPublicTask(task: ScheduledTaskRecord): ScheduledTaskPublic {
  const { loginSessionId: _loginSessionId, ...rest } = task;
  return { ...rest, hasLoginSession: Boolean(task.loginSessionId) };
}

export function loginSessionFileName(loginSessionId: string): string {
  return `${createHash("sha256").update(loginSessionId).digest("hex")}.json`;
}

function parseDestination(value: string): ScheduledTaskDestination {
  if (value === "conversation" || value === "standalone") return value;
  throw new ScheduledTaskHttpError(400, "destination is invalid");
}

export function recurrenceIntervalMs(task: {
  repeat: string;
  intervalMs?: number;
}): number | undefined {
  if (typeof task.intervalMs === "number" && task.intervalMs > 0) {
    return Math.round(task.intervalMs);
  }
  if (task.repeat === "hourly" || task.repeat === "daily" || task.repeat === "weekly") {
    return REPEAT_MS[task.repeat];
  }
  return undefined;
}

export function parseRecurrence(
  input: ScheduledTaskRecurrenceInput,
): { repeat: ScheduledTaskRepeat; intervalMs?: number } {
  const fromUnits = firstDefinedIntervalMs(input);
  const label = parseRepeatLabel(input.repeat);
  if (fromUnits !== undefined) {
    return { repeat: namedRepeat(fromUnits), intervalMs: fromUnits };
  }
  if (label === "hourly" || label === "daily" || label === "weekly") {
    return { repeat: label, intervalMs: REPEAT_MS[label] };
  }
  if (label === "interval") {
    throw new ScheduledTaskHttpError(400, "intervalMs is required");
  }
  return { repeat: "none" };
}

function hasRecurrencePatch(patch: ScheduledTaskRecurrenceInput): boolean {
  return (
    patch.repeat !== undefined ||
    patch.intervalMs !== undefined ||
    patch.intervalSeconds !== undefined ||
    patch.intervalMinutes !== undefined ||
    patch.intervalHours !== undefined
  );
}

function recurrenceFromBody(body: Record<string, unknown>): ScheduledTaskRecurrenceInput {
  return {
    repeat: body.repeat,
    intervalMs: body.intervalMs,
    intervalSeconds: body.intervalSeconds,
    intervalMinutes: body.intervalMinutes,
    intervalHours: body.intervalHours,
  };
}

function namedRepeat(intervalMs: number): ScheduledTaskRepeat {
  if (intervalMs === REPEAT_MS.hourly) return "hourly";
  if (intervalMs === REPEAT_MS.daily) return "daily";
  if (intervalMs === REPEAT_MS.weekly) return "weekly";
  return "interval";
}

function parseRepeatLabel(value: unknown): ScheduledTaskRepeat | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  if (
    value === "none" ||
    value === "hourly" ||
    value === "daily" ||
    value === "weekly" ||
    value === "interval"
  ) {
    return value;
  }
  throw new ScheduledTaskHttpError(400, "repeat is invalid");
}

function firstDefinedIntervalMs(
  input: ScheduledTaskRecurrenceInput,
): number | undefined {
  const fields: Array<[unknown, number, string]> = [
    [input.intervalMs, 1, "intervalMs"],
    [input.intervalSeconds, 1000, "intervalSeconds"],
    [input.intervalMinutes, 60_000, "intervalMinutes"],
    [input.intervalHours, 3600e3, "intervalHours"],
  ];
  for (const [value, multiplier, field] of fields) {
    const parsed = optionalPositiveMs(value, multiplier, field);
    if (parsed !== undefined) return parsed;
  }
  return undefined;
}

function optionalPositiveMs(
  value: unknown,
  multiplier: number,
  field: string,
): number | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  const n =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value.trim())
        : Number.NaN;
  if (!Number.isFinite(n)) {
    throw new ScheduledTaskHttpError(400, `${field} is invalid`);
  }
  if (n <= 0) return undefined;
  const ms = Math.round(n * multiplier);
  if (ms <= 0 || !Number.isSafeInteger(ms)) {
    throw new ScheduledTaskHttpError(400, `${field} is invalid`);
  }
  return ms;
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new ScheduledTaskHttpError(400, `${field} is required`);
  }
  return value.trim();
}

function optionalString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed || undefined;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function stripLoginSessionId(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object" || Array.isArray(body)) return {};
  const { loginSessionId: _ignored, ...rest } = body as Record<string, unknown>;
  return rest;
}

function mapTransferredPath(
  candidatePath: string,
  mapping: ScheduledTaskRewriteMapping,
): string {
  const sourceEncoded = encodeFolderName(mapping.sourceFolderPath);
  const targetEncoded = encodeFolderName(mapping.targetFolderPath);
  let next = candidatePath;
  const sourceSessions = path.resolve(mapping.sourceSessionsRoot);
  const targetSessions = path.resolve(mapping.targetSessionsRoot);
  const sourceFolder = path.resolve(mapping.sourceFolderPath);
  const targetFolder = path.resolve(mapping.targetFolderPath);
  const resolved = path.resolve(candidatePath);
  if (resolved === sourceSessions || resolved.startsWith(`${sourceSessions}${path.sep}`)) {
    next = path.join(targetSessions, path.relative(sourceSessions, resolved));
  } else if (resolved === sourceFolder || resolved.startsWith(`${sourceFolder}${path.sep}`)) {
    next = path.join(targetFolder, path.relative(sourceFolder, resolved));
  }
  return next.split(sourceEncoded).join(targetEncoded);
}

function encodeFolderName(folderPath: string): string {
  return folderPath.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-");
}

async function writeJsonFile(filePath: string, value: unknown): Promise<void> {
  await writeFileAtomically(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

async function readTaskFile(filePath: string): Promise<ScheduledTaskRecord | undefined> {
  try {
    const value = JSON.parse(await fs.promises.readFile(filePath, "utf8")) as Partial<ScheduledTaskRecord>;
    if (
      typeof value.taskId !== "string" ||
      (value.destination !== "conversation" && value.destination !== "standalone") ||
      typeof value.enabled !== "boolean" ||
      typeof value.nextRunAt !== "string" ||
      (value.repeat !== undefined &&
        value.repeat !== "none" &&
        value.repeat !== "hourly" &&
        value.repeat !== "daily" &&
        value.repeat !== "weekly" &&
        value.repeat !== "interval") ||
      (value.intervalMs !== undefined &&
        (typeof value.intervalMs !== "number" ||
          !Number.isFinite(value.intervalMs) ||
          value.intervalMs <= 0)) ||
      typeof value.taskName !== "string" ||
      typeof value.ownerUserId !== "string" ||
      typeof value.workspacePath !== "string" ||
      !value.payload ||
      typeof value.payload.userInput !== "string"
    ) {
      return undefined;
    }
    return {
      ...value,
      repeat:
        value.repeat ??
        (typeof value.intervalMs === "number"
          ? namedRepeat(value.intervalMs)
          : "none"),
    } as ScheduledTaskRecord;
  } catch {
    return undefined;
  }
}

async function readRunFile(filePath: string): Promise<ScheduledTaskRunRecord | undefined> {
  try {
    const value = JSON.parse(await fs.promises.readFile(filePath, "utf8")) as Partial<ScheduledTaskRunRecord>;
    if (typeof value.runId !== "string" || typeof value.taskId !== "string") return undefined;
    return value as ScheduledTaskRunRecord;
  } catch {
    return undefined;
  }
}

function withTimeout<T>(
  promise: Promise<T>,
  ms: number,
  onTimeout: () => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      try {
        onTimeout();
      } catch {
        // The abort path is best-effort.
      }
      reject(new Error("SCHEDULED_TASK_TIMEOUT"));
    }, ms);
    promise.then(
      value => {
        clearTimeout(timer);
        resolve(value);
      },
      error => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}
