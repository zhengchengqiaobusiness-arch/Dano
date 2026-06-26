import * as path from "node:path";
import {
  SessionManager,
  type AgentSession,
  type AgentSessionEvent,
  type ExtensionUIContext,
} from "@earendil-works/pi-coding-agent";
import { createDetachedAgentSession } from "./detached-session.js";
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
  private session: AgentSession | null = null;
  private unsubscribeSession: (() => void) | null = null;
  private readonly listeners = new Set<(event: AgentSessionEvent) => void>();
  private viewerBinding: ViewerBinding | null = null;

  constructor(
    public sessionPath: string,
    private sessionManager: SessionManager,
    private readonly fallbackCwd: string,
    private readonly onSessionEvent: (
      event: DetachedSessionRegistryEvent,
    ) => void,
  ) {}

  getSessionManager(): SessionManager {
    return this.sessionManager;
  }

  getSession(): AgentSession | null {
    return this.session;
  }

  isActive(): boolean {
    return this.session !== null;
  }

  subscribe(listener: (event: AgentSessionEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  async bindViewer(binding: ViewerBinding): Promise<void> {
    this.viewerBinding = binding;
    if (!this.session) return;
    await this.bindSessionExtensions(this.session, binding.uiContext);
  }

  async releaseViewer(clientId: string): Promise<void> {
    if (!this.viewerBinding || this.viewerBinding.clientId !== clientId) {
      return;
    }

    this.viewerBinding = null;
    if (!this.session) return;
    await this.bindSessionExtensions(this.session, createHeadlessUIContext());
  }

  async ensureSession(): Promise<AgentSession> {
    if (this.session) {
      return this.session;
    }

    const created = await createDetachedAgentSession(
      this.sessionManager.getCwd() || this.fallbackCwd,
      this.sessionManager,
    );

    const session = created.session;
    const nextSessionPath = session.sessionFile ?? this.sessionPath;
    this.sessionManager = session.sessionManager;

    await this.bindSessionExtensions(
      session,
      this.viewerBinding?.uiContext ?? createHeadlessUIContext(),
    );

    this.unsubscribeSession = session.subscribe(event => {
      this.onSessionEvent({
        sessionPath: this.sessionPath,
        event,
      });
      for (const listener of this.listeners) {
        listener(event);
      }
    });
    this.session = session;

    if (nextSessionPath !== this.sessionPath) {
      this.sessionPath = nextSessionPath;
    }

    return session;
  }

  dispose(): void {
    this.unsubscribeSession?.();
    this.unsubscribeSession = null;
    this.session?.dispose();
    this.session = null;
    this.listeners.clear();
    this.viewerBinding = null;
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
}

export class DetachedSessionRegistry {
  private readonly handles = new Map<string, DetachedSessionHandle>();
  private readonly listeners = new Set<
    (event: DetachedSessionRegistryEvent) => void
  >();

  constructor(private readonly fallbackCwd: string) {}

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

  async ensureSession(sessionPath: string): Promise<AgentSession> {
    return this.openSession(sessionPath).ensureSession();
  }

  removeSession(sessionPath: string): void {
    const handle = this.handles.get(sessionPath);
    if (handle) {
      handle.dispose();
      this.handles.delete(sessionPath);
    }
  }

  dispose(): void {
    for (const handle of this.handles.values()) {
      handle.dispose();
    }
    this.handles.clear();
    this.listeners.clear();
  }

  private emit(event: DetachedSessionRegistryEvent): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }
}
