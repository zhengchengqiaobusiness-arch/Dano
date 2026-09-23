/**
 * Bridge HTTP and SSE server.
 *
 * Handles:
 * - HTTP static file serving from config.staticDir
 * - HTTP command delivery from browser clients
 * - SSE fan-out for events, command responses, and extension UI requests
 * - Client tracking with monotonic sequence numbers
 */

import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { once } from "node:events";
import { InvalidMemoryCursor, projectMemoryOperation } from "./memory-operation-page.js";
import type { BridgeEventBus } from "./bridge-event-bus.js";
import { getLanIps, isTailscaleIp } from "./network.js";
import {
  UploadRegistry,
  type StoredUpload,
  type UploadAccess,
} from "./upload-registry.js";
import {
  toBrowserUserSummary,
  UserContextError,
  type AuthenticatedUserContext,
  type ClientUserResolution,
  type UserContext,
  type UserContextResolver,
} from "./user-context.js";
import type { BridgeAuthenticationState } from "../../types/protocol.js";
import type { UserRuntimeRegistry } from "./user-runtime-registry.js";
import { AnonymousUserCleanup } from "./anonymous-user-cleanup.js";
import type { AnonymousUserContextResolver } from "./anonymous-user-context.js";
import {
  parseThemeColorPreference,
  readThemeColorPreference,
  saveThemeColorPreference,
} from "./user-preferences.js";
import type {
  BridgeConfig,
  BridgeBrowserRuntimeConfig,
  BridgeEvent,
  ClientMessage,
  RpcUploadedFileRef,
  ServerMessage,
  BridgeClient,
} from "./types.js";

const MAX_JSON_BODY_BYTES = 1024 * 1024;
const MAX_UPLOAD_BODY_BYTES = 50 * 1024 * 1024;
class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export interface RpcConnectionHandler {
  readonly defaultWorkspacePath?: string;
  handleClientMessage(message: ClientMessage): void | Promise<void>;
  currentGitCwd?(): string;
  currentSessionId?(): string;
  isBusy?(): boolean;
  dispose(): void;
}

export interface RpcConnectionContext {
  client: BridgeClient;
  user?: UserContext;
  loginSessionId?: string;
  config: BridgeConfig;
  eventBus: BridgeEventBus;
  uploadRegistry: UploadRegistry;
  beginUserOperation: () => () => void;
  emitEvent: (event: BridgeEvent) => void;
  send: (message: ServerMessage) => void;
}

export type RpcConnectionHandlerFactory = (
  ctx: RpcConnectionContext,
) => RpcConnectionHandler | Promise<RpcConnectionHandler>;

export interface AuthHttpHandler {
  handle(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    url: URL,
    lifecycle: AuthHttpLifecycle,
  ): Promise<boolean>;
}

export interface AuthHttpLifecycle {
  resolveAnonymousUser(
    headers: http.IncomingHttpHeaders,
  ): Promise<UserContext | null>;
  transferAnonymousUser(
    headers: http.IncomingHttpHeaders,
    expectedUserId: string,
    target: AuthenticatedUserContext,
  ): Promise<void>;
  disconnectLoginSession(loginSessionId: string): void;
}

let clientSeqCounter = 0;

