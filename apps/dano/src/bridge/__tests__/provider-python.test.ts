import { execFile, type ExecFileOptions } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import {
  createBashTool,
  SessionManager,
  type AgentSessionEvent,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { afterEach, expect, it, vi } from "vitest";
import { CredentialBroker } from "../credential-broker.js";
import { withProviderPython, wrapProviderBash } from "../provider-python.js";

const executeFile = promisify(execFile);
const execute = (file: string, args: string[], options: ExecFileOptions = {}) =>
  executeFile(file, args, {
    ...options,
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
const cleanup: (() => Promise<unknown>)[] = [];
afterEach(async () => {
  vi.unstubAllEnvs();
  for (const close of cleanup.splice(0).reverse()) await close();
});

it("a real Python request uses its Assistant Turn's Login Session without receiving the provider token", async () => {
  const observed: { authorization?: string; url?: string }[] = [];
  const oa = createServer((req, res) => {
    observed.push({ authorization: req.headers.authorization, url: req.url });
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ code: 0, data: { total: 3, list: [] } }));
  });
  await new Promise<void>(resolve => oa.listen(0, "127.0.0.1", resolve));
  cleanup.push(() => new Promise<void>(resolve => oa.close(() => resolve())));
  const address = oa.address();
  if (!address || typeof address === "string")
    throw new Error("No OA listener");
  const broker = new CredentialBroker({
    providerApiOrigin: `http://127.0.0.1:${address.port}`,
    allowInsecureProviderApiOrigin: true,
    readCredential: async id =>
      id === "login-a" ? { accessToken: "oa-secret-a" } : null,
  });
  let emit!: (event: AgentSessionEvent) => void;
  broker.observe("user-a", {
    sessionId: "agent-a",
    subscribe(listener) {
      emit = listener;
      return () => {};
    },
  });
  broker.queueAssistantTurn("user-a", "agent-a", "login-a");
  emit({
    type: "message_start",
    message: { role: "user", content: "run skill", timestamp: 1 },
  } as AgentSessionEvent);
  emit({ type: "turn_start" } as AgentSessionEvent);
  const cwd = await mkdtemp(join(tmpdir(), "dano-python-"));
  cleanup.push(() => rm(cwd, { recursive: true, force: true }));
  const result = await withProviderPython(
    { broker, scope: "user-a", agentSessionId: "agent-a", cwd },
    async commandPrefix => {
      return execute(
        "bash",
        [
          "-c",
          commandPrefix +
            `python3 - <<'PY'\nimport json, os\nfrom dano_provider import request\nprint(json.dumps(request('GET', '/todo?pageNo=1')))\nassert 'oa-secret-a' not in str(dict(os.environ))\nPY`,
        ],
        { cwd },
      );
    },
  );
  expect(JSON.parse(result.stdout)).toMatchObject({ ok: true, status: 200 });
  expect(observed).toEqual([
    { authorization: "Bearer oa-secret-a", url: "/todo?pageNo=1" },
  ]);
});

