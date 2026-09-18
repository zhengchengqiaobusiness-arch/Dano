import * as fs from "node:fs";
import * as path from "node:path";
import {
  createDanoBackend,
  type CreateDanoBackendOptions,
  type DanoBackend,
} from "../backend.js";
import type { UserContext } from "./user-context.js";
import { workspaceSessionDirectoryPath } from "./runtime-layout.js";
import { ensureSafeDirectory } from "./safe-directory.js";
import type { ProtectedSessionTools } from "./protected-session-tools.js";

export interface UserRuntimeContext {
  readonly userId: string;
  readonly backend: DanoBackend;
  readonly defaultWorkspacePath: string;
  readonly sessionsRootPath: string;
  ownsSessionPath(candidatePath: string): boolean;
  ownsWorkspacePath(candidatePath: string): boolean;
}

export type UserBackendFactory = (
  options: CreateDanoBackendOptions,
) => Promise<DanoBackend>;

export interface UserRuntimeRegistryOptions {
  readonly sessionsRootPath?: string;
  readonly protectedToolsForUser?: (context: UserContext) => Promise<ProtectedSessionTools>;
}

export interface UserOwnershipPathMap {
  readonly sourceUserId: string;
  readonly targetUserId: string;
  readonly sourceWorkspacePath: string;
  readonly targetWorkspacePath: string;
  mapUserPath(candidatePath: string): string;
}

export class UserRuntimeRegistry {
  private readonly contexts = new Map<
    string,
    Promise<UserRuntimeContext>
  >();
  private readonly failedCleanup: unknown[] = [];
  private closed = false;
  private closing?: Promise<void>;
  private readonly retired = new Set<string>();
  private readonly retirements = new Map<string, Promise<void>>();
  private readonly disposers = new WeakMap<UserRuntimeContext, () => Promise<void>>();
  private readonly ownerLocks = new Map<string, Promise<void>>();

  constructor(
    private readonly createBackend: UserBackendFactory = createDanoBackend,
    private readonly options: UserRuntimeRegistryOptions = {},
  ) {}

  get(userContext: UserContext): Promise<UserRuntimeContext> {
    if (this.closed || this.retired.has(userContext.user.id)) {
      return Promise.reject(new Error("USER_RUNTIME_CLOSED"));
    }
    const existing = this.contexts.get(userContext.user.id);
    if (existing) return existing;

    const creating = this.create(userContext).catch(error => {
      this.contexts.delete(userContext.user.id);
      throw error;
    });
    this.contexts.set(userContext.user.id, creating);
    return creating;
  }

  dispose(): Promise<void> {
    if (this.closing) return this.closing;
    this.closed = true;
    const contexts = [...this.contexts.values()];
    this.contexts.clear();
    this.closing = (async () => {
      const settled = await Promise.allSettled(contexts);
      await settleCleanup([
        ...settled.flatMap(result => result.status === "fulfilled"
          ? [this.disposers.get(result.value)!()] : []),
        ...this.retirements.values(),
        ...this.failedCleanup.map(error => Promise.reject(error)),
      ]);
    })();
    return this.closing;
  }

  async transferOwnership(
    source: UserContext,
    target: UserContext,
    options: {
      assertIdle(): void;
      commitOwnership(paths: UserOwnershipPathMap): Promise<void>;
    },
  ): Promise<void> {
    if (source.user.id === target.user.id) return;
    await this.withOwnerLocks([source.user.id, target.user.id], async () => {
      options.assertIdle();
      const sourceSessionsRoot = this.sessionsRootPath(source);
      const targetSessionsRoot = this.sessionsRootPath(target);
      const sourceWorkspacePath = path.join(
        source.folderPath,
        "workspaces",
        "default",
      );
      const targetWorkspacePath = path.join(
        target.folderPath,
        "workspaces",
        "default",
      );
      const journal = new FileTransferJournal();
      try {
        await mergeDirectory(source.folderPath, target.folderPath, journal, {
          skipTopLevelName:
            sourceSessionsRoot === path.join(source.folderPath, "sessions")
              ? "sessions"
              : undefined,
          sourceRootText: source.folderPath,
          targetRootText: target.folderPath,
          rewriteRootText: false,
        });
        await mergeSessionDirectory(
          sourceSessionsRoot,
          targetSessionsRoot,
          source.folderPath,
          target.folderPath,
          journal,
        );
        await options.commitOwnership({
          sourceUserId: source.user.id,
          targetUserId: target.user.id,
          sourceWorkspacePath,
          targetWorkspacePath,
          mapUserPath(candidatePath) {
            return replacePathRoot(
              candidatePath,
              source.folderPath,
              target.folderPath,
            );
          },
        });
      } catch (error) {
        await journal.rollback();
        throw error;
      }
      journal.commit();
    });
  }