function generateClientId(): string {
  return `client_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

export class BridgeServer {
  private config: BridgeConfig;
  private handlerFactory: RpcConnectionHandlerFactory;
  private eventBus: BridgeEventBus;
  private emitEvent: (event: BridgeEvent) => void;

  private httpServer: http.Server | undefined;
  private handlers = new Map<string, RpcConnectionHandler>();
  private clients = new Map<string, BridgeClient>();
  private clientUsers = new Map<string, UserContext>();
  private clientAuthentication = new Map<string, BridgeAuthenticationState>();
  private clientLoginSessions = new Map<string, string>();
  private activeUserOperations = new Map<string, number>();
  private transferringUserIds = new Set<string>();
  private userActivityWaiters = new Set<() => void>();
  private sseStreams = new Map<string, Set<() => void>>();
  private uploadRegistry: UploadRegistry;
  private cleanupInterval: ReturnType<typeof setInterval> | undefined;
  private readonly anonymousUserCleanup?: AnonymousUserCleanup;
  private readonly serverInstanceId = randomUUID();
  private readonly serverStartTime = new Date().toISOString();

  private isRunning = false;
  private stopping = false;
  private host: string = "localhost";
  private port: number = 0;

  constructor(
    config: BridgeConfig,
    handlerFactory: RpcConnectionHandlerFactory,
    eventBus: BridgeEventBus,
    emitEvent: (event: BridgeEvent) => void,
    private readonly userContextResolver?: UserContextResolver,
    private readonly authHttpHandler?: AuthHttpHandler,
    private readonly userRuntimeRegistry?: UserRuntimeRegistry,
    private readonly anonymousUsers?: AnonymousUserContextResolver,
    anonymousUserCleanup?: { idleTtlMs: number; intervalMs: number },
  ) {
    const transferTimeout = config.userTransferTimeoutMs;
    if (transferTimeout !== undefined && (!Number.isSafeInteger(transferTimeout) || transferTimeout <= 0)) {
      throw new Error("userTransferTimeoutMs must be a positive integer");
    }
    this.config = config;
    this.handlerFactory = handlerFactory;
    this.eventBus = eventBus;
    this.emitEvent = emitEvent;
    this.uploadRegistry = new UploadRegistry({
      ...config.upload,
      requireOwnership: Boolean(userContextResolver),
    });
    if (anonymousUsers && anonymousUserCleanup) {
      this.anonymousUserCleanup = new AnonymousUserCleanup(anonymousUsers, {
        ...anonymousUserCleanup,
        beginCleanup: userId => this.beginAnonymousUserCleanup(userId),
        cleanupUser: userContext => this.cleanupAnonymousUser(userContext),
        onError: error => {
          console.warn("Dano Anonymous User cleanup failed:", error);
        },
      });
    }
  }

  async start(): Promise<{ host: string; port: number }> {
    if (this.isRunning) {
      throw new Error("Server is already running");
    }

    const startPort = this.config.port || 0;
    const maxPort = this.config.portMax || startPort;

    let boundPort = 0;
    let lastError: Error | undefined;

    await this.uploadRegistry.initialize();

    for (
      let tryPort = startPort;
      tryPort <= maxPort || (startPort === 0 && tryPort === 0);
    ) {
      try {
        await this.bindToPort(tryPort);
        boundPort =
          tryPort === 0
            ? ((this.httpServer?.address() as { port: number })?.port ?? 0)
            : tryPort;
        break;
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        if (startPort === 0) {
          throw lastError;
        }

        tryPort++;
        if (tryPort > maxPort) {
          throw new Error(
            `Failed to bind to any port in range ${startPort}-${maxPort}: ${lastError.message}`,
          );
        }
      }
    }

    this.startUploadCleanupInterval();
    await this.anonymousUserCleanup?.start();
    this.host = this.config.host;
    this.port = boundPort;
    this.stopping = false;
    this.isRunning = true;

    this.emitEvent({
      type: "server_start",
      host: this.host,
      port: this.port,
    });

    return { host: this.host, port: this.port };
  }

  private bindToPort(port: number): Promise<void> {
    this.httpServer = http.createServer((req, res) => {
      void this.handleRequest(req, res);
    });

    return new Promise((resolve, reject) => {
      const server = this.httpServer!;

      const onError = (err: Error) => {
        server.off("error", onError);
        server.off("listening", onListening);
        reject(err);
      };

      const onListening = () => {
        server.off("error", onError);
        server.off("listening", onListening);
        resolve();
      };

      server.once("error", onError);
      server.once("listening", onListening);
      server.listen(port, this.config.host);
    });
  }

  private startUploadCleanupInterval(): void {
    if (this.cleanupInterval) clearInterval(this.cleanupInterval);
    this.cleanupInterval = setInterval(() => {
      void this.uploadRegistry.cleanupExpiredUploads().catch(error => {
        console.warn("Dano upload cleanup failed:", error);
      });
    }, this.config.upload.cleanupIntervalMs);
    if (
      typeof this.cleanupInterval === "object" &&
      "unref" in this.cleanupInterval
    ) {
      this.cleanupInterval.unref();
    }
  }

  async stop(): Promise<void> {
    if (!this.isRunning) {
      return;
    }

    this.stopping = true;
    this.notifyUserActivity();
    for (const [clientId, handler] of this.handlers) {
      handler.dispose();
      this.eventBus.unregisterClient(clientId);
    }
    for (const clientId of [...this.sseStreams.keys()]) {
      this.closeSseStreams(clientId);
    }
    this.handlers.clear();
    this.clients.clear();
    this.clientUsers.clear();
    this.clientAuthentication.clear();
    this.clientLoginSessions.clear();

    if (this.httpServer) {
      await new Promise<void>((resolve, reject) => {
        this.httpServer?.close(err => {
          if (err) reject(err);
          else resolve();
        });
      });
      this.httpServer = undefined;
    }

    this.isRunning = false;
    this.port = 0;
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
      this.cleanupInterval = undefined;
    }
    this.anonymousUserCleanup?.dispose();
    await this.uploadRegistry.dispose();

    this.emitEvent({ type: "server_stop" });
  }

  getIsRunning(): boolean {
    return this.isRunning;
  }

  getAddress(): { host: string; port: number } | undefined {
    if (!this.isRunning) return undefined;
    return { host: this.host, port: this.port };
  }

  getClientCount(): number {
    return this.clients.size;
  }

  getClients(): BridgeClient[] {
    return Array.from(this.clients.values());
  }

  getClientUserResolution(clientId: string): ClientUserResolution | undefined {
    const userContext = this.clientUsers.get(clientId);
    const authentication = this.clientAuthentication.get(clientId);
    if (!userContext || !authentication) return undefined;
    const loginSessionId = this.clientLoginSessions.get(clientId);
    return {
      userContext,
      authentication,
      ...(loginSessionId ? { loginSessionId } : {}),
    };
  }

  requireReauthentication(loginSessionId: string): void {
    for (const [clientId, boundSessionId] of [
      ...this.clientLoginSessions,
    ]) {
      if (boundSessionId !== loginSessionId) continue;
      this.eventBus.sendToClient(clientId, {
        type: "authentication",
        payload: { status: "reauth_required" },
      });
      this.disconnectClient(clientId);
    }
  }

  private async handleRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
  ): Promise<void> {
    try {
      const url = new URL(req.url || "/", `http://${req.headers.host}`);
      const pathname = url.pathname;

      if (
        await this.authHttpHandler?.handle(req, res, url, {
          resolveAnonymousUser: headers =>
            this.userContextResolver?.resolveAnonymous?.(headers) ??
            Promise.resolve(null),
          transferAnonymousUser: (headers, expectedUserId, target) =>
            this.transferAnonymousUser(headers, expectedUserId, target),
          disconnectLoginSession: loginSessionId =>
            this.disconnectLoginSession(loginSessionId),
        })
      ) {
        return;
      }

      if (req.method === "GET" && pathname === "/api/health") {
        writeJson(res, 200, { status: "ok" });
        return;
      }

      if (req.method === "POST" && pathname === "/api/clients") {
        await readJsonBody(req);
        await this.createClient(req, res);
        return;
      }

      await this.authenticateProtectedRequest(req, url);

      if (req.method === "POST" && pathname === "/api/uploads") {
        await this.withUserWrite(url.searchParams.get("clientId"), () =>
          this.handleUploadRequest(req, res, url),
        );
        return;
      }

      if (req.method === "GET" && pathname === "/api/uploads/lookup") {
        await this.handleUploadLookupRequest(res, url);
        return;
      }

      if (req.method === "GET" && pathname === "/api/workspace-files/preview") {
        await this.handleWorkspaceFilePreview(res, url);
        return;
      }

      const uploadPreviewMatch = /^\/api\/uploads\/([^/]+)\/preview$/.exec(
        pathname,
      );
      if (req.method === "GET" && uploadPreviewMatch?.[1]) {
        await this.handleUploadPreview(
          res,
          decodeURIComponent(uploadPreviewMatch[1]),
          url,
        );
        return;
      }

      const uploadOrphanMatch = /^\/api\/uploads\/([^/]+)\/orphan$/.exec(
        pathname,
      );
      if (
        (req.method === "POST" || req.method === "DELETE") &&
        uploadOrphanMatch?.[1]
      ) {
        await readJsonBody(req);
        this.handleUploadOrphan(
          res,
          decodeURIComponent(uploadOrphanMatch[1]),
          url,
        );
        return;
      }

      const clientEventsMatch = /^\/api\/clients\/([^/]+)\/events$/.exec(
        pathname,
      );
      if (req.method === "GET" && clientEventsMatch?.[1]) {
        const clientId = decodeURIComponent(clientEventsMatch[1]);
        this.openEventStream(req, res, clientId);
        return;
      }

      const clientUserMatch = /^\/api\/clients\/([^/]+)\/user$/.exec(pathname);
      if (req.method === "GET" && clientUserMatch?.[1]) {
        await this.handleCurrentUser(
          res,
          decodeURIComponent(clientUserMatch[1]),
        );
        return;
      }

      const memoryContentMatch = /^\/api\/clients\/([^/]+)\/memory\/operations\/([^/]+)\/content\/([0-9]+)$/.exec(pathname);
      if (req.method === "GET" && memoryContentMatch) {
        const clientId = decodeURIComponent(memoryContentMatch[1]!);
        const user = this.clientUsers.get(clientId);
        if (!user || !("username" in user.user)) throw new UserContextError(401, "请先登录后再使用长期记忆");
        const operationId = decodeURIComponent(memoryContentMatch[2]!);
        const index = Number(memoryContentMatch[3]);
        if (!Number.isSafeInteger(index)) throw new HttpError(400, "记忆内容序号无效");
        await this.withUserWrite(clientId, async () => {
          let content;
          try {
            const runtime = await this.userRuntimeRegistry?.get(user);
            if (!runtime?.memory) throw new Error("MEMORY_UNAVAILABLE");
            content = await runtime.memory.content(operationId, index);
          } catch { throw new HttpError(503, "记忆内容暂时不可用，请刷新后重试"); }
          if (!content) throw new HttpError(404, "记忆内容不存在或尚未就绪");
          writeJson(res, 200, { operationId: content.operationId, index: content.index,
            total: content.total, text: content.text }, "no-store");
        });
        return;
      }

      const memoryOperationsMatch = /^\/api\/clients\/([^/]+)\/memory\/operations$/.exec(pathname);
      if (req.method === "GET" && memoryOperationsMatch) {
        const clientId = decodeURIComponent(memoryOperationsMatch[1]!);
        const user = this.clientUsers.get(clientId);
        if (!user || !("username" in user.user)) throw new UserContextError(401, "请先登录后再使用长期记忆");
        const cursor = url.searchParams.get("cursor") ?? undefined;
        await this.withUserWrite(clientId, async () => {
          try {
            const runtime = await this.userRuntimeRegistry?.get(user);
            if (!runtime?.memory) throw new Error("MEMORY_UNAVAILABLE");
            const page = await runtime.memory.operations(cursor);
            writeJson(res, 200, { items: page.items.map(projectMemoryOperation), nextCursor: page.nextCursor }, "no-store");
          } catch (error) {
            if (error instanceof InvalidMemoryCursor) throw new HttpError(400, "记忆列表分页位置无效，请刷新");
            throw new HttpError(503, "长期记忆暂时不可用，普通聊天可继续");
          }
        });
        return;
      }

      const memoryOperationMatch = /^\/api\/clients\/([^/]+)\/memory\/operations\/([^/]+)$/.exec(pathname);
      if (req.method === "GET" && memoryOperationMatch) {
        const clientId = decodeURIComponent(memoryOperationMatch[1]!);
        const operationId = decodeURIComponent(memoryOperationMatch[2]!);
        const user = this.clientUsers.get(clientId);
        if (!user || !("username" in user.user)) throw new UserContextError(401, "请先登录后再使用长期记忆");
        await this.withUserWrite(clientId, async () => {
          let operation;
          try {
            const runtime = await this.userRuntimeRegistry?.get(user);
            if (!runtime?.memory) throw new Error("MEMORY_UNAVAILABLE");
            operation = await runtime.memory.operation(operationId);
          } catch { throw new HttpError(503, "长期记忆暂时不可用，普通聊天可继续"); }
          if (!operation) throw new HttpError(404, "记忆投递记录不存在");
          writeJson(res, 200, projectMemoryOperation(operation), "no-store");
        });
        return;
      }

      const memorySettingsMatch = /^\/api\/clients\/([^/]+)\/memory\/settings$/.exec(pathname);
      if (memorySettingsMatch?.[1] && (req.method === "GET" || req.method === "PUT")) {
        const clientId = decodeURIComponent(memorySettingsMatch[1]);
        const user = this.clientUsers.get(clientId);
        if (!user || !("username" in user.user)) {
          throw new UserContextError(401, "请先登录后再使用长期记忆");
        }
        let enabled: boolean | undefined;
        let automaticCollection: boolean | undefined;
        let collectionPolicyVersion: string | undefined;
        if (req.method === "PUT") {
          if (req.headers["content-type"]?.split(";", 1)[0]?.trim().toLowerCase() !== "application/json") {
            throw new HttpError(415, "记忆设置需要 JSON 请求");
          }
          let body: unknown;
          try { body = await readJsonBody(req); }
          catch { throw new HttpError(400, "记忆设置无效"); }
          if (!body || typeof body !== "object" || Array.isArray(body)) {
            throw new HttpError(400, "记忆设置无效");
          }
          const settings = body as Record<string, unknown>;
          const keys = Object.keys(settings);
          if (keys.length === 1 && typeof settings.enabled === "boolean") {
            enabled = settings.enabled;
          } else if (typeof settings.automaticCollection === "boolean"
            && keys.every(key => key === "automaticCollection" || key === "collectionPolicyVersion")
            && (settings.automaticCollection
              ? typeof settings.collectionPolicyVersion === "string" && Boolean(settings.collectionPolicyVersion.trim())
                && settings.collectionPolicyVersion.length <= 16384
              : !Object.hasOwn(settings, "collectionPolicyVersion"))) {
            automaticCollection = settings.automaticCollection;
            collectionPolicyVersion = settings.collectionPolicyVersion as string | undefined;
          } else throw new HttpError(400, "记忆设置无效");
        }
        await this.withUserWrite(clientId, async () => {
          try {
            const runtime = await this.userRuntimeRegistry?.get(user);
            if (!runtime?.memory) throw new Error("MEMORY_UNAVAILABLE");
            if (enabled !== undefined) await runtime.memory.setEnabled(enabled);
            if (automaticCollection !== undefined) await runtime.memory.setAutomaticCollection(automaticCollection, collectionPolicyVersion);
            const status = await runtime.memory.status();
            writeJson(res, 200, { enabled: status.enabled, automaticCollection: status.automaticCollection,
              effectiveAt: status.effectiveAt, policyVersion: status.policyVersion, revision: status.revision,
              ...(status.collection ? { collection: {
                availablePolicyVersion: status.collection.availablePolicyVersion,
                consent: status.collection.consent ? {
                  policyVersion: status.collection.consent.policyVersion, scope: status.collection.consent.scope,
                  effectiveAt: status.collection.consent.effectiveAt, revision: status.collection.consent.revision,
                } : null,
              } } : {}) }, "no-store");
          } catch { throw new HttpError(503, "长期记忆暂时不可用，普通聊天可继续"); }
        });
        return;
      }

      const memoryGovernanceMatch = /^\/api\/clients\/([^/]+)\/memory\/governance(?:\/([a-f0-9-]{36})(?:\/review)?)?$/.exec(pathname);
      const memoryExportMatch = /^\/api\/clients\/([^/]+)\/memory\/export$/.exec(pathname);
      if (memoryGovernanceMatch || memoryExportMatch) {
        const clientId = decodeURIComponent((memoryGovernanceMatch ?? memoryExportMatch)![1]!);
        const user = this.clientUsers.get(clientId);
        if (!user || !("username" in user.user)) throw new UserContextError(401, "请先登录后再管理长期记忆");
        if (req.method === "POST" && req.headers["content-type"]?.split(";", 1)[0]?.trim().toLowerCase() !== "application/json") {
          throw new HttpError(415, "记忆管理需要 JSON 请求");
        }
        let body: Record<string, unknown> | undefined;
        if (req.method === "POST") {
          let input: unknown;
          try { input = await readJsonBody(req); }
          catch { throw new HttpError(400, "记忆管理请求无效"); }
          if (!input || typeof input !== "object" || Array.isArray(input)) throw new HttpError(400, "记忆管理请求无效");
          body = input as Record<string, unknown>;
        }
        await this.withUserWrite(clientId, async () => {
          const runtime = await this.userRuntimeRegistry?.get(user);
          if (!runtime?.memory) throw new HttpError(503, "长期记忆暂时不可用");
          const governance = runtime.memory.governance();
          try {
            if (memoryExportMatch && req.method === "GET") {
              const limit = Number(url.searchParams.get("limit") ?? "10");
              if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new HttpError(400, "导出分页大小无效");
              const page = await governance.exportPage(limit, url.searchParams.get("cursor") ?? undefined, 1024 * 1024);
              writeJson(res, 200, page, "no-store"); return;
            }
            const jobId = memoryGovernanceMatch?.[2];
            if (jobId && pathname.endsWith("/review") && req.method === "GET") {
              writeJson(res, 200, await governance.review(jobId), "no-store"); return;
            }
            if (jobId && pathname.endsWith("/review") && req.method === "POST") {
              const operationId = body?.operationId;
              if (typeof operationId !== "string" || !/^[a-f0-9-]{36}$/.test(operationId)) throw new HttpError(400, "复核对象无效");
              let receipt;
              if (Object.keys(body!).length === 2 && (body?.decision === "target" || body?.decision === "unrelated")) {
                receipt = await governance.reviewWriter(jobId, operationId, body.decision);
              } else if (Object.keys(body!).length === 2 && typeof body?.exactText === "string") {
                receipt = await governance.resolveMergedWriter(jobId, operationId, body.exactText);
              } else throw new HttpError(400, "复核决定无效");
              runtime.memory.wakeGovernance();
              writeJson(res, 200, receipt, "no-store"); return;
            }
            if (jobId && req.method === "GET") {
              writeJson(res, 200, await governance.status(jobId), "no-store"); return;
            }
            if (!jobId && req.method === "GET") {
              writeJson(res, 200, { pending: await governance.pending() ?? null }, "no-store"); return;
            }
            if (!jobId && req.method === "POST") {
              const action = body?.action;
              let receipt;
              if (action === "clear" && Object.keys(body!).length === 2 && body?.confirmed === true) {
                receipt = await governance.clear();
              } else if ((action === "forget" || action === "correct")
                && typeof body?.memoryUri === "string" && typeof body?.selectedText === "string"
                && Object.keys(body!).length === (action === "correct" ? 4 : 3)) {
                receipt = action === "forget"
                  ? await governance.forget(body.memoryUri, body.selectedText)
                  : typeof body.replacementText === "string"
                    ? await governance.correct(body.memoryUri, body.selectedText, body.replacementText)
                    : undefined;
                if (!receipt) throw new HttpError(400, "纠正文本无效");
              } else throw new HttpError(400, "记忆管理请求无效");
              runtime.memory.wakeGovernance();
              writeJson(res, 200, receipt, "no-store"); return;
            }
            throw new HttpError(405, "不支持的记忆管理操作");
          } catch (error) {
            if (error instanceof HttpError) throw error;
            const code = error instanceof Error ? error.message : "";
            if (["MEMORY_TARGET_NOT_FOUND", "MEMORY_TARGET_AMBIGUOUS", "MEMORY_GOVERNANCE_TARGET_MISMATCH",
              "INVALID_MEMORY_GOVERNANCE", "INVALID_MEMORY_EXPORT_CURSOR"].includes(code)) {
              throw new HttpError(400, code);
            }
            if (["MEMORY_GOVERNANCE_PENDING", "MEMORY_DISABLED", "MEMORY_EXPORT_CHANGED"].includes(code)) {
              throw new HttpError(409, code);
            }
            throw new HttpError(503, "长期记忆暂时不可用，请稍后重试");
          }
        });
        return;
      }

      const clientThemePreferenceMatch =
        /^\/api\/clients\/([^/]+)\/preferences\/theme$/.exec(pathname);
      if (
        (req.method === "GET" || req.method === "PUT") &&
        clientThemePreferenceMatch?.[1]
      ) {
        const clientId = decodeURIComponent(clientThemePreferenceMatch[1]);
        const userContext = this.clientUsers.get(clientId);
        if (!userContext) {
          throw new UserContextError(401, "Authenticated User is required");
        }
        if (req.method === "GET") {
          writeJson(res, 200, await readThemeColorPreference(userContext));
          return;
        }
        const body = await readJsonBody(req);
        const preference = parseThemeColorPreference(body);
        if (!preference) {
          throw new HttpError(400, "Accent Color Preset is invalid");
        }
        await this.withUserWrite(clientId, async () => {
          try {
            await saveThemeColorPreference(userContext, preference);
          } catch {
            throw new HttpError(500, "Theme Color preference could not be saved");
          }
        });
        writeJson(res, 200, preference);
        return;
      }

      const clientMessagesMatch = /^\/api\/clients\/([^/]+)\/messages$/.exec(
        pathname,
      );
      if (req.method === "POST" && clientMessagesMatch?.[1]) {
        const clientId = decodeURIComponent(clientMessagesMatch[1]);
        const body = await readJsonBody(req);
        this.handleClientMessage(
          res,
          clientId,
          body,
        );
        return;
      }

      const clientDisconnectMatch = /^\/api\/clients\/([^/]+)\/disconnect$/.exec(
        pathname,
      );
      if (
        (req.method === "POST" || req.method === "DELETE") &&
        clientDisconnectMatch?.[1]
      ) {
        const clientId = decodeURIComponent(clientDisconnectMatch[1]);
        await readJsonBody(req);
        this.disconnectClient(clientId);
        writeJson(res, 202, { status: "disconnected" });
        return;
      }

      if (pathname.startsWith("/api/")) {
        writeJson(res, 404, { error: "API route was not found" });
        return;
      }

      this.handleStaticRequest(req, res);
    } catch (error) {
      if (error instanceof UserContextError) {
        writeJson(res, error.status, { error: error.message });
        return;
      }
      if (error instanceof HttpError) {
        writeJson(res, error.status, { error: error.message });
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      writeJson(res, 500, { error: message || "Internal Server Error" });
    }
  }

  private async handleUploadRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    url: URL,
  ): Promise<void> {
    const mimeType = normalizeUploadMimeType(
      url.searchParams.get("mimeType") ?? req.headers["content-type"],
    );
    const rawHash = url.searchParams.get("sha256") ?? url.searchParams.get("hash");
    const declaredHash = normalizeSha256(rawHash);
    if (rawHash !== null && !declaredHash) {
      throw new HttpError(400, "Upload sha256 is invalid");
    }

    const name = normalizeUploadName(url.searchParams.get("name"));
    if (!name) throw new HttpError(400, "Upload name is required");

    const contentLength = Number(req.headers["content-length"] ?? NaN);
    if (Number.isFinite(contentLength) && contentLength > MAX_UPLOAD_BODY_BYTES) {
      throw new HttpError(413, "Upload is too large");
    }

    const ownerClientId = url.searchParams.get("clientId");
    if (!ownerClientId || !this.clients.has(ownerClientId)) {
      throw new HttpError(400, "Valid clientId is required");
    }
    const workspacePath = this.getClientWorkspacePath(ownerClientId);
    let uploadPath: { id: string; partPath: string };
    let storagePath: { id: string; filePath: string; relativePath: string } | null;
    if (declaredHash) {
      const pathInfo = await this.uploadRegistry.createFilePath(
        workspacePath,
        declaredHash,
        name,
      );
      uploadPath = { id: pathInfo.id, partPath: pathInfo.partPath };
      storagePath = {
        id: pathInfo.id,
        filePath: pathInfo.filePath,
        relativePath: pathInfo.relativePath,
      };
    } else {
      uploadPath = await this.uploadRegistry.createIncomingPartPath(workspacePath);
      storagePath = null;
    }
    await assertWorkspaceWriteParent(uploadPath.partPath, workspacePath);
    if (storagePath) {
      await assertWorkspaceWriteParent(storagePath.filePath, workspacePath);
    }
    const finalFileExists =
      storagePath !== null && fs.existsSync(storagePath.filePath);
    if (
      Number.isFinite(contentLength) &&
      !finalFileExists &&
      !(await this.uploadRegistry.cleanupBeforeUpload(contentLength, workspacePath))
    ) {
      throw new HttpError(413, "Upload storage limit exceeded");
    }

    const { size, hash } = await writeUploadBody(
      req,
      uploadPath.partPath,
      MAX_UPLOAD_BODY_BYTES,
    );
    if (declaredHash && hash !== declaredHash) {
      await fs.promises.rm(uploadPath.partPath, { force: true });
      throw new HttpError(400, "Upload sha256 mismatch");
    }
    storagePath ??= await this.uploadRegistry.createFilePath(workspacePath, hash, name);
    await assertWorkspaceWriteParent(storagePath.filePath, workspacePath);
    if (!fs.existsSync(storagePath.filePath)) {
      if (!(await this.uploadRegistry.cleanupBeforeUpload(size, workspacePath))) {
        await fs.promises.rm(uploadPath.partPath, { force: true });
        throw new HttpError(413, "Upload storage limit exceeded");
      }
      await fs.promises.rename(uploadPath.partPath, storagePath.filePath);
    } else {
      await fs.promises.rm(uploadPath.partPath, { force: true });
    }
    const ref: RpcUploadedFileRef = {
      id: storagePath.id,
      name,
      size,
      mimeType,
      path: storagePath.filePath,
      relativePath: storagePath.relativePath,
      previewUrl: uploadPreviewUrl(
        storagePath.id,
        this.clientUsers.has(ownerClientId) ? ownerClientId : undefined,
      ),
    };
    this.uploadRegistry.register(ref, this.getClientUploadAccess(ownerClientId));
    writeJson(res, 201, ref);
  }

  private async handleUploadLookupRequest(
    res: http.ServerResponse,
    url: URL,
  ): Promise<void> {
    const mimeType = normalizeUploadMimeType(url.searchParams.get("mimeType"));
    const declaredHash = normalizeSha256(url.searchParams.get("sha256"));
    if (!declaredHash) throw new HttpError(400, "Upload sha256 is required");

    const name = normalizeUploadName(url.searchParams.get("name"));
    if (!name) throw new HttpError(400, "Upload name is required");

    const ownerClientId = url.searchParams.get("clientId");
    if (!ownerClientId || !this.clients.has(ownerClientId)) {
      throw new HttpError(400, "Valid clientId is required");
    }
    const workspacePath = this.getClientWorkspacePath(ownerClientId);
    const { id, filePath, relativePath } =
      await this.uploadRegistry.createFilePath(workspacePath, declaredHash, name);
    await assertWorkspaceWriteParent(filePath, workspacePath);
    if (!fs.existsSync(filePath)) {
      writeJson(res, 404, { error: "Upload was not found" });
      return;
    }
    const stats = await fs.promises.stat(filePath);
    const ref: RpcUploadedFileRef = {
      id,
      name,
      size: stats.size,
      mimeType,
      path: filePath,
      relativePath,
      previewUrl: uploadPreviewUrl(
        id,
        this.clientUsers.has(ownerClientId) ? ownerClientId : undefined,
      ),
    };
    this.uploadRegistry.register(ref, this.getClientUploadAccess(ownerClientId));
    writeJson(res, 200, ref);
  }

  private getClientWorkspacePath(clientId: string): string {
    const handler = this.handlers.get(clientId);
    const workspacePath = handler?.currentGitCwd?.().trim();
    if (!workspacePath) {
      throw new HttpError(409, "Client workspace is not ready");
    }
    return workspacePath;
  }

  private async handleUploadPreview(
    res: http.ServerResponse,
    id: string,
    url: URL,
  ): Promise<void> {
    const stored = this.uploadRegistry.peek(id);
    const clientId = url.searchParams.get("clientId");
    if (stored?.ownerUserId && !clientId) {
      writeJson(res, 403, { error: "Upload requires its bound Client" });
      return;
    }
    const ref = this.uploadRegistry.touch(
      id,
      clientId ? this.getClientUploadAccess(clientId) : undefined,
    );
    if (!ref) {
      writeJson(res, stored ? 403 : 404, {
        error: stored
          ? "Upload does not belong to this Client"
          : "Upload was not found",
      });
      return;
    }

    const filePath = path.resolve(ref.path);
    fs.stat(filePath, (statErr, stats) => {
      if (statErr || !stats.isFile()) {
        writeJson(res, 404, { error: "Upload was not found" });
        return;
      }

      res.writeHead(200, {
        "Content-Type": ref.mimeType,
        "Content-Length": stats.size,
        "Cache-Control": "no-cache",
      });
      fs.createReadStream(filePath).pipe(res);
    });
  }

  private async handleWorkspaceFilePreview(
    res: http.ServerResponse,
    url: URL,
  ): Promise<void> {
    const clientId = url.searchParams.get("clientId");
    const requestedPath = url.searchParams.get("path")?.trim();
    if (!clientId || !this.clients.has(clientId)) {
      writeJson(res, 400, { error: "Valid clientId is required" });
      return;
    }
    if (!requestedPath) {
      writeJson(res, 400, { error: "File path is required" });
      return;
    }

    const workspacePath = path.resolve(this.getClientWorkspacePath(clientId));
    const filePath = path.resolve(workspacePath, requestedPath);
    if (filePath !== workspacePath && !filePath.startsWith(workspacePath + path.sep)) {
      writeJson(res, 403, { error: "File path is outside the workspace" });
      return;
    }

    let realFilePath: string;
    try {
      realFilePath = await realPathInsideWorkspace(filePath, workspacePath);
    } catch (error) {
      if (error instanceof HttpError) throw error;
      writeJson(res, 404, { error: "File was not found" });
      return;
    }

    try {
      const stats = await fs.promises.stat(realFilePath);
      if (!stats.isFile()) {
        writeJson(res, 404, { error: "File was not found" });
        return;
      }

      res.writeHead(200, {
        "Content-Type": workspacePreviewMimeType(realFilePath),
        "Content-Length": stats.size,
        "Cache-Control": "no-cache",
      });
      fs.createReadStream(realFilePath).pipe(res);
    } catch {
      writeJson(res, 404, { error: "File was not found" });
    }
  }

  private handleUploadOrphan(
    res: http.ServerResponse,
    id: string,
    url: URL,
  ): void {
    const clientId = url.searchParams.get("clientId");
    if (!clientId) {
      writeJson(res, 403, { error: "Upload does not belong to this client" });
      return;
    }
    const access = this.getClientUploadAccess(clientId);
    const stored = this.uploadRegistry.peek(id);
    if (
      stored?.ownerClientId !== clientId &&
      stored?.ownerClientId &&
      !this.clients.has(stored.ownerClientId)
    ) {
      this.uploadRegistry.resolve(stored, access);
    }
    const upload = this.uploadRegistry.touch(id, access);
    if (!upload) {
      writeJson(res, 404, { error: "Upload was not found" });
      return;
    }
    if (upload.ownerClientId !== clientId) {
      writeJson(res, 403, { error: "Upload does not belong to this client" });
      return;
    }
    if (upload.state === "reading") {
      writeJson(res, 409, { error: "Upload is currently being read" });
      return;
    }
    this.uploadRegistry.markOrphaned(id, access);
    writeJson(res, 202, { status: "orphaned" });
  }

  private async createClient(
    req: http.IncomingMessage,
    res: http.ServerResponse,
  ): Promise<void> {
    const resolution = this.userContextResolver?.resolveForClient
      ? await this.userContextResolver.resolveForClient(req.headers)
      : undefined;
    const user =
      resolution?.userContext ??
      (await this.userContextResolver?.resolve(req.headers));
    if (this.userContextResolver && !user) {
      throw new UserContextError(401, "Authenticated User is required");
    }
    const finishActivity = user
      ? this.beginUserOperationForUser(user.user.id)
      : () => {};
    try {
      if (user && !("username" in user.user)) {
        const touched = await this.anonymousUsers?.touchAnonymousUser(
          user.user.id,
        );
        if (this.anonymousUsers && !touched) {
          throw new UserContextError(401, "Anonymous User is no longer valid");
        }
      }
      clientSeqCounter++;
      const client: BridgeClient = {
        id: generateClientId(),
        seq: clientSeqCounter,
        connectedAt: new Date().toISOString(),
      };

      this.clients.set(client.id, client);
      if (user) this.clientUsers.set(client.id, user);
      const authentication =
        resolution?.authentication ??
        (user ? browserAuthentication(user) : undefined);
      if (authentication) {
        this.clientAuthentication.set(client.id, authentication);
      }
      if (resolution?.loginSessionId) {
        this.clientLoginSessions.set(client.id, resolution.loginSessionId);
      }
      const unregisterClient = this.eventBus.registerClient(client);
      let handler: RpcConnectionHandler;
      try {
        handler = await this.handlerFactory({
          client,
          user: user ?? undefined,
          loginSessionId: resolution?.loginSessionId,
          config: this.config,
          eventBus: this.eventBus,
          uploadRegistry: this.uploadRegistry,
          beginUserOperation: () =>
            user ? this.beginUserOperationForUser(user.user.id) : () => {},
          emitEvent: this.emitEvent,
          send: message => {
            this.eventBus.sendToClient(client.id, message);
          },
        });
      } catch (error) {
        unregisterClient();
        this.clientUsers.delete(client.id);
        this.clientAuthentication.delete(client.id);
        this.clientLoginSessions.delete(client.id);
        this.clients.delete(client.id);
        throw error;
      }
      this.handlers.set(client.id, handler);

      this.emitEvent({
        type: "client_connect",
        client,
      });

      if (resolution?.setCookie) {
        res.setHeader("Set-Cookie", resolution.setCookie);
      }
      writeJson(res, 201, {
        client,
        ...(authentication ? { authentication } : {}),
        ...(authentication?.status === "authenticated"
          ? { currentUser: authentication.user }
          : {}),
        eventsUrl: `/api/clients/${encodeURIComponent(client.id)}/events`,
        messagesUrl: `/api/clients/${encodeURIComponent(client.id)}/messages`,
        defaultWorkspacePath:
          handler.defaultWorkspacePath ?? this.config.defaultWorkspacePath,
      });
    } finally {
      finishActivity();
    }
  }

  private async handleCurrentUser(
    res: http.ServerResponse,
    clientId: string,
  ): Promise<void> {
    const authentication = this.clientAuthentication.get(clientId);
    if (!authentication) {
      throw new UserContextError(401, "Authenticated User is required");
    }
    writeJson(
      res,
      200,
      authentication.status === "authenticated"
        ? authentication.user
        : authentication,
    );
  }

  private async authenticateProtectedRequest(
    req: http.IncomingMessage,
    url: URL,
  ): Promise<void> {
    const clientRoute = /^\/api\/clients\/([^/]+)(?:\/|$)/.exec(url.pathname);
    let clientId = clientRoute?.[1]
      ? decodeURIComponent(clientRoute[1])
      : undefined;
    const previewMatch = /^\/api\/uploads\/([^/]+)\/preview$/.exec(url.pathname);

    if (!clientId && previewMatch?.[1]) {
      const upload = this.uploadRegistry.peek(decodeURIComponent(previewMatch[1]));
      const requestedClientId = url.searchParams.get("clientId") ?? undefined;
      if (
        upload?.ownerUserId &&
        (!requestedClientId || !this.clients.has(requestedClientId))
      ) {
        clientId =
          requestedClientId &&
          upload.previousClientIds.includes(requestedClientId) &&
          upload.ownerClientId &&
          this.clients.has(upload.ownerClientId)
            ? upload.ownerClientId
            : await this.recoverUploadClient(req, upload);
        if (clientId) url.searchParams.set("clientId", clientId);
      } else {
        clientId = requestedClientId ?? upload?.ownerClientId;
      }
    }

    if (
      !clientId &&
      !previewMatch &&
      (url.pathname.startsWith("/api/uploads") ||
        url.pathname.startsWith("/api/workspace-files"))
    ) {
      clientId = url.searchParams.get("clientId") ?? undefined;
    }

    if (clientId) await this.authenticateBoundClientRequest(req, clientId);
  }

  private async authenticateBoundClientRequest(
    req: http.IncomingMessage,
    clientId: string,
  ): Promise<UserContext | undefined> {
    if (!this.clients.has(clientId)) {
      throw new HttpError(404, "Client was not found");
    }
    const boundUser = this.clientUsers.get(clientId);
    if (!boundUser) return undefined;
    if (this.transferringUserIds.has(boundUser.user.id)) {
      throw new HttpError(409, "User runtime ownership is changing");
    }
    if (!this.userContextResolver) {
      throw new UserContextError(503, "User authentication is unavailable");
    }
    const resolution = this.userContextResolver.resolveExisting
      ? await this.userContextResolver.resolveExisting(req.headers)
      : null;
    const requestUser =
      resolution?.userContext ??
      (await this.userContextResolver.resolve(req.headers));
    if (!requestUser) {
      throw new UserContextError(401, "Authenticated User is required");
    }
    if (requestUser.user.id !== boundUser.user.id) {
      throw new UserContextError(403, "Client belongs to another User");
    }
    const boundLoginSessionId = this.clientLoginSessions.get(clientId);
    if (
      boundLoginSessionId &&
      resolution?.loginSessionId !== boundLoginSessionId
    ) {
      throw new UserContextError(401, "Dano Login Session is no longer valid");
    }
    if (!("username" in boundUser.user)) {
      const touched = await this.anonymousUsers?.touchAnonymousUser(
        boundUser.user.id,
      );
      if (this.anonymousUsers && !touched) {
        throw new UserContextError(401, "Anonymous User is no longer valid");
      }
    }
    return boundUser;
  }

  private getClientUploadAccess(clientId: string): UploadAccess {
    return {
      ownerUserId: this.clientUsers.get(clientId)?.user.id,
      ownerClientId: clientId,
      workspacePath: this.getClientWorkspacePath(clientId),
      sessionId: this.handlers.get(clientId)?.currentSessionId?.(),
    };
  }

  private async recoverUploadClient(
    req: http.IncomingMessage,
    upload: StoredUpload,
  ): Promise<string | undefined> {
    if (
      !upload ||
      !upload.ownerUserId ||
      !upload.sessionId ||
      (upload.ownerClientId && this.clients.has(upload.ownerClientId)) ||
      !this.userContextResolver
    ) {
      return undefined;
    }
    const requestUser = await this.userContextResolver.resolve(req.headers);
    if (!requestUser) {
      throw new UserContextError(401, "Authenticated User is required");
    }
    if (requestUser.user.id !== upload.ownerUserId) {
      throw new UserContextError(403, "Upload belongs to another User");
    }

    for (const [clientId, clientUser] of this.clientUsers) {
      if (clientUser.user.id !== upload.ownerUserId) continue;
      let access: UploadAccess;
      try {
        access = this.getClientUploadAccess(clientId);
      } catch {
        continue;
      }
      if (this.uploadRegistry.resolve(upload, access)) return clientId;
    }
    return undefined;
  }

  private openEventStream(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    clientId: string,
  ): void {
    if (!this.clients.has(clientId)) {
      writeJson(res, 404, { error: "Client was not found" });
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    res.flushHeaders?.();

    this.closeSseStreams(clientId);

    const send = (message: ServerMessage) => {
      res.write(formatSseMessage(message));
    };
    const unregister = this.eventBus.connectClient(clientId, send);
    let closed = false;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let removeStream = () => {};
    const closeStream = () => {
      if (closed) return;
      closed = true;
      if (heartbeat) clearInterval(heartbeat);
      unregister();
      removeStream();
      res.end();
    };
    removeStream = this.registerSseStream(clientId, closeStream);
    const writeHeartbeat = () => {
      if (!this.isClientRoutable(clientId)) {
        closeStream();
        return;
      }
      res.write(
        formatSseMessage({
          type: "event",
          payload: {
            type: "heartbeat",
            serverInstanceId: this.serverInstanceId,
            serverStartTime: this.serverStartTime,
          },
        }),
      );
    };
    heartbeat = setInterval(writeHeartbeat, this.config.heartbeatInterval);

    req.on("close", () => {
      closeStream();
    });
  }

  private handleClientMessage(
    res: http.ServerResponse,
    clientId: string,
    body: unknown,
  ): void {
    const handler = this.handlers.get(clientId);
    if (!handler) {
      writeJson(res, 404, { error: "Client was not found" });
      return;
    }

    if (!isClientMessage(body)) {
      writeJson(res, 400, { error: "Request body must be a client message" });
      return;
    }

    if (!this.eventBus.hasActiveClientConnection(clientId)) {
      writeJson(res, 409, { error: "RECONNECT_REQUIRED" });
      return;
    }

    const finishOperation = this.beginUserOperation(clientId);
    try {
      const completion = handler.handleClientMessage(body);
      if (completion) {
        void completion
          .catch(error => {
            console.error("Bridge client command failed:", error);
          })
          .finally(finishOperation);
      } else {
        finishOperation();
      }
    } catch (error) {
      finishOperation();
      throw error;
    }
    writeJson(res, 202, { status: "accepted" });
  }

  private disconnectClient(clientId: string): void {
    const handler = this.handlers.get(clientId);
    const client = this.clients.get(clientId);
    if (!handler || !client) return;

    this.uploadRegistry.markClientDraftsOrphaned(clientId);
    handler.dispose();
    this.handlers.delete(clientId);
    this.clients.delete(clientId);
    this.clientUsers.delete(clientId);
    this.clientAuthentication.delete(clientId);
    this.clientLoginSessions.delete(clientId);
    this.eventBus.unregisterClient(clientId);
    this.closeSseStreams(clientId);
  }

  private isClientRoutable(clientId: string): boolean {
    return (
      this.clients.has(clientId) &&
      this.handlers.has(clientId) &&
      this.eventBus.hasActiveClientConnection(clientId)
    );
  }

  private registerSseStream(clientId: string, close: () => void): () => void {
    const streams = this.sseStreams.get(clientId) ?? new Set<() => void>();
    streams.add(close);
    this.sseStreams.set(clientId, streams);
    return () => {
      streams.delete(close);
      if (streams.size === 0) this.sseStreams.delete(clientId);
    };
  }

  private closeSseStreams(clientId: string): void {
    const streams = this.sseStreams.get(clientId);
    if (!streams) return;
    for (const close of [...streams]) {
      close();
    }
  }

  private async transferAnonymousUser(
    headers: http.IncomingHttpHeaders,
    expectedUserId: string,
    target: AuthenticatedUserContext,
  ): Promise<void> {
    if (!this.userRuntimeRegistry || !this.userContextResolver?.resolveAnonymous) {
      throw new UserContextError(503, "User data transfer is unavailable");
    }
    const source = await this.userContextResolver.resolveAnonymous(headers);
    if (!source || source.user.id !== expectedUserId) {
      throw new UserContextError(401, "Anonymous User binding is no longer valid");
    }
    const userIds = [source.user.id, target.user.id];
    const deadline = performance.now() + (this.config.userTransferTimeoutMs ?? 10_000);
    let finishTransfer: (() => void) | null;
    while (!(finishTransfer = this.beginExclusiveUserMutation(userIds))) {
      await this.waitForUserActivity(deadline);
    }
    try {
      // Prevent new operations from entering while accepted operations drain.
      // Do not move files still referenced by an in-flight command or upload.
      while (userIds.some(id => (this.activeUserOperations.get(id) ?? 0) > 0)) {
        await this.waitForUserActivity(deadline);
      }
      if (this.stopping) throw new HttpError(503, "Server is stopping");
      // A competing login or cleanup may have retired this binding while queued.
      const currentSource = await this.userContextResolver.resolveAnonymous(headers);
      if (!currentSource || currentSource.user.id !== expectedUserId) {
        throw new UserContextError(401, "Anonymous User binding is no longer valid");
      }
      await this.userRuntimeRegistry.transferOwnership(source, target, {
        assertIdle: () => {
          for (const [clientId, user] of this.clientUsers) {
            if (
              (user.user.id === source.user.id ||
                user.user.id === target.user.id) &&
              this.handlers.get(clientId)?.isBusy?.()
            ) {
              throw new HttpError(409, "User runtime is busy");
            }
          }
          if (
            (this.activeUserOperations.get(source.user.id) ?? 0) > 0 ||
            (this.activeUserOperations.get(target.user.id) ?? 0) > 0
          ) {
            throw new HttpError(409, "User runtime is being written");
          }
        },
        commitOwnership: async paths => {
          const rollbackUploads = this.uploadRegistry.transferOwnership(
            paths.sourceUserId,
            paths.targetUserId,
            paths.mapUserPath,
          );
          try {
            const revoked = await this.userContextResolver!.revokeAnonymous?.(
              headers,
              expectedUserId,
            );
            if (!revoked) {
              throw new UserContextError(
                401,
                "Anonymous User binding could not be revoked",
              );
            }
          } catch (error) {
            rollbackUploads();
            throw error;
          }
        },
      });
      for (const [clientId, user] of [...this.clientUsers]) {
        if (user.user.id === source.user.id) this.disconnectClient(clientId);
      }
      await this.userRuntimeRegistry
        .retireUser(source)
        .then(() =>
          this.userContextResolver?.completeAnonymousUserCleanup?.(
            source.user.id,
          ),
        )
        .catch(error => {
          console.warn("Transferred Anonymous User cleanup failed:", error);
        });
    } finally {
      finishTransfer();
    }
  }

  private disconnectLoginSession(loginSessionId: string): void {
    for (const [clientId, boundSessionId] of [...this.clientLoginSessions]) {
      if (boundSessionId === loginSessionId) this.disconnectClient(clientId);
    }
  }

  private async withUserWrite<T>(
    clientId: string | null,
    operation: () => Promise<T>,
  ): Promise<T> {
    if (!clientId) return operation();
    const finishOperation = this.beginUserOperation(clientId);
    try {
      return await operation();
    } finally {
      finishOperation();
    }
  }

  private beginUserOperation(clientId: string): () => void {
    const userId = this.clientUsers.get(clientId)?.user.id;
    if (!userId) return () => {};
    return this.beginUserOperationForUser(userId);
  }

  private beginUserOperationForUser(userId: string): () => void {
    if (this.transferringUserIds.has(userId)) {
      throw new HttpError(409, "User runtime ownership is changing");
    }
    this.activeUserOperations.set(
      userId,
      (this.activeUserOperations.get(userId) ?? 0) + 1,
    );
    let finished = false;
    return () => {
      if (finished) return;
      finished = true;
      const remaining = (this.activeUserOperations.get(userId) ?? 1) - 1;
      if (remaining > 0) this.activeUserOperations.set(userId, remaining);
      else this.activeUserOperations.delete(userId);
      this.notifyUserActivity();
    };
  }

  private notifyUserActivity(): void {
    for (const notify of [...this.userActivityWaiters]) notify();
  }

  private waitForUserActivity(deadline: number): Promise<void> {
    if (this.stopping) return Promise.reject(new HttpError(503, "Server is stopping"));
    const remaining = deadline - performance.now();
    if (remaining <= 0) {
      return Promise.reject(new HttpError(503, "User data transfer timed out"));
    }
    return new Promise((resolve, reject) => {
      const done = () => {
        clearTimeout(timer);
        this.userActivityWaiters.delete(done);
        if (this.stopping) reject(new HttpError(503, "Server is stopping"));
        else resolve();
      };
      const timer = setTimeout(() => {
        this.userActivityWaiters.delete(done);
        reject(new HttpError(503, "User data transfer timed out"));
      }, remaining);
      this.userActivityWaiters.add(done);
    });
  }

  private beginExclusiveUserMutation(
    userIds: readonly string[],
  ): (() => void) | null {
    const uniqueUserIds = [...new Set(userIds)];
    if (uniqueUserIds.some(userId => this.transferringUserIds.has(userId))) {
      return null;
    }
    for (const userId of uniqueUserIds) this.transferringUserIds.add(userId);
    return () => {
      for (const userId of uniqueUserIds) this.transferringUserIds.delete(userId);
      this.notifyUserActivity();
    };
  }

  private beginAnonymousUserCleanup(userId: string): (() => void) | null {
    const release = this.beginExclusiveUserMutation([userId]);
    if (!release) return null;
    const clientIds = [...this.clientUsers]
      .filter(([, user]) => user.user.id === userId)
      .map(([clientId]) => clientId);
    const isActive =
      (this.activeUserOperations.get(userId) ?? 0) > 0 ||
      clientIds.some(clientId => this.sseStreams.has(clientId)) ||
      clientIds.some(clientId => this.handlers.get(clientId)?.isBusy?.());
    if (isActive) {
      release();
      return null;
    }
    for (const clientId of clientIds) this.disconnectClient(clientId);
    return release;
  }

  private async cleanupAnonymousUser(userContext: UserContext): Promise<void> {
    if (!this.userRuntimeRegistry) {
      throw new Error("Anonymous User runtime cleanup is unavailable");
    }
    await this.uploadRegistry.deleteOwnedByUser(userContext.user.id);
    await this.userRuntimeRegistry.retireUser(userContext);
  }

  private handleStaticRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
  ): void {
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405, { "Content-Type": "text/plain" });
      res.end("Method Not Allowed");
      return;
    }

    const url = new URL(req.url || "/", `http://${req.headers.host}`);
    const pathname = url.pathname === "/" ? "/index.html" : url.pathname;
    const safePath = path.normalize(pathname).replace(/^(\.\.(\/|\\|$))+/, "");

    if (!this.config.staticDir) {
      if (safePath === "/index.html") {
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(getPlaceholderHtml(this.host, this.port));
      } else {
        res.writeHead(404, { "Content-Type": "text/plain" });
        res.end("Not Found - No web bundle configured");
      }
      return;
    }

    const root = path.resolve(this.config.staticDir);
    const filePath = path.resolve(path.join(root, safePath));
    if (filePath !== root && !filePath.startsWith(`${root}${path.sep}`)) {
      res.writeHead(403, { "Content-Type": "text/plain" });
      res.end("Forbidden");
      return;
    }

    fs.stat(filePath, (err, stats) => {
      if (err || !stats.isFile()) {
        const indexPath = path.join(this.config.staticDir!, "index.html");
        fs.stat(indexPath, (indexErr, indexStats) => {
          if (indexErr || !indexStats.isFile()) {
            res.writeHead(404, { "Content-Type": "text/plain" });
            res.end("Not Found");
            return;
          }
          this.serveFile(req, res, indexPath);
        });
        return;
      }

      this.serveFile(req, res, filePath);
    });
  }

  private serveFile(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    filePath: string,
  ): void {
    const ext = path.extname(filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || "application/octet-stream";

    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(500, { "Content-Type": "text/plain" });
        res.end("Internal Server Error");
        return;
      }

      const body =
        contentType === "text/html"
          ? injectRuntimeConfig(data.toString("utf8"), this.config)
          : data;

      res.writeHead(200, {
        "Content-Type": contentType,
        "Cache-Control": "no-cache",
      });
      if (req.method === "HEAD") {
        res.end();
        return;
      }
      res.end(body);
    });
  }
}

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".mjs": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
  ".eot": "application/vnd.ms-fontobject",
};

