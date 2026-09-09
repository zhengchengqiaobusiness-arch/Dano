import {
  defineTool,
  type AgentSessionEvent,
  type AgentToolResult,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import type { ProviderCredential } from "./oauth-provider.js";

const AUTHENTICATION_REQUIRED = {
  ok: false,
  error: {
    code: "authentication_required",
    message: "Login is required for this provider request.",
  },
} as const;

const REAUTHENTICATION_REQUIRED = {
  ok: false,
  error: {
    code: "reauth_required",
    message: "Login is required again for this provider request.",
  },
} as const;

const credentialBrokerParameters = Type.Object({
  method: Type.String(),
  path: Type.String(),
  headers: Type.Optional(Type.Record(Type.String(), Type.String())),
  body: Type.Optional(Type.Unknown()),
});

type ProviderRequestToolArguments = {
  method: string;
  path: string;
  headers?: Record<string, string>;
  body?: unknown;
};

export function prepareProviderRequestArguments(
  input: unknown,
): ProviderRequestToolArguments {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return { method: "", path: "" };
  }
  const raw = input as Record<string, unknown>;
  const headers = normalizeArgumentHeaders(raw.headers);
  return {
    method: typeof raw.method === "string" ? raw.method : "",
    path: typeof raw.path === "string" ? raw.path : "",
    ...(Object.keys(headers).length > 0 ? { headers } : {}),
    ...(raw.body === undefined ? {} : { body: raw.body }),
  };
}

export interface CredentialSession {
  readonly sessionId: string;
  subscribe(listener: (event: AgentSessionEvent) => void): () => void;
}

export interface ProviderRequest {
  readonly method: string;
  readonly path: string;
  readonly headers?: Readonly<Record<string, string>>;
  readonly body?: unknown;
}

export type ProviderResponse =
  | {
      readonly ok: true;
      readonly status: number;
      readonly headers: Record<string, string>;
      readonly body: string;
    }
  | {
      readonly ok: false;
      readonly error: {
        readonly code:
          | "authentication_required"
          | "reauth_required"
          | "provider_request_failed";
        readonly message: string;
      };
    }
  | ProviderRequestInvalidResponse;

interface ProviderRequestInvalidResponse {
  readonly ok: false;
  readonly status: "invalid";
  readonly error: {
    readonly code: "invalid_provider_request";
    readonly category: "validation";
    readonly message: string;
    readonly retryable: true;
    readonly issues: readonly ProviderRequestIssue[];
  };
}

interface ProviderRequestIssue {
  readonly code:
    | "invalid_provider_method"
    | "invalid_provider_path"
    | "invalid_provider_body";
  readonly path: "method" | "path" | "body";
  readonly message: string;
}

export interface CredentialBrokerOptions {
  readonly providerApiOrigin: string;
  /** Explicit deployment opt-in for a plaintext HTTP provider API origin. */
  readonly allowInsecureProviderApiOrigin?: boolean;
  readonly readCredential: (
    loginSessionId: string,
  ) => Promise<ProviderCredential | null>;
  readonly refreshCredential?: (
    loginSessionId: string,
  ) => Promise<ProviderCredential | null>;
  readonly requireReauthentication?: (loginSessionId: string) => Promise<void>;
  readonly isAccessTokenInvalid?: (
    response: Response,
  ) => boolean | Promise<boolean>;
  readonly observeRequestBinding?: (loginSessionId: string) => void;
  readonly observeCredentialAvailability?: (available: boolean) => void;
  readonly observeRequestStage?: (
    stage:
      | "binding_confirmed"
      | "reauth_short_circuit"
      | "credential_read"
      | "request_sent"
      | "invalid_detected",
  ) => void;
  readonly fetch?: typeof fetch;
}

interface AssistantTurnBinding {
  readonly id: number;
  readonly loginSessionId?: string;
  queueEntry?: object;
}

export interface AssistantTurnBindingHandle {
  readonly scope: string;
  readonly agentSessionId: string;
  readonly id: number;
}

interface SessionBindings {
  readonly pending: AssistantTurnBinding[];
  enqueueTail?: Promise<void>;
  piTurnStarted?: boolean;
  awaitingTurn?: AssistantTurnBinding;
  assistantTurn?: AssistantTurnBinding;
  activePiTurn?: AssistantTurnBinding;
  unsubscribe?: () => void;
}