  retireUser(userContext: UserContext): Promise<void> {
    const previous = this.retirements.get(userContext.user.id);
    if (previous) return previous;
    this.retired.add(userContext.user.id);
    const creating = this.contexts.get(userContext.user.id);
    this.contexts.delete(userContext.user.id);
    const retiring = (async () => {
      if (creating) {
        const context = await creating;
        await this.disposers.get(context)!();
      }
      await fs.promises.rm(userContext.folderPath, { recursive: true, force: true });
      const sessionsRootPath = this.sessionsRootPath(userContext);
      if (!isPathInsideRoot(userContext.folderPath, sessionsRootPath)) {
        await fs.promises.rm(sessionsRootPath, { recursive: true, force: true });
      }
    })();
    this.retirements.set(userContext.user.id, retiring);
    return retiring;
  }

  private async create(
    userContext: UserContext,
  ): Promise<UserRuntimeContext> {
    const workspaceRootPath = path.join(userContext.folderPath, "workspaces");
    const defaultWorkspacePath = path.join(workspaceRootPath, "default");
    const sessionsRootPath = this.sessionsRootPath(userContext);
    const unsafeRuntimeDirectory = () =>
      new Error("User runtime path is not a safe directory");
    await ensureSafeDirectory(workspaceRootPath, {
      recursive: true,
      unsafeDirectoryError: unsafeRuntimeDirectory,
    });
    await ensureSafeDirectory(defaultWorkspacePath, {
      unsafeDirectoryError: unsafeRuntimeDirectory,
    });
    if (this.options.sessionsRootPath) {
      await ensureSafeDirectory(path.resolve(this.options.sessionsRootPath), {
        recursive: true,
        unsafeDirectoryError: unsafeRuntimeDirectory,
      });
    }
    await ensureSafeDirectory(sessionsRootPath, {
      unsafeDirectoryError: unsafeRuntimeDirectory,
    });
    const protectedTools = await this.options.protectedToolsForUser?.(userContext);
    let backend: DanoBackend;
    try {
      backend = await this.createBackend({
        cwd: defaultWorkspacePath,
        credentialBrokerScope: userContext.user.id,
        protectedTools,
        sessionDir: workspaceSessionDirectoryPath(
          sessionsRootPath,
          defaultWorkspacePath,
        ),
      });
    } catch (error) {
      try { await protectedTools?.dispose?.(); }
      catch (cleanupError) {
        this.failedCleanup.push(cleanupError);
        throw new AggregateError([error, cleanupError], "USER_RUNTIME_INITIALIZATION_FAILED");
      }
      throw error;
    }
    const context: UserRuntimeContext = {
      userId: userContext.user.id,
      backend,
      defaultWorkspacePath,
      sessionsRootPath,
      ownsSessionPath: candidatePath =>
        isOwnedRuntimePath(sessionsRootPath, candidatePath),
      ownsWorkspacePath: candidatePath =>
        isOwnedRuntimePath(workspaceRootPath, candidatePath),
    };
    let disposing: Promise<void> | undefined;
    this.disposers.set(context, () => disposing ??= (async () => {
      const errors: unknown[] = [];
      try { await backend.dispose(); } catch (error) { errors.push(error); }
      try { await protectedTools?.dispose?.(); } catch (error) { errors.push(error); }
      if (errors.length) throw new AggregateError(errors, "USER_RUNTIME_DISPOSAL_FAILED");
    })());
    return context;
  }

  private sessionsRootPath(userContext: UserContext): string {
    return this.options.sessionsRootPath
      ? path.join(
          path.resolve(this.options.sessionsRootPath),
          path.basename(userContext.folderPath),
        )
      : path.join(userContext.folderPath, "sessions");
  }