async function readJsonBody(req: http.IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;

  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_JSON_BODY_BYTES) {
      throw new Error("Request body is too large");
    }
    chunks.push(buffer);
  }

  const text = Buffer.concat(chunks).toString("utf8").trim();
  if (!text) {
    return {};
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new Error("Request body must be JSON");
  }
}

function normalizeUploadMimeType(value: unknown): string {
  if (typeof value !== "string") return "application/octet-stream";
  const mimeType = value.split(";")[0]?.trim().toLowerCase() ?? "";
  return mimeType || "application/octet-stream";
}

function normalizeUploadName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const base = path.basename(value.trim());
  return base && base !== "." && base !== ".." ? base : null;
}

function uploadPreviewUrl(id: string, clientId?: string): string {
  const route = `/api/uploads/${encodeURIComponent(id)}/preview`;
  return clientId
    ? `${route}?clientId=${encodeURIComponent(clientId)}`
    : route;
}

function workspacePreviewMimeType(filePath: string): string {
  const extension = path.extname(filePath).toLowerCase();
  if (extension === ".png") return "image/png";
  if (extension === ".jpg" || extension === ".jpeg") return "image/jpeg";
  if (extension === ".gif") return "image/gif";
  if (extension === ".webp") return "image/webp";
  if (extension === ".svg") return "image/svg+xml";
  if (extension === ".md" || extension === ".markdown") return "text/markdown; charset=utf-8";
  if (extension === ".txt" || extension === ".log") return "text/plain; charset=utf-8";
  return "application/octet-stream";
}