const FORBIDDEN_REQUEST_HEADERS = new Set([
  "authorization",
  "cookie",
  "host",
  "proxy-authorization",
]);
const FORBIDDEN_RESPONSE_HEADERS = new Set([
  "authorization",
  "proxy-authenticate",
  "set-cookie",
]);

export class CredentialBroker {
  private readonly providerApiOrigin: string;
  private readonly providerFetch: typeof fetch;
  private readonly sessions = new Map<
    string,
    Map<string, SessionBindings>
  >();
  private readonly queuedEntries = new WeakMap<
    object,
    AssistantTurnBindingHandle
  >();
  private readonly refreshes = new Map<
    string,
    Promise<ProviderCredential | null>
  >();
  private readonly reauthentication = new Map<string, Promise<void>>();
  private readonly reauthenticationRequired = new Set<string>();
  private nextBindingId = 1;

  constructor(private readonly options: CredentialBrokerOptions) {
    const providerApiOrigin = new URL(options.providerApiOrigin);
    if (
      providerApiOrigin.protocol !== "https:" &&
      !(
        providerApiOrigin.protocol === "http:" &&
        options.allowInsecureProviderApiOrigin
      )
    ) {
      throw new Error("Provider API origin must use HTTPS");
    }
    this.providerApiOrigin = providerApiOrigin.origin;
    this.providerFetch = options.fetch ?? fetch;
  }

  observe(scope: string, session: CredentialSession): () => void {
    this.release(scope, session.sessionId);
    const state: SessionBindings = { pending: [] };
    const unsubscribe = session.subscribe(event => {
      this.handleSessionEvent(scope, session.sessionId, state, event);
    });
    state.unsubscribe = unsubscribe;
    let scopedSessions = this.sessions.get(scope);
    if (!scopedSessions) {
      scopedSessions = new Map();
      this.sessions.set(scope, scopedSessions);
    }
    scopedSessions.set(session.sessionId, state);
    return () => {
      if (this.sessionState(scope, session.sessionId) === state) {
        this.release(scope, session.sessionId);
      } else {
        unsubscribe();
      }
    };
  }

  queueAssistantTurn(
    scope: string,
    agentSessionId: string,
    loginSessionId: string | undefined,
  ): AssistantTurnBindingHandle | undefined {
    const state = this.sessionState(scope, agentSessionId);
    if (!state) return undefined;
    const binding = {
      id: this.nextBindingId++,
      ...(loginSessionId ? { loginSessionId } : {}),
    };
    state.pending.push(binding);
    return { scope, agentSessionId, id: binding.id };
  }

  associateQueuedAssistantTurn(
    handle: AssistantTurnBindingHandle | undefined,
    queueEntry: object | undefined,
  ): void {
    if (!handle || !queueEntry) return;
    const pending = this.sessionState(
      handle.scope,
      handle.agentSessionId,
    )?.pending;
    const binding = pending?.find(candidate => candidate.id === handle.id);
    if (!binding) return;
    binding.queueEntry = queueEntry;
    this.queuedEntries.set(queueEntry, handle);
  }

  async enqueueAssociatedAssistantTurn(
    handle: AssistantTurnBindingHandle | undefined,
    queuedEntries: () => readonly object[],
    enqueue: () => Promise<void>,
  ): Promise<void> {
    if (!handle) {
      await enqueue();
      return;
    }
    const state = this.sessionState(handle.scope, handle.agentSessionId);
    if (!state) {
      await enqueue();
      return;
    }
    const previous = state.enqueueTail ?? Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>(resolve => {
      release = resolve;
    });
    const tail = previous.then(() => gate);
    state.enqueueTail = tail;
    await previous;
    try {
      const before = new Set(queuedEntries());
      await enqueue();
      const queueEntry = queuedEntries().find(entry => !before.has(entry));
      if (queueEntry) {
        this.associateQueuedAssistantTurn(handle, queueEntry);
      } else {
        this.cancelQueuedAssistantTurn(handle);
      }
    } catch (error) {
      this.cancelQueuedAssistantTurn(handle);
      throw error;
    } finally {
      release();
      if (state.enqueueTail === tail) state.enqueueTail = undefined;
    }
  }

