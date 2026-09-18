import * as path from "node:path";
import {
  SessionManager,
  type AgentSession,
  type AgentSessionEvent,
  type AgentSessionRuntime,
  type ExtensionUIContext,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import {
  askUserQuestionTool as defaultAskUserQuestionTool,
} from "./ask-user-question.js";
import {
  createDetachedAgentSessionRuntime,
  type CreateDetachedAgentSessionOptions,
} from "./detached-session.js";
import { createHeadlessUIContext } from "./headless-ui-context.js";

interface ViewerBinding {
  clientId: string;
  uiContext: ExtensionUIContext;
}

export interface DetachedSessionRegistryEvent {
  sessionPath: string;
  event: AgentSessionEvent;
}

export class DetachedSessionHandle {
  private runtime: AgentSessionRuntime | null = null;
  private initializingSession: Promise<AgentSession> | null = null;
  private disposePromise: Promise<void> | null = null;
  private disposed = false;
  private unsubscribeSession: (() => void) | null = null;
  private disposeDanoLlmResilience: (() => void) | null = null;
  private readonly listeners = new Set<(event: AgentSessionEvent) => void>();
  private readonly viewerBindings = new Map<string, ViewerBinding>();

  constructor(
    public sessionPath: string,
    private sessionManager: SessionManager,
    private readonly fallbackCwd: string,
    private readonly askUserQuestionTool: ToolDefinition,
    private readonly runtimeOptions: CreateDetachedAgentSessionOptions,
    private readonly onSessionEvent: (
      event: DetachedSessionRegistryEvent,
    ) => void,
  ) {}

  getSessionManager(): SessionManager {
    return this.runtime?.session.sessionManager ?? this.sessionManager;
  }

  getSession(): AgentSession | null {
    return this.runtime?.session ?? null;
  }

  getRuntime(): AgentSessionRuntime | null {
    return this.runtime;
  }

  isActive(): boolean {
    return this.runtime !== null;
  }

  subscribe(listener: (event: AgentSessionEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  async bindViewer(binding: ViewerBinding): Promise<void> {
    this.viewerBindings.delete(binding.clientId);
    this.viewerBindings.set(binding.clientId, binding);
    if (!this.runtime) return;
    await this.bindSessionExtensions(this.runtime.session, binding.uiContext);
  }

  async releaseViewer(clientId: string): Promise<void> {
    if (!this.viewerBindings.delete(clientId)) {
      return;
    }

    if (!this.runtime) return;
    await this.bindSessionExtensions(
      this.runtime.session,
      this.latestViewer()?.uiContext ?? createHeadlessUIContext(),
    );
  }

  async destroyViewer(clientId: string): Promise<void> {
    if (!this.viewerBindings.delete(clientId)) {
      return;
    }

    if (!this.runtime) return;
    const remainingViewer = this.latestViewer();
    if (remainingViewer) {
      await this.bindSessionExtensions(
        this.runtime.session,
        remainingViewer.uiContext,
      );
      return;
    }

    try {
      await this.runtime.session.abort();
    } catch (error) {
      console.error(
        `DetachedSessionHandle[${path.basename(this.sessionPath)}]: Failed to abort orphaned session:`,
        error,
      );
    }

    await this.bindSessionExtensions(
      this.runtime.session,
      this.latestViewer()?.uiContext ?? createHeadlessUIContext(),
    );
  }

  async ensureSession(): Promise<AgentSession> {
    if (this.disposed) {
      throw new Error("Session handle is disposed");
    }
    if (this.runtime) {
      return this.runtime.session;
    }
    if (this.initializingSession) {
      return this.initializingSession;
    }

    this.initializingSession = this.createSessionRuntime();
    try {
      return await this.initializingSession;
    } finally {
      this.initializingSession = null;
    }
  }

  private async createSessionRuntime(): Promise<AgentSession> {
    const created = await createDetachedAgentSessionRuntime(
      this.sessionManager.getCwd() || this.fallbackCwd,
      this.sessionManager,
      {
        ...this.runtimeOptions,
        askUserQuestionTool: this.askUserQuestionTool,
      },
    );
    await this.adoptRuntime(
      created.runtime,
      created.disposeDanoLlmResilience,
    );
    return created.runtime.session;
  }

  async adoptRuntime(
    runtime: AgentSessionRuntime,
    disposeDanoLlmResilience: () => void = () => {},
  ): Promise<void> {
    if (this.disposed) {
      disposeDanoLlmResilience();
      await runtime.dispose();
      throw new Error("Session handle is disposed");
    }
    if (this.runtime && this.runtime !== runtime) {
      throw new Error("Session handle already owns a Pi runtime");
    }
    runtime.setRebindSession(session => this.bindRuntimeSession(session));
    try {
      await this.bindRuntimeSession(runtime.session);
    } catch (error) {
      runtime.setRebindSession(undefined);
      disposeDanoLlmResilience();
      await runtime.dispose();
      throw error;
    }
    this.runtime = runtime;
    this.disposeDanoLlmResilience = disposeDanoLlmResilience;
  }

  dispose(): Promise<void> {
    if (this.disposePromise) {
      return this.disposePromise;
    }
    this.disposed = true;
    this.disposePromise = this.disposeOwnedRuntime();
    return this.disposePromise;
  }

  private async disposeOwnedRuntime(): Promise<void> {
    await this.initializingSession?.catch(() => {});
    this.unsubscribeSession?.();
    this.unsubscribeSession = null;
    this.disposeDanoLlmResilience?.();
    this.disposeDanoLlmResilience = null;
    await this.runtime?.dispose();
    this.runtime = null;
    this.listeners.clear();
    this.viewerBindings.clear();
  }

  private latestViewer(): ViewerBinding | undefined {
    return Array.from(this.viewerBindings.values()).at(-1);
  }

  private async bindSessionExtensions(
    session: AgentSession,
    uiContext: ExtensionUIContext,
  ): Promise<void> {
    await session.bindExtensions({
      uiContext,
      onError: error => {
        console.error(
          `DetachedSessionHandle[${path.basename(this.sessionPath)}]: Extension error:`,
          error,
        );
      },
      shutdownHandler: () => {},
    });
  }

  private async bindRuntimeSession(session: AgentSession): Promise<void> {
    await this.bindSessionExtensions(
      session,
      this.latestViewer()?.uiContext ?? createHeadlessUIContext(),
    );
    const unsubscribeSession = session.subscribe(event => {
      this.onSessionEvent({
        sessionPath: this.sessionPath,
        event,
      });
      for (const listener of this.listeners) {
        listener(event);
      }
    });
    this.unsubscribeSession?.();
    this.unsubscribeSession = unsubscribeSession;
    this.sessionManager = session.sessionManager;
    this.sessionPath = session.sessionFile ?? this.sessionPath;
  }
}

export class DetachedSessionRegistry {
  private readonly handles = new Map<string, DetachedSessionHandle>();
  private readonly listeners = new Set<
    (event: DetachedSessionRegistryEvent) => void
  >();
  private initialSessionPath: string | null = null;

  constructor(
    private readonly fallbackCwd: string,
    private readonly askUserQuestionTool: ToolDefinition =
      defaultAskUserQuestionTool,
    private readonly newSessionRuntimeOptions: CreateDetachedAgentSessionOptions = {},
  ) {}

  createSession(options?: {
    cwd?: string;
    sessionDir?: string;
  }): DetachedSessionHandle {
    const cwd = options?.cwd?.trim() || this.fallbackCwd;
    const sessionManager = SessionManager.create(cwd, options?.sessionDir);
    const sessionPath = sessionManager.getSessionFile();
    if (!sessionPath) {
      throw new Error("Selected session file not found");
    }

    const handle = new DetachedSessionHandle(
      sessionPath,
      sessionManager,
      this.fallbackCwd,
      this.askUserQuestionTool,
      this.runtimeOptionsFor(cwd),
      event => {
        this.emit(event);
      },
    );
    this.handles.set(sessionPath, handle);
    return handle;
  }

  hasSession(sessionPath: string): boolean {
    return this.handles.has(sessionPath);
  }

  getInitialSessionPath(): string | null {
    return this.initialSessionPath;
  }

  getReplacementSessionPath(excludedPath: string): string | null {
    if (
      this.initialSessionPath &&
      this.initialSessionPath !== excludedPath &&
      this.handles.has(this.initialSessionPath)
    ) {
      return this.initialSessionPath;
    }
    return (
      [...this.handles.keys()].find(sessionPath => sessionPath !== excludedPath) ??
      null
    );
  }

  async adoptRuntime(
    runtime: AgentSessionRuntime,
    disposeDanoLlmResilience: () => void = () => {},
  ): Promise<DetachedSessionHandle> {
    const sessionPath = runtime.session.sessionFile;
    if (!sessionPath) {
      throw new Error("Selected session file not found");
    }
    const existing = this.handles.get(sessionPath);
    if (existing) {
      await existing.adoptRuntime(runtime, disposeDanoLlmResilience);
      this.initialSessionPath ??= sessionPath;
      return existing;
    }
    const handle = new DetachedSessionHandle(
      sessionPath,
      runtime.session.sessionManager,
      this.fallbackCwd,
      this.askUserQuestionTool,
      this.runtimeOptionsFor(runtime.cwd),
      event => {
        this.emit(event);
      },
    );
    await handle.adoptRuntime(runtime, disposeDanoLlmResilience);
    this.handles.set(sessionPath, handle);
    this.initialSessionPath ??= sessionPath;
    return handle;
  }

  openSession(sessionPath: string): DetachedSessionHandle {
    const existing = this.handles.get(sessionPath);
    if (existing) {
      return existing;
    }

    const sessionManager = SessionManager.open(sessionPath);
    const handle = new DetachedSessionHandle(
      sessionPath,
      sessionManager,
      this.fallbackCwd,
      this.askUserQuestionTool,
      this.runtimeOptionsFor(sessionManager.getCwd() || this.fallbackCwd),
      event => {
        this.emit(event);
      },
    );
    this.handles.set(sessionPath, handle);
    return handle;
  }

  getHandle(sessionPath: string): DetachedSessionHandle | null {
    return this.handles.get(sessionPath) ?? null;
  }

  getCachedSessionManagers(): SessionManager[] {
    return [...this.handles.values()].map(handle => handle.getSessionManager());
  }

  getCachedSessionManager(sessionPath: string): SessionManager | null {
    return this.handles.get(sessionPath)?.getSessionManager() ?? null;
  }

  getActiveSession(sessionPath: string): AgentSession | null {
    return this.handles.get(sessionPath)?.getSession() ?? null;
  }

  isSessionActive(sessionPath: string): boolean {
    return this.handles.get(sessionPath)?.isActive() ?? false;
  }

  isSessionRunning(sessionPath: string): boolean {
    return this.handles.get(sessionPath)?.getSession()?.isStreaming ?? false;
  }

  subscribe(
    listener: (event: DetachedSessionRegistryEvent) => void,
  ): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  async bindViewer(sessionPath: string, binding: ViewerBinding): Promise<void> {
    const handle = this.openSession(sessionPath);
    await handle.bindViewer(binding);
  }

  async releaseViewer(sessionPath: string, clientId: string): Promise<void> {
    const handle = this.handles.get(sessionPath);
    if (!handle) return;
    await handle.releaseViewer(clientId);
  }

  async destroyViewer(sessionPath: string, clientId: string): Promise<void> {
    const handle = this.handles.get(sessionPath);
    if (!handle) return;
    await handle.destroyViewer(clientId);
  }

  async ensureSession(sessionPath: string): Promise<AgentSession> {
    return this.openSession(sessionPath).ensureSession();
  }

  async forkSession(
    sessionPath: string,
    entryId: string,
  ): Promise<{ sessionPath: string; selectedText?: string; cancelled: boolean }> {
    const sourceManager = SessionManager.open(sessionPath);
    const created = await createDetachedAgentSessionRuntime(
      sourceManager.getCwd() || this.fallbackCwd,
      sourceManager,
      {
        askUserQuestionTool: this.askUserQuestionTool,
        credentialBroker: this.newSessionRuntimeOptions.credentialBroker,
        credentialBrokerScope:
          this.newSessionRuntimeOptions.credentialBrokerScope,
        protectedTools: this.newSessionRuntimeOptions.protectedTools,
      },
    );
    const handle = new DetachedSessionHandle(
      sessionPath,
      sourceManager,
      this.fallbackCwd,
      this.askUserQuestionTool,
      this.runtimeOptionsFor(sourceManager.getCwd()),
      event => {
        this.emit(event);
      },
    );
    await handle.adoptRuntime(
      created.runtime,
      created.disposeDanoLlmResilience,
    );
    const runtime = handle.getRuntime();
    if (!runtime) {
      throw new Error("Selected session runtime not found");
    }

    let result: Awaited<ReturnType<AgentSessionRuntime["fork"]>>;
    try {
      result = await runtime.fork(entryId, { position: "at" });
    } catch (error) {
      await handle.dispose();
      throw error;
    }
    if (result.cancelled) {
      await handle.dispose();
      return {
        ...result,
        sessionPath,
      };
    }

    const nextPath = runtime.session.sessionFile ?? handle.sessionPath;
    if (nextPath === sessionPath) {
      await handle.dispose();
      throw new Error("Fork did not create a new session");
    }
    const existing = this.handles.get(nextPath);
    if (existing) {
      await handle.dispose();
      throw new Error("Forked session is already active");
    }
    this.handles.set(nextPath, handle);
    return {
      ...result,
      sessionPath: nextPath,
    };
  }

  async removeSession(sessionPath: string): Promise<void> {
    const handle = this.handles.get(sessionPath);
    if (handle) {
      this.handles.delete(sessionPath);
      if (this.initialSessionPath === sessionPath) {
        this.initialSessionPath = this.handles.keys().next().value ?? null;
      }
      await handle.dispose();
    }
  }

  async dispose(): Promise<void> {
    const handles = [...this.handles.values()];
    this.handles.clear();
    this.initialSessionPath = null;
    for (const handle of handles) {
      await handle.dispose();
    }
    this.listeners.clear();
  }

  private emit(event: DetachedSessionRegistryEvent): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }

  private runtimeOptionsFor(
    cwd: string | undefined,
  ): CreateDetachedAgentSessionOptions {
    const effectiveCwd = cwd?.trim() || this.fallbackCwd;
    if (path.resolve(effectiveCwd) === path.resolve(this.fallbackCwd)) {
      return this.newSessionRuntimeOptions;
    }
    return this.newSessionRuntimeOptions.credentialBroker || this.newSessionRuntimeOptions.protectedTools
      ? {
          credentialBroker: this.newSessionRuntimeOptions.credentialBroker,
          credentialBrokerScope:
            this.newSessionRuntimeOptions.credentialBrokerScope,
          protectedTools: this.newSessionRuntimeOptions.protectedTools,
        }
      : {};
  }
}