function normalizeSha256(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const hash = value.trim().toLowerCase();
  return /^[a-f0-9]{64}$/.test(hash) ? hash : null;
}

async function assertWorkspaceWriteParent(
  filePath: string,
  workspacePath: string,
): Promise<void> {
  await realPathInsideWorkspace(path.dirname(filePath), workspacePath);
}

async function realPathInsideWorkspace(
  targetPath: string,
  workspacePath: string,
): Promise<string> {
  const [workspaceRealPath, parentRealPath] = await Promise.all([
    fs.promises.realpath(workspacePath),
    fs.promises.realpath(targetPath),
  ]);
  if (!isInsidePath(parentRealPath, workspaceRealPath)) {
    throw new HttpError(403, "File path is outside the workspace");
  }
  return parentRealPath;
}

function isInsidePath(targetPath: string, rootPath: string): boolean {
  return targetPath === rootPath || targetPath.startsWith(rootPath + path.sep);
}

async function writeUploadBody(
  req: http.IncomingMessage,
  filePath: string,
  maxBytes: number,
): Promise<{ size: number; hash: string }> {
  const out = fs.createWriteStream(filePath, { flags: "wx" });
  const hash = createHash("sha256");
  let size = 0;
  try {
    for await (const chunk of req) {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      size += buffer.length;
      if (size > maxBytes) {
        throw new HttpError(413, "Upload is too large");
      }
      hash.update(buffer);
      if (!out.write(buffer)) {
        await once(out, "drain");
      }
    }
    out.end();
    await once(out, "finish");
    return { size, hash: hash.digest("hex") };
  } catch (error) {
    out.destroy();
    fs.rm(filePath, { force: true }, () => {});
    throw error;
  }
}