  cancelQueuedAssistantTurn(
    handle: AssistantTurnBindingHandle | undefined,
  ): void {
    if (!handle) return;
    const pending = this.sessionState(
      handle.scope,
      handle.agentSessionId,
    )?.pending;
    if (!pending) return;
    const index = pending.findIndex(binding => binding.id === handle.id);
    if (index >= 0) {
      const [removed] = pending.splice(index, 1);
      if (removed?.queueEntry) this.queuedEntries.delete(removed.queueEntry);
    }
  }

  cancelQueuedAssistantTurnForEntry(queueEntry: object | undefined): void {
    if (!queueEntry) return;
    const handle = this.queuedEntries.get(queueEntry);
    this.queuedEntries.delete(queueEntry);
    this.cancelQueuedAssistantTurn(handle);
  }

  clearQueuedAssistantTurns(scope: string, agentSessionId: string): void {
    const state = this.sessionState(scope, agentSessionId);
    if (!state) return;
    for (const binding of state.pending) {
      if (binding.queueEntry) this.queuedEntries.delete(binding.queueEntry);
    }
    state.pending.length = 0;
    state.awaitingTurn = undefined;
  }

  createTool(scope: string) {
    return defineTool({
      name: "provider_request",
      label: "Provider Request",
      description:
        "Send a generic authenticated request to the configured provider API without exposing its credential.",
      promptSnippet:
        "Use provider_request for provider API calls required by a Skill",
      promptGuidelines: [
        "Pass only a relative provider path plus method, headers, and body; do not request or supply credentials, user identities, Login Session identifiers, or an origin.",
      ],
      parameters: credentialBrokerParameters,
      prepareArguments: prepareProviderRequestArguments,
      executionMode: "sequential",
      execute: async (
        _toolCallId,
        request,
        signal,
        _onUpdate,
        context,
      ): Promise<AgentToolResult<ProviderResponse>> => {
        const response = await this.request(
          scope,
          context.sessionManager.getSessionId(),
          request,
          signal,
        );
        return {
          content: [{ type: "text", text: JSON.stringify(response) }],
          details: response,
        };
      },
    });
  }

  /** Capture authority at script start; a later turn must never replace it. */
  bindRequest(scope: string, agentSessionId: string) {
    const state = this.sessionState(scope, agentSessionId);
    const binding = state?.activePiTurn;
    return (request: ProviderRequest, signal?: AbortSignal): Promise<ProviderResponse> => {
      if (
        !binding?.loginSessionId || signal?.aborted ||
        this.sessionState(scope, agentSessionId) !== state ||
        state?.activePiTurn !== binding
      ) return Promise.resolve(AUTHENTICATION_REQUIRED);
      return this.request(scope, agentSessionId, request, signal);
    };
  }