it("the supplied PointLion Skill doctor and query succeed through Python and the Broker", async () => {
  const requests: string[] = [];
  const oa = createServer((req, res) => {
    if (req.headers.authorization !== "Bearer skill-token") {
      res.writeHead(401).end();
      return;
    }
    requests.push(req.url!);
    const route = req.url!.split("?")[0];
    const data =
      route === "/admin-api/bpm/category/simple-list"
        ? [{ name: "行政", code: "OA" }]
        : route === "/admin-api/bpm/process-definition/simple-list"
          ? [{ name: "请假", key: "leave" }]
          : route === "/admin-api/system/dict-data/simple-list"
            ? [{ dictType: "bpm_task_status", label: "审批中", value: "1" }]
            : { list: [], total: 0 };
    res.end(JSON.stringify({ code: 0, msg: "", data }));
  });
  await new Promise<void>(resolve => oa.listen(0, "127.0.0.1", resolve));
  cleanup.push(() => new Promise<void>(resolve => oa.close(() => resolve())));
  const address = oa.address();
  if (!address || typeof address === "string")
    throw new Error("No OA listener");
  const broker = new CredentialBroker({
    providerApiOrigin: `http://127.0.0.1:${address.port}`,
    allowInsecureProviderApiOrigin: true,
    readCredential: async () => ({ accessToken: "skill-token" }),
  });
  let emit!: (event: AgentSessionEvent) => void;
  broker.observe("user", {
    sessionId: "agent",
    subscribe(listener) {
      emit = listener;
      return () => {};
    },
  });
  broker.queueAssistantTurn("user", "agent", "login");
  emit({
    type: "message_start",
    message: { role: "user", content: "doctor", timestamp: 1 },
  } as AgentSessionEvent);
  emit({ type: "turn_start" } as AgentSessionEvent);
  const cwd = await mkdtemp(join(tmpdir(), "dano-pointlion-"));
  cleanup.push(() => rm(cwd, { recursive: true, force: true }));
  const script = resolve(
    "examples/skills/pointlion-todo-query/scripts/pointlion_todo.py",
  );
  for (const command of ["doctor", "query --page 1 --page-size 1"]) {
    const result = await withProviderPython(
      { broker, scope: "user", agentSessionId: "agent", cwd },
      prefix =>
        execute("bash", ["-c", prefix + `python3 '${script}' ${command}`], {
          cwd,
        }),
    );
    const body = JSON.parse(result.stdout);
    if (command === "doctor")
      expect(body).toMatchObject({
        ok: true,
        endpoints: { todoPage: { ok: true, total: 0 } },
      });
    else
      expect(body).toMatchObject({
        total: 0,
        count: 0,
        items: [],
        enumWarning: null,
      });
  }
  expect(requests).toEqual([
    "/admin-api/bpm/category/simple-list",
    "/admin-api/bpm/process-definition/simple-list",
    "/admin-api/system/dict-data/simple-list",
    "/admin-api/bpm/task/todo-page?pageNo=1&pageSize=1",
    "/admin-api/bpm/task/todo-page?pageNo=1&pageSize=1",
    "/admin-api/system/dict-data/simple-list",
  ]);
});

it("wraps the existing bash executor, preserves its checks and redacts the local capability", async () => {
  const cwd = await mkdtemp(join(tmpdir(), "dano-wrapped-bash-"));
  cleanup.push(() => rm(cwd, { recursive: true, force: true }));
  const broker = new CredentialBroker({
    providerApiOrigin: "https://oa.test",
    readCredential: async () => null,
  });
  const existing = createBashTool(cwd);
  let delegateCalls = 0;
  const definition = {
    ...existing,
    label: "guarded bash",
    async execute(...args: Parameters<typeof existing.execute>) {
      delegateCalls++;
      return existing.execute(...args);
    },
  } as ToolDefinition;
  const wrapped = wrapProviderBash(definition, { broker, scope: "user", cwd });
  const result = await wrapped.execute(
    "call",
    { command: "printf '%s' \"$DANO_PROVIDER_CAPABILITY\"" },
    undefined,
    undefined,
    { sessionManager: SessionManager.inMemory(cwd), cwd } as never,
  );
  expect(delegateCalls).toBe(1);
  expect(result.content).toEqual([{ type: "text", text: "[redacted]" }]);
  expect(wrapped.label).toBe("guarded bash");
});

async function pythonHarness() {
  const observed: {
    auth?: string;
    url?: string;
    method?: string;
    body: string;
  }[] = [];
  const oa = createServer(async (req, res) => {
    let body = "";
    for await (const part of req) body += part;
    observed.push({
      auth: req.headers.authorization,
      url: req.url,
      method: req.method,
      body,
    });
    if (
      req.url === "/refresh" &&
      req.headers.authorization === "Bearer expired"
    ) {
      res.writeHead(401).end();
      return;
    }
    if (req.url === "/redirect") {
      res.writeHead(302, { location: "http://example.invalid/stolen" }).end();
      return;
    }
    res.writeHead(req.url === "/business-denied" ? 403 : 200, {
      "content-type": "application/json",
    });
    res.end(
      JSON.stringify({
        code: req.url === "/business-denied" ? 403 : 0,
        data: "ok",
      }),
    );
  });
  await new Promise<void>(resolve => oa.listen(0, "127.0.0.1", resolve));
  cleanup.push(() => new Promise<void>(resolve => oa.close(() => resolve())));
  const address = oa.address();
  if (!address || typeof address === "string")
    throw new Error("No OA listener");
  const credentials = new Map([
    ["login-a", "token-a"],
    ["login-b", "token-b"],
  ]);
  let refreshed = 0;
  let refreshAllowed = true;
  const broker = new CredentialBroker({
    providerApiOrigin: `http://127.0.0.1:${address.port}`,
    allowInsecureProviderApiOrigin: true,
    readCredential: async id =>
      credentials.has(id)
        ? { accessToken: credentials.get(id)!, refreshToken: "refresh-secret" }
        : null,
    refreshCredential: async id => {
      refreshed++;
      if (!refreshAllowed) return null;
      credentials.set(id, "renewed");
      return { accessToken: "renewed" };
    },
  });
  const cwd = await mkdtemp(join(tmpdir(), "dano-python-auth-"));
  cleanup.push(() => rm(cwd, { recursive: true, force: true }));
  function session(scope: string, id: string, login?: string) {
    let emit!: (e: AgentSessionEvent) => void;
    const release = broker.observe(scope, {
      sessionId: id,
      subscribe(listener) {
        emit = listener;
        return () => {};
      },
    });
    function turn(login?: string) {
      broker.queueAssistantTurn(scope, id, login);
      emit({
        type: "message_start",
        message: { role: "user", content: "script", timestamp: 1 },
      } as AgentSessionEvent);
      emit({ type: "turn_start" } as AgentSessionEvent);
    }
    turn(login);
    return {
      options: { broker, scope, agentSessionId: id, cwd },
      turn,
      release,
      settled() {
        emit({ type: "agent_settled" } as AgentSessionEvent);
      },
    };
  }
  return {
    observed,
    credentials,
    session,
    broker,
    cwd,
    refreshCount: () => refreshed,
    disableRefresh() {
      refreshAllowed = false;
    },
  };
}