  private async withOwnerLocks<T>(
    ownerIds: readonly string[],
    operation: () => Promise<T>,
  ): Promise<T> {
    const keys = [...new Set(ownerIds)].sort();
    const releases: Array<() => void> = [];
    for (const key of keys) {
      const previous = this.ownerLocks.get(key) ?? Promise.resolve();
      let release!: () => void;
      const current = new Promise<void>(resolve => {
        release = resolve;
      });
      const queued = previous.then(() => current);
      this.ownerLocks.set(key, queued);
      await previous;
      releases.push(() => {
        release();
        if (this.ownerLocks.get(key) === queued) this.ownerLocks.delete(key);
      });
    }
    try {
      return await operation();
    } finally {
      for (const release of releases.reverse()) release();
    }
  }
}

class FileTransferJournal {
  private readonly createdPaths: string[] = [];
  private readonly replacedFiles: Array<{
    path: string;
    data: Buffer;
    mode: number;
  }> = [];
  private committed = false;

  created(createdPath: string): void {
    this.createdPaths.push(createdPath);
  }

  replaced(filePath: string, data: Buffer, mode: number): void {
    this.replacedFiles.push({ path: filePath, data, mode });
  }

  commit(): void {
    this.committed = true;
  }

  async rollback(): Promise<void> {
    if (this.committed) return;
    for (const createdPath of this.createdPaths.reverse()) {
      await fs.promises.rm(createdPath, { recursive: true, force: true });
    }
    for (const replaced of this.replacedFiles.reverse()) {
      await fs.promises.writeFile(replaced.path, replaced.data, {
        mode: replaced.mode,
      });
    }
  }
}