  async request(
    scope: string,
    agentSessionId: string,
    request: ProviderRequest,
    signal?: AbortSignal,
  ): Promise<ProviderResponse> {
    const issues: ProviderRequestIssue[] = [];
    const target =
      typeof request.path === "string"
        ? this.resolveTarget(request.path)
        : null;
    if (!target) {
      issues.push({
        code: "invalid_provider_path",
        path: "path",
        message: "Path must stay on the configured provider origin.",
      });
    }
    const method =
      typeof request.method === "string"
        ? request.method.trim().toUpperCase()
        : "";
    if (!method || !isHttpToken(method)) {
      issues.push({
        code: "invalid_provider_method",
        path: "method",
        message: "Method must be a valid HTTP token.",
      });
    }
    let body: string | undefined;
    try {
      body = requestBody(request.body);
    } catch {
      issues.push({
        code: "invalid_provider_body",
        path: "body",
        message: "Body must be JSON serializable or a string.",
      });
    }
    if (issues.length > 0) return invalidProviderRequest(issues);

    const binding = this.sessionState(scope, agentSessionId)?.activePiTurn;
    if (!binding?.loginSessionId) return AUTHENTICATION_REQUIRED;
    try {
      this.options.observeRequestBinding?.(binding.loginSessionId);
    } catch {}
    try {
      this.options.observeRequestStage?.("binding_confirmed");
    } catch {}
    if (this.reauthenticationRequired.has(binding.loginSessionId)) {
      try {
        this.options.observeRequestStage?.("reauth_short_circuit");
      } catch {}
      return REAUTHENTICATION_REQUIRED;
    }
    try {
      this.options.observeRequestStage?.("credential_read");
    } catch {}
    const credential = await this.options.readCredential(binding.loginSessionId);
    try {
      this.options.observeCredentialAvailability?.(Boolean(credential));
    } catch {}
    if (!credential) return AUTHENTICATION_REQUIRED;

    const send = (requestCredential: ProviderCredential) => {
      const headers = requestHeaders(request.headers, requestCredential);
      if (body !== undefined && typeof request.body !== "string") {
        headers["content-type"] ??= "application/json";
      }
      try {
        this.options.observeRequestStage?.("request_sent");
      } catch {}
      return this.providerFetch(target!, {
        method,
        headers,
        ...(body === undefined ? {} : { body }),
        redirect: "manual",
        signal,
      });
    };
    try {
      let response = await send(credential);
      let responseCredential = credential;
      if (await this.accessTokenInvalid(response)) {
        try {
          this.options.observeRequestStage?.("invalid_detected");
        } catch {}
        const latestCredential = await this.options.readCredential(
          binding.loginSessionId,
        );
        const refreshed =
          latestCredential?.accessToken !== credential.accessToken
            ? latestCredential
            : credential.refreshToken
              ? await this.refreshLoginSession(binding.loginSessionId)
              : null;
        if (!refreshed) {
          await this.markReauthenticationRequired(binding.loginSessionId);
          return REAUTHENTICATION_REQUIRED;
        }
        responseCredential = refreshed;
        response = await send(refreshed);
        if (await this.accessTokenInvalid(response)) {
          await this.markReauthenticationRequired(binding.loginSessionId);
          return REAUTHENTICATION_REQUIRED;
        }
      }
      const secrets = [
        credential.accessToken,
        credential.refreshToken,
        responseCredential.accessToken,
        responseCredential.refreshToken,
      ].filter(
        (value): value is string => Boolean(value),
      );
      return {
        ok: true,
        status: response.status,
        headers: responseHeaders(response.headers, secrets),
        body: redactSecrets(await response.text(), secrets),
      };
    } catch {
      return {
        ok: false,
        error: {
          code: "provider_request_failed",
          message: "The provider request failed.",
        },
      };
    }
  }

  private async accessTokenInvalid(response: Response): Promise<boolean> {
    return (
      (await this.options.isAccessTokenInvalid?.(response)) ??
      response.status === 401
    );
  }

  private refreshLoginSession(
    loginSessionId: string,
  ): Promise<ProviderCredential | null> {
    const existing = this.refreshes.get(loginSessionId);
    if (existing) return existing;
    const refresh = (this.options.refreshCredential?.(loginSessionId) ??
      Promise.resolve(null))
      .catch(() => null)
      .finally(() => {
        if (this.refreshes.get(loginSessionId) === refresh) {
          this.refreshes.delete(loginSessionId);
        }
      });
    this.refreshes.set(loginSessionId, refresh);
    return refresh;
  }

  private markReauthenticationRequired(loginSessionId: string): Promise<void> {
    this.reauthenticationRequired.add(loginSessionId);
    const existing = this.reauthentication.get(loginSessionId);
    if (existing) return existing;
    const required = (this.options.requireReauthentication?.(loginSessionId) ??
      Promise.resolve())
      .catch(() => {})
      .finally(() => {
        if (this.reauthentication.get(loginSessionId) === required) {
          this.reauthentication.delete(loginSessionId);
        }
      });
    this.reauthentication.set(loginSessionId, required);
    return required;
  }

  private handleSessionEvent(
    scope: string,
    agentSessionId: string,
    state: SessionBindings,
    event: AgentSessionEvent,
  ): void {
    if (event.type === "message_start" && event.message.role === "user") {
      const queueEntry = event.message as object;
      const associated = this.queuedEntries.get(queueEntry);
      const pendingIndex = associated
        ? state.pending.findIndex(
            binding =>
              binding.id === associated.id &&
              associated.scope === scope &&
              associated.agentSessionId === agentSessionId,
          )
        : state.pending.findIndex(binding => !binding.queueEntry);
      if (pendingIndex >= 0) {
        const [binding] = state.pending.splice(pendingIndex, 1);
        state.awaitingTurn = binding;
        if (binding?.queueEntry) this.queuedEntries.delete(binding.queueEntry);
        if (state.piTurnStarted) {
          state.assistantTurn = binding;
          state.activePiTurn = binding;
          state.awaitingTurn = undefined;
        }
      }
      return;
    }
    if (event.type === "turn_start") {
      state.piTurnStarted = true;
      if (state.awaitingTurn) {
        state.assistantTurn = state.awaitingTurn;
        state.awaitingTurn = undefined;
      }
      state.activePiTurn = state.assistantTurn;
      return;
    }
    if (event.type === "turn_end") {
      state.piTurnStarted = false;
      state.activePiTurn = undefined;
      return;
    }
    if (event.type === "agent_settled") {
      this.clearPending(state);
      state.piTurnStarted = false;
      state.awaitingTurn = undefined;
      state.assistantTurn = undefined;
      state.activePiTurn = undefined;
    }
  }

