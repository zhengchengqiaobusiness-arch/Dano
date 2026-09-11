import { execFile, type ExecFileOptions } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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

it("the original PointLion ZIP stays byte-identical while its doctor and query use the login credential", async () => {
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
  const archive = resolve("apps/dano/src/bridge/__tests__/fixtures/pointlion-todo-query-token-inline.zip");
  await execute("python3", ["-c", "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])", archive, cwd]);
  const manifest = async () => (await execute("python3", ["-c", "import zipfile,sys,hashlib,pathlib,json; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps({n:hashlib.sha256((pathlib.Path(sys.argv[2])/n).read_bytes()).hexdigest()==hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if not n.endswith('/')}))", archive, cwd])).stdout;
  expect(Object.values(JSON.parse(await manifest())).every(Boolean)).toBe(true);
  const script = join(cwd, "pointlion-todo-query/scripts/pointlion_todo.py");
  for (const command of ["doctor", "query --page 1 --page-size 1"]) {
    const result = await withProviderPython(
      { broker, scope: "user", agentSessionId: "agent", cwd },
      prefix =>
        execute("bash", ["-c", prefix + `python3 '${script}' --base-url http://127.0.0.1:${address.port} --token INVALID-ORIGINAL-TOKEN ${command}`], {
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
  expect(Object.values(JSON.parse(await manifest())).every(Boolean)).toBe(true);
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

it("redacts full-output capabilities across file chunks without changing other text", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(
    s.options,
    async (prefix, _redact, _requests, redactFile) => {
      const path = join(h.cwd, "output.log");
      await execute(
        "bash",
        [
          "-c",
          prefix +
            'python3 -c \'import os; print(("x" * 65520 + os.environ["DANO_PROVIDER_CAPABILITY"] + "汉字\\n") * 3, end="")\' > output.log',
        ],
        { cwd: h.cwd },
      );
      await redactFile(path);
      expect(await readFile(path, "utf8")).toBe(
        ("x".repeat(65520) + "[redacted]汉字\n").repeat(3),
      );
      const untouched = Buffer.concat([
        Buffer.alloc(65470, "x"),
        Buffer.from("😀" + "z".repeat(128)),
        Buffer.from([0, 255, 128]),
      ]);
      await writeFile(path, untouched);
      await redactFile(path);
      expect(await readFile(path)).toEqual(untouched);
    },
  );
});

it("withholds live full-output artifacts and sanitizes them before publication", async () => {
  const h = await pythonHarness();
  const wrapped = wrapProviderBash(createBashTool(h.cwd) as ToolDefinition, {
    broker: h.broker,
    scope: "user",
    cwd: h.cwd,
  });
  const updates: unknown[] = [];
  const result = await wrapped.execute(
    "verbose",
    {
      command:
        'python3 -c \'import os; print((os.environ["DANO_PROVIDER_CAPABILITY"] + "\\n") * 3000)\'',
    },
    undefined,
    update => updates.push(update),
    {
      sessionManager: SessionManager.inMemory(h.cwd),
      cwd: h.cwd,
    } as never,
  );
  const path = (result.details as { fullOutputPath: string }).fullOutputPath;
  cleanup.push(() => rm(path, { force: true }));
  expect(updates.length).toBeGreaterThan(0);
  for (const update of updates)
    expect(JSON.stringify(update)).not.toContain(path);
  expect(
    (await readFile(path, "utf8"))
      .trim()
      .split("\n")
      .every(line => line === "[redacted]"),
  ).toBe(true);
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
    if (req.url === "/stalled") return;
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
    origin: `http://127.0.0.1:${address.port}`,
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

it("bounds a stalled OA request with the Python client's timeout", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  expect(
    await withProviderPython(s.options, prefix =>
      pythonRequest(prefix, "request('GET', '/stalled', timeout=0.05)"),
    ),
  ).toEqual({ error: "provider_request_failed" });
});

it("preserves existing Python module paths for non-OA scripts", async () => {
  const h = await pythonHarness();
  await writeFile(join(h.cwd, "existing_module.py"), "value = 42\n");
  vi.stubEnv("PYTHONPATH", h.cwd);
  const s = h.session("user", "agent", "login-a");
  const result = await withProviderPython(s.options, prefix =>
    execute(
      "bash",
      [
        "-c",
        prefix +
          "python3 -c 'import existing_module, dano_provider; print(existing_module.value)'",
      ],
      { cwd: tmpdir() },
    ),
  );
  expect(result.stdout.trim()).toBe("42");
});

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

it("transparently replaces an unmodified urllib script's old Authorization at the OA receiver", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  const script = join(h.cwd, "original.py");
  const source = `from urllib.request import Request, build_opener\nimport json\nreq = Request('${h.origin}/query', headers={'Authorization': 'Bearer original-old-token'})\nwith build_opener().open(req) as r:\n print(json.dumps({'status': r.status, 'body': json.load(r)}))\n`;
  await writeFile(script, source);
  const result = await withProviderPython(s.options, prefix =>
    execute("bash", ["-c", prefix + `python3 '${script}'`], { cwd: h.cwd }),
  );
  expect(JSON.parse(result.stdout)).toMatchObject({ status: 200 });
  expect(h.observed).toMatchObject([{ auth: "Bearer token-a", url: "/query" }]);
  expect(await readFile(script, "utf8")).toBe(source);
});

async function urllibRequest(prefix: string, url: string, options = "") {
  const result = await execute("bash", ["-c", prefix + `python3 - <<'PY'\nimport json\nfrom urllib.request import Request, build_opener\nfrom urllib.error import HTTPError, URLError\ntry:\n with build_opener().open(Request(${JSON.stringify(url)}, ${options || "headers={'Authorization':'Bearer original'}"}), timeout=2) as r:\n  print(json.dumps({'status':r.status,'body':r.read().decode()}))\nexcept HTTPError as e:\n print(json.dumps({'status':e.code}))\nexcept URLError as e:\n print(json.dumps({'error':str(e.reason)}))\nPY`]);
  return JSON.parse(result.stdout);
}

it("records authentication evidence at final send rather than inferring binding from HTTP success", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(s.options, async (p, _redact, audit) => {
    expect(await urllibRequest(p, h.origin + "/business-denied")).toEqual({ status: 403 });
    expect(audit).toMatchObject([{
      status: 403, loginSessionBound: true,
      sends: [{ targetMatched: true, authorizationMatched: true }],
    }]);
  });
});

it("does not inject into different ports or hostnames, and preserves an anonymous script's own authentication", async () => {
  const h = await pythonHarness();
  const other = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(s.options, async p => {
    expect(await urllibRequest(p, other.origin + "/other-port")).toMatchObject({ status: 200 });
    expect(await urllibRequest(p, h.origin.replace("127.0.0.1", "localhost") + "/other-host")).toMatchObject({ status: 200 });
  });
  const guest = h.session("guest", "guest");
  await withProviderPython(guest.options, p => urllibRequest(p, h.origin + "/guest"));
  expect(other.observed.map(r => r.auth)).toEqual(["Bearer original"]);
  expect(h.observed.map(r => r.auth)).toEqual(["Bearer original", "Bearer original"]);
});

it("transparent urllib cannot follow OA redirects or fall back to its old token after logout or a later Turn", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  let ended = "";
  await withProviderPython(s.options, async p => {
    ended = p;
    expect(await urllibRequest(p, h.origin + "/redirect")).toEqual({ status: 302 });
    h.credentials.delete("login-a");
    expect(await urllibRequest(p, h.origin + "/query")).toMatchObject({ error: expect.stringContaining("authentication_required") });
    s.settled(); s.turn("login-b");
    expect(await urllibRequest(p, h.origin + "/query")).toMatchObject({ error: expect.stringContaining("authentication_required") });
  });
  // A surviving child still has its already-loaded hook; the expired endpoint
  // must never instruct it to fall back. Exercise that while files still exist
  // via cancellation in the separate lifetime test.
  expect(ended).not.toBe("");
  expect(h.observed.map(r => r.auth)).toEqual(["Bearer token-a"]);
});

it("keeps existing sitecustomize behavior and urllib JSON request semantics", async () => {
  const h = await pythonHarness();
  await writeFile(join(h.cwd, "sitecustomize.py"), "import os\nos.environ['EXISTING_SITE_CUSTOMIZATION'] = 'kept'\n");
  vi.stubEnv("PYTHONPATH", h.cwd);
  const s = h.session("user", "agent", "login-a");
  const result = await withProviderPython(s.options, p => execute("bash", ["-c", p + `python3 - <<'PY'\nimport os\nfrom urllib.request import Request, urlopen\nassert os.environ.get('EXISTING_SITE_CUSTOMIZATION') == 'kept'\nwith urlopen(Request('${h.origin}/json', data=b'{"text":"hello"}', headers={'Content-Type':'application/json','Authorization':'Bearer old'})) as r:\n assert r.getcode() == 200\n print(r.read().decode())\nPY`], { cwd: tmpdir() }));
  expect(JSON.parse(result.stdout)).toMatchObject({ code: 0 });
  expect(h.observed).toMatchObject([{ method: "POST", body: '{"text":"hello"}', auth: "Bearer token-a" }]);
});

it("isolates transparent urllib calls across logins and refreshes without falling back to package credentials", async () => {
  const h = await pythonHarness();
  const sessions = [h.session("same-user", "a", "login-a"), h.session("same-user", "b", "login-b"), h.session("other-user", "c", "login-a")];
  await Promise.all(sessions.map((s, i) => withProviderPython(s.options, p => urllibRequest(p, h.origin + `/parallel-${i}`, "headers={}"))));
  expect(h.observed.map(r => [r.url, r.auth]).sort()).toEqual([
    ["/parallel-0", "Bearer token-a"], ["/parallel-1", "Bearer token-b"], ["/parallel-2", "Bearer token-a"],
  ]);
  h.credentials.set("login-a", "expired");
  await withProviderPython(sessions[0].options, async p => {
    expect(await urllibRequest(p, h.origin + "/refresh")).toMatchObject({ status: 200 });
    h.credentials.set("login-a", "expired"); h.disableRefresh();
    expect(await urllibRequest(p, h.origin + "/refresh")).toMatchObject({ error: expect.stringContaining("reauth_required") });
  });
  expect(h.observed.slice(3).map(r => r.auth)).toEqual(["Bearer expired", "Bearer renewed", "Bearer expired"]);
});

it("does not send a credential resolved after its captured Assistant Turn has ended", async () => {
  let resolveCredential!: (value: { accessToken: string }) => void;
  let signalRead!: () => void;
  const readStarted = new Promise<void>(resolve => { signalRead = resolve; });
  const send = vi.fn<typeof fetch>();
  const broker = new CredentialBroker({ providerApiOrigin: "https://oa.test", readCredential: () => {
    signalRead(); return new Promise(resolve => { resolveCredential = resolve; });
  }, fetch: send });
  let emit!: (event: AgentSessionEvent) => void;
  broker.observe("user", { sessionId: "agent", subscribe(listener) { emit = listener; return () => {}; } });
  broker.queueAssistantTurn("user", "agent", "login");
  emit({ type: "message_start", message: {role:"user",content:"go",timestamp:1} } as AgentSessionEvent);
  emit({ type: "turn_start" } as AgentSessionEvent);
  const cwd = await mkdtemp(join(tmpdir(), "dano-read-race-"));
  cleanup.push(() => rm(cwd, { recursive: true, force: true }));
  await withProviderPython({broker,scope:"user",agentSessionId:"agent",cwd}, async p => {
    const pending = urllibRequest(p, "https://oa.test/query");
    await readStarted;
    emit({ type: "agent_settled" } as AgentSessionEvent);
    resolveCredential({accessToken:"must-not-be-sent"});
    expect(await pending).toMatchObject({error:expect.any(String)});
  });
  expect(send).not.toHaveBeenCalled();
});

it("preserves urllib request processors, default form headers, response processors and audit events", async () => {
  const received: Record<string, string | string[] | undefined>[] = [];
  const oa = createServer((req, res) => { received.push(req.headers); res.end('ok'); });
  await new Promise<void>(resolve => oa.listen(0, '127.0.0.1', resolve));
  cleanup.push(() => new Promise<void>(resolve => oa.close(() => resolve())));
  const port = (oa.address() as {port:number}).port;
  const broker = new CredentialBroker({providerApiOrigin:`http://127.0.0.1:${port}`,allowInsecureProviderApiOrigin:true,readCredential:async()=>({accessToken:'current'})});
  let emit!: (event: AgentSessionEvent)=>void;
  broker.observe('u',{sessionId:'a',subscribe(listener){emit=listener;return()=>{};}});
  broker.queueAssistantTurn('u','a','login');
  emit({type:'message_start',message:{role:'user',content:'go',timestamp:1}} as AgentSessionEvent);
  emit({type:'turn_start'} as AgentSessionEvent);
  const cwd = await mkdtemp(join(tmpdir(),'dano-handlers-'));
  cleanup.push(()=>rm(cwd,{recursive:true,force:true}));
  await withProviderPython({broker,scope:'u',agentSessionId:'a',cwd},p=>execute('bash',['-c',p+`python3 - <<'PY'\nimport sys\nfrom urllib.request import BaseHandler, Request, build_opener\naudits=[]\nsys.addaudithook(lambda event,args: audits.append(event))\nclass Business(BaseHandler):\n def http_request(self, req):\n  req.add_header('X-Tenant','tenant-1')\n  return req\n def http_response(self, req, response):\n  response.headers['X-Processed']='yes'\n  return response\nwith build_opener(Business()).open(Request('http://127.0.0.1:${port}/form',data=b'a=1')) as response:\n assert response.headers['X-Processed']=='yes'\nassert 'urllib.Request' in audits\nPY`]));
  expect(received).toMatchObject([{'authorization':'Bearer current','x-tenant':'tenant-1','content-type':'application/x-www-form-urlencoded'}]);
});

it("stops Python before running the Skill if its authentication startup module cannot import", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(s.options, async p => {
    const path = (await execute("bash", ["-c", p + 'printf %s "$PYTHONPATH"'])).stdout.split(":")[0];
    await writeFile(join(path,"dano_provider.py"),'raise RuntimeError("broken module")\n');
    await expect(urllibRequest(p,h.origin+'/must-not-run')).rejects.toThrow('Dano Python authentication initialization failed');
  });
  expect(h.observed).toHaveLength(0);
});

it("matches canonical default ports and hostname case without exposing duplicate Authorization headers", async () => {
  const received: {url:string,headers:Headers}[]=[];
  const broker=new CredentialBroker({providerApiOrigin:'https://oa.test',readCredential:async()=>({accessToken:'canonical-token'}),fetch:async(url,init)=>{received.push({url:String(url),headers:new Headers(init?.headers)});return new Response('{}',{status:200});}});
  let emit!: (event:AgentSessionEvent)=>void;
  broker.observe('u',{sessionId:'a',subscribe(listener){emit=listener;return()=>{};}});
  broker.queueAssistantTurn('u','a','login');emit({type:'message_start',message:{role:'user',content:'go',timestamp:1}} as AgentSessionEvent);emit({type:'turn_start'} as AgentSessionEvent);
  const cwd=await mkdtemp(join(tmpdir(),'dano-canonical-'));cleanup.push(()=>rm(cwd,{recursive:true,force:true}));
  await withProviderPython({broker,scope:'u',agentSessionId:'a',cwd},p=>urllibRequest(p,'https://OA.TEST:443/query',"headers={'Authorization':'Bearer old','authorization':'Bearer duplicate'}"));
  expect(received.map(r=>r.url)).toEqual(['https://oa.test/query']);
  expect(received[0].headers.get('authorization')).toBe('Bearer canonical-token');
});