async function mergeDirectory(
  sourceRoot: string,
  targetRoot: string,
  journal: FileTransferJournal,
  options: {
    skipTopLevelName?: string;
    sourceRootText: string;
    targetRootText: string;
    rewriteRootText: boolean;
  },
): Promise<void> {
  let entries: fs.Dirent[];
  try {
    entries = await fs.promises.readdir(sourceRoot, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  await fs.promises.mkdir(targetRoot, { recursive: true });
  for (const entry of entries) {
    if (entry.name === options.skipTopLevelName) continue;
    await mergeEntry(
      path.join(sourceRoot, entry.name),
      path.join(targetRoot, entry.name),
      journal,
      options,
    );
  }
}

async function mergeEntry(
  sourcePath: string,
  targetPath: string,
  journal: FileTransferJournal,
  options: {
    sourceRootText: string;
    targetRootText: string;
    rewriteRootText: boolean;
  },
): Promise<void> {
  const sourceStat = await fs.promises.lstat(sourcePath);
  if (sourceStat.isSymbolicLink()) {
    throw new Error("User data transfer does not follow symbolic links");
  }
  if (sourceStat.isDirectory()) {
    if (!fs.existsSync(targetPath)) {
      await fs.promises.mkdir(targetPath, { recursive: true });
      journal.created(targetPath);
    }
    const targetStat = await fs.promises.lstat(targetPath);
    if (!targetStat.isDirectory()) {
      targetPath = await availableTransferredPath(targetPath);
      await fs.promises.mkdir(targetPath, { recursive: true });
      journal.created(targetPath);
    }
    const entries = await fs.promises.readdir(sourcePath, {
      withFileTypes: true,
    });
    for (const entry of entries) {
      await mergeEntry(
        path.join(sourcePath, entry.name),
        path.join(targetPath, entry.name),
        journal,
        options,
      );
    }
    return;
  }
  if (!sourceStat.isFile()) {
    throw new Error("User data transfer accepts regular files only");
  }
  let destinationPath = targetPath;
  if (fs.existsSync(destinationPath)) {
    const [sourceData, targetData] = await Promise.all([
      fs.promises.readFile(sourcePath),
      fs.promises.readFile(destinationPath),
    ]);
    if (sourceData.equals(targetData)) return;
    if (
      await resolveTransferredFileConflict(
        sourcePath,
        destinationPath,
        sourceData,
        targetData,
        journal,
        options,
      )
    ) {
      return;
    }
    destinationPath = await availableTransferredPath(destinationPath);
  }
  const data = await fs.promises.readFile(sourcePath);
  const rewritten = options.rewriteRootText
    ? Buffer.from(
        data
          .toString("utf8")
          .replaceAll(options.sourceRootText, options.targetRootText),
        "utf8",
      )
    : data;
  await fs.promises.copyFile(sourcePath, destinationPath);
  if (!rewritten.equals(data)) {
    await fs.promises.writeFile(destinationPath, rewritten, {
      mode: sourceStat.mode,
    });
  }
  journal.created(destinationPath);
}

async function resolveTransferredFileConflict(
  sourcePath: string,
  targetPath: string,
  sourceData: Buffer,
  targetData: Buffer,
  journal: FileTransferJournal,
  roots: {
    sourceRootText: string;
    targetRootText: string;
  },
): Promise<boolean> {
  const sourceRelative = path.relative(roots.sourceRootText, sourcePath);
  const targetRelative = path.relative(roots.targetRootText, targetPath);
  if (sourceRelative !== targetRelative) return false;
  const segments = sourceRelative.split(path.sep);
  if (
    segments.length !== 2 ||
    segments[0] !== "preferences" ||
    path.extname(segments[1]!) !== ".json"
  ) {
    return false;
  }

  const source = parseJsonObject(sourceData);
  const target = parseJsonObject(targetData);
  if (!source || !target) return false;
  const merged = Buffer.from(
    `${JSON.stringify({ ...source, ...target })}\n`,
    "utf8",
  );
  if (merged.equals(targetData)) return true;
  const targetMode = (await fs.promises.stat(targetPath)).mode;
  journal.replaced(targetPath, targetData, targetMode);
  await fs.promises.writeFile(targetPath, merged, { mode: targetMode });
  return true;
}

function parseJsonObject(data: Buffer): Record<string, unknown> | null {
  try {
    const value = JSON.parse(data.toString("utf8")) as unknown;
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

async function mergeSessionDirectory(
  sourceRoot: string,
  targetRoot: string,
  sourceUserFolder: string,
  targetUserFolder: string,
  journal: FileTransferJournal,
): Promise<void> {
  let entries: fs.Dirent[];
  try {
    entries = await fs.promises.readdir(sourceRoot, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  await fs.promises.mkdir(targetRoot, { recursive: true });
  const sourceEncoded = sourceUserFolder
    .replace(/^[/\\]/, "")
    .replace(/[/\\:]/g, "-");
  const targetEncoded = targetUserFolder
    .replace(/^[/\\]/, "")
    .replace(/[/\\:]/g, "-");
  for (const entry of entries) {
    const targetName = entry.name.replace(sourceEncoded, targetEncoded);
    await mergeEntry(
      path.join(sourceRoot, entry.name),
      path.join(targetRoot, targetName),
      journal,
      {
        sourceRootText: sourceUserFolder,
        targetRootText: targetUserFolder,
        rewriteRootText: true,
      },
    );
  }
}

async function availableTransferredPath(candidatePath: string): Promise<string> {
  const extension = path.extname(candidatePath);
  const stem = extension ? candidatePath.slice(0, -extension.length) : candidatePath;
  for (let attempt = 1; ; attempt += 1) {
    const next = `${stem}.anonymous-${attempt}${extension}`;
    if (!fs.existsSync(next)) return next;
  }
}

function replacePathRoot(
  candidatePath: string,
  sourceRoot: string,
  targetRoot: string,
): string {
  const resolved = path.resolve(candidatePath);
  const relative = path.relative(path.resolve(sourceRoot), resolved);
  if (relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))) {
    return path.join(targetRoot, relative);
  }
  return resolved;
}

function isOwnedRuntimePath(rootPath: string, candidatePath: string): boolean {
  const resolvedRoot = path.resolve(rootPath);
  const resolvedCandidate = path.resolve(candidatePath);
  if (!isPathInsideRoot(resolvedRoot, resolvedCandidate)) return false;

  try {
    const realRoot = fs.realpathSync.native(resolvedRoot);
    let existingPath = resolvedCandidate;
    while (!fs.existsSync(existingPath) && existingPath !== resolvedRoot) {
      existingPath = path.dirname(existingPath);
    }
    return isPathInsideRoot(
      realRoot,
      fs.realpathSync.native(existingPath),
    );
  } catch {
    return false;
  }
}

function isPathInsideRoot(rootPath: string, candidatePath: string): boolean {
  return (
    candidatePath === rootPath ||
    candidatePath.startsWith(`${rootPath}${path.sep}`)
  );
}

async function settleCleanup(pending: Promise<void>[]): Promise<void> {
  const results = await Promise.allSettled(pending);
  const errors = results.flatMap(result => result.status === "rejected" ? [result.reason] : []);
  if (errors.length) throw new AggregateError(errors, "USER_RUNTIME_DISPOSAL_FAILED");
}