  private resolveTarget(relativePath: string): URL | null {
    if (!relativePath.startsWith("/") || relativePath.startsWith("//")) {
      return null;
    }
    try {
      const target = new URL(relativePath, this.providerApiOrigin);
      return target.origin === this.providerApiOrigin ? target : null;
    } catch {
      return null;
    }
  }

  private release(scope: string, agentSessionId: string): void {
    const scopedSessions = this.sessions.get(scope);
    const state = scopedSessions?.get(agentSessionId);
    scopedSessions?.delete(agentSessionId);
    if (scopedSessions?.size === 0) this.sessions.delete(scope);
    state?.unsubscribe?.();
    if (state) {
      this.clearPending(state);
      state.awaitingTurn = undefined;
      state.assistantTurn = undefined;
      state.activePiTurn = undefined;
    }
  }

  private clearPending(state: SessionBindings): void {
    for (const binding of state.pending) {
      if (binding.queueEntry) this.queuedEntries.delete(binding.queueEntry);
    }
    state.pending.length = 0;
  }

  private sessionState(
    scope: string,
    agentSessionId: string,
  ): SessionBindings | undefined {
    return this.sessions.get(scope)?.get(agentSessionId);
  }
}

function requestHeaders(
  input: Readonly<Record<string, string>> | undefined,
  credential: ProviderCredential,
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [name, value] of Object.entries(input ?? {})) {
    const normalizedName = name.trim().toLowerCase();
    if (!normalizedName || FORBIDDEN_REQUEST_HEADERS.has(normalizedName)) {
      continue;
    }
    headers[normalizedName] = value;
  }
  headers.authorization = `${providerAuthorizationScheme(credential.tokenType)} ${credential.accessToken}`;
  return headers;
}

function providerAuthorizationScheme(tokenType: string | undefined): "Bearer" {
  const normalized = tokenType?.trim();
  if (!normalized || normalized.toLowerCase() === "bearer") return "Bearer";
  throw new Error("Unsupported Provider Credential token type");
}

function normalizeArgumentHeaders(input: unknown): Record<string, string> {
  let value = input;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value.trim()) as unknown;
    } catch {
      return {};
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const normalized: Record<string, string> = {};
  for (const [name, headerValue] of Object.entries(value)) {
    if (
      typeof headerValue === "string" ||
      typeof headerValue === "number" ||
      typeof headerValue === "boolean"
    ) {
      normalized[name] = String(headerValue);
    }
  }
  return normalized;
}

function requestBody(input: unknown): string | undefined {
  if (input === undefined) return undefined;
  if (typeof input === "string") return input;
  return JSON.stringify(input);
}

function invalidProviderRequest(
  issues: readonly ProviderRequestIssue[],
): ProviderRequestInvalidResponse {
  return {
    ok: false,
    status: "invalid",
    error: {
      code: "invalid_provider_request",
      category: "validation",
      message: "Provider request arguments are invalid.",
      retryable: true,
      issues,
    },
  };
}

function responseHeaders(
  input: Headers,
  secrets: readonly string[],
): Record<string, string> {
  const headers: Record<string, string> = {};
  input.forEach((value, name) => {
    const normalizedName = name.toLowerCase();
    if (FORBIDDEN_RESPONSE_HEADERS.has(normalizedName)) return;
    headers[normalizedName] = redactSecrets(value, secrets);
  });
  return headers;
}

function redactSecrets(value: string, secrets: readonly string[]): string {
  let redacted = value;
  for (const secret of secrets) {
    if (!secret) continue;
    for (const representation of new Set([secret, encodeURIComponent(secret)])) {
      redacted = redacted.split(representation).join("[redacted]");
    }
  }
  return redacted;
}

function isHttpToken(value: string): boolean {
  return /^[!#$%&'*+.^_`|~0-9A-Z-]+$/.test(value);
}