async function pythonRequest(
  prefix: string,
  expression = "request('GET', '/query')",
) {
  const result = await execute("bash", [
    "-c",
    prefix +
      `python3 - <<'PY'\nimport json\nfrom dano_provider import request, ProviderError\ntry:\n print(json.dumps(${expression}))\nexcept ProviderError as e:\n print(json.dumps({'error':e.code}))\nPY`,
  ]);
  return JSON.parse(result.stdout);
}

it("isolates concurrent Users and separate Login Sessions of the same User", async () => {
  const h = await pythonHarness();
  const sessions = [
    h.session("user", "one", "login-a"),
    h.session("user", "two", "login-b"),
    h.session("another", "three", "login-a"),
  ];
  await Promise.all(
    sessions.map((s, i) =>
      withProviderPython(s.options, p =>
        pythonRequest(p, `request('GET', '/query-${i}')`),
      ),
    ),
  );
  expect(h.observed.map(r => [r.url, r.auth]).sort()).toEqual([
    ["/query-0", "Bearer token-a"],
    ["/query-1", "Bearer token-b"],
    ["/query-2", "Bearer token-a"],
  ]);
});

it("rejects an anonymous script and cannot rebind a still-running script to the next Assistant Turn", async () => {
  const h = await pythonHarness();
  const guest = h.session("guest", "guest");
  expect(
    await withProviderPython(guest.options, p => pythonRequest(p)),
  ).toEqual({ error: "authentication_required" });
  const s = h.session("user", "one", "login-a");
  await withProviderPython(s.options, async p => {
    expect(await pythonRequest(p)).toMatchObject({ ok: true });
    s.settled();
    s.turn("login-b");
    expect(await pythonRequest(p)).toEqual({
      error: "authentication_required",
    });
  });
  expect(h.observed.map(r => r.auth)).toEqual(["Bearer token-a"]);
});

it("revokes script authority after logout, session disposal, cancellation and execution completion", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "one", "login-a");
  await withProviderPython(s.options, async p => {
    h.credentials.delete("login-a");
    expect(await pythonRequest(p)).toEqual({
      error: "authentication_required",
    });
    h.credentials.set("login-a", "token-a");
    s.release();
    expect(await pythonRequest(p)).toEqual({
      error: "authentication_required",
    });
  });
  const next = h.session("user", "two", "login-b");
  const abort = new AbortController();
  let endpoint = "",
    capability = "";
  await withProviderPython(
    { ...next.options, signal: abort.signal },
    async p => {
      const env = await execute("bash", [
        "-c",
        p + `printf '%s\\n%s' "$DANO_PROVIDER_URL" "$DANO_PROVIDER_CAPABILITY"`,
      ]);
      [endpoint, capability] = env.stdout.split("\n");
      const noAuth = await fetch(endpoint, { method: "POST", body: "{}" });
      expect(noAuth.status).toBe(403);
      abort.abort();
      expect(await pythonRequest(p)).toEqual({
        error: "provider_request_failed",
      });
    },
  );
  await expect(
    fetch(endpoint, {
      method: "POST",
      headers: { authorization: `Bearer ${capability}` },
      body: "{}",
    }),
  ).rejects.toThrow();
  expect(h.observed).toHaveLength(0);
});