function isClientMessage(value: unknown): value is ClientMessage {
  if (!value || typeof value !== "object") return false;
  const data = value as Partial<ClientMessage>;
  if (data.type === "command") {
    return Boolean(data.payload && typeof data.payload === "object");
  }
  if (data.type === "extension_ui_response") {
    return Boolean(data.payload && typeof data.payload === "object");
  }
  return false;
}

function browserAuthentication(
  userContext: UserContext,
): BridgeAuthenticationState {
  const user = userContext.user;
  return "username" in user
    ? { status: "authenticated", user: toBrowserUserSummary(user) }
    : { status: "anonymous" };
}

function formatSseMessage(message: ServerMessage): string {
  const lines = ["event: message"];
  const data = JSON.stringify(message);
  for (const line of data.split(/\r?\n/)) {
    lines.push(`data: ${line}`);
  }
  return `${lines.join("\n")}\n\n`;
}

function writeJson(
  res: http.ServerResponse,
  status: number,
  data: unknown,
  cacheControl: "no-cache" | "no-store" = "no-cache",
): void {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": cacheControl,
  });
  res.end(JSON.stringify(data));
}

function runtimeDebugModeEnabled(): boolean {
  const value = process.env.PI_WEB_DEBUG;
  if (typeof value !== "string") return false;
  const normalized = value.trim().toLowerCase();
  return normalized === "1" || normalized === "true";
}