it("refreshes an expired provider credential without exposing it to Python and fails closed if refresh fails", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "one", "login-a");
  h.credentials.set("login-a", "expired");
  const result = await withProviderPython(s.options, p =>
    pythonRequest(p, "request('GET', '/refresh')"),
  );
  expect(result).toMatchObject({ ok: true, status: 200 });
  expect(h.refreshCount()).toBe(1);
  expect(h.observed.map(r => r.auth)).toEqual([
    "Bearer expired",
    "Bearer renewed",
  ]);
  h.credentials.set("login-a", "expired");
  h.disableRefresh();
  expect(
    await withProviderPython(s.options, p =>
      pythonRequest(p, "request('GET', '/refresh')"),
    ),
  ).toEqual({ error: "reauth_required" });
  const count = h.observed.length;
  expect(await withProviderPython(s.options, p => pythonRequest(p))).toEqual({
    error: "reauth_required",
  });
  expect(h.observed).toHaveLength(count);
});

it("keeps provider origin and authentication headers server-owned and does not follow redirects or retry business errors", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "one", "login-a");
  await withProviderPython(s.options, async p => {
    expect(
      await pythonRequest(
        p,
        "request('GET', 'https://example.invalid/stolen')",
      ),
    ).toEqual({ error: "invalid_provider_request" });
    expect(
      await pythonRequest(
        p,
        "request('POST', '/json', headers={'Authorization':'Bearer attacker','Cookie':'session=attacker'}, body={'enabled':True})",
      ),
    ).toMatchObject({ ok: true, status: 200 });
    expect(await pythonRequest(p, "request('GET', '/redirect')")).toMatchObject(
      { ok: true, status: 302 },
    );
    expect(
      await pythonRequest(p, "request('GET', '/business-denied')"),
    ).toMatchObject({ ok: true, status: 403 });
  });
  expect(h.observed).toHaveLength(3);
  expect(h.observed[0]).toMatchObject({
    auth: "Bearer token-a",
    method: "POST",
    body: '{"enabled":true}',
  });
  expect(h.refreshCount()).toBe(0);
});

it("the real Dano session retains Heimdall's fail-closed bash boundary on unsupported platforms", async () => {
  if (process.platform === "linux") return;
  const { fauxProvider, fauxAssistantMessage, fauxToolCall } = await import(
    "@earendil-works/pi-ai"
  );
  const { ModelRuntime, SettingsManager } = await import(
    "@earendil-works/pi-coding-agent"
  );
  const { createDetachedAgentSessionRuntime } = await import(
    "../detached-session.js"
  );
  const { createHeadlessUIContext } = await import("../headless-ui-context.js");
  const h = await pythonHarness();
  const provider = fauxProvider({ provider: "python-sandbox-boundary" });
  provider.setResponses([
    fauxAssistantMessage(
      [fauxToolCall("bash", { command: "python3 -c 'print(123456)'" })],
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage("finished"),
  ]);
  const modelRuntime = await ModelRuntime.create({
    authPath: join(h.cwd, "auth.json"),
    modelsPath: null,
  });
  modelRuntime.registerNativeProvider(provider.provider);
  await modelRuntime.setRuntimeApiKey(provider.provider.id, "test-only");
  vi.stubEnv("PI_CODING_AGENT_DIR", join(h.cwd, "agent"));
  const runtime = await createDetachedAgentSessionRuntime(
    h.cwd,
    SessionManager.inMemory(h.cwd),
    {
      credentialBroker: h.broker,
      credentialBrokerScope: "user",
      modelRuntime,
      model: provider.getModel(),
      thinkingLevel: "off",
      settingsManager: SettingsManager.inMemory({
        packages: [],
        extensions: [],
      }),
    },
  );
  cleanup.push(() => runtime.runtime.dispose());
  await runtime.runtime.session.bindExtensions({
    uiContext: createHeadlessUIContext(),
  });
  h.broker.queueAssistantTurn(
    "user",
    runtime.runtime.session.sessionId,
    "login-a",
  );
  await runtime.runtime.session.prompt("run script");
  const result = runtime.runtime.session.messages.find(
    m => m.role === "toolResult",
  );
  expect(result).toMatchObject({ role: "toolResult", isError: true });
  expect(JSON.stringify(result)).toContain("Protected Configuration");
  expect(h.observed).toHaveLength(0);
});