function serializeRuntimeConfig(config: unknown): string {
  return JSON.stringify(config).replace(/</g, "\\u003c");
}

function injectRuntimeConfig(html: string, config: BridgeConfig): string {
  const runtimeConfig: BridgeBrowserRuntimeConfig = {
    debugModeAvailable: runtimeDebugModeEnabled(),
    productName: config.productName,
    emptyState: config.emptyState,
    quickActions: config.quickActions,
    slashCommandsAndMentionsEnabled:
      config.slashCommandsAndMentionsEnabled,
    transcriptProcessSummaryEnabled:
      config.transcriptProcessSummaryEnabled,
  };
  const configScript = `<script>window.__PI_WEB_CONFIG__=${serializeRuntimeConfig(runtimeConfig)};</script>`;
  return html.includes("</head>")
    ? html.replace("</head>", `${configScript}</head>`)
    : `${configScript}${html}`;
}

function getPlaceholderHtml(
  _host: string,
  port: number,
): string {
  const lanIps = getLanIps();
  const httpUrl = (ip: string) => `http://${ip}:${port}`;
  const lanUrlLines =
    lanIps.length > 0
      ? lanIps
          .map(ip => {
            const label = isTailscaleIp(ip) ? " Tailscale" : "";
            return `<span class="code">${httpUrl(ip)}</span>${label}`;
          })
          .join("<br>\n\t\t\t")
      : "";

  return `<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title></title>
	<style>
		body {
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
			max-width: 800px;
			margin: 50px auto;
			padding: 20px;
			line-height: 1.6;
			color: #333;
		}
		h1 { color: #2563eb; }
		.status { background: #f0fdf4; border: 1px solid #86efac; padding: 15px; border-radius: 6px; margin: 20px 0; }
		.lan-info { background: #ecfdf5; padding: 15px; border-radius: 6px; margin: 20px 0; }
		.code { font-family: 'SF Mono', Monaco, monospace; background: #f3f4f6; padding: 2px 6px; border-radius: 3px; }
	</style>
</head>
<body>
	<h1>Pi Web Bridge</h1>
	<div class="status">
		<strong>Bridge server is running</strong><br>
		HTTP/SSE transport: <span class="code">/api/clients</span>
	</div>
	${
    lanIps.length > 0
      ? `<div class="lan-info">
		<strong>LAN Access:</strong><br>
			${lanUrlLines}
	</div>`
      : ""
  }
	<p>No web bundle is configured. Build the web UI or pass a staticDir.</p>
</body>
</html>`;
}
