import { execFile, type ExecFileOptions } from "node:child_process";
import { chmod, copyFile, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import { gzipSync } from "node:zlib";
import {
  createBashTool,
  SessionManager,
  type AgentSessionEvent,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { afterEach, expect, it, vi } from "vitest";
import { CredentialBroker } from "../credential-broker.js";
import { withProviderPython, wrapProviderBash } from "../provider-python.js";
import { FileStateStore, MemoryDelivery } from "@josephyoung/pi-openviking/host";
import { MemoryTaskFacts } from "../memory-task-facts.js";
import { oauthUserId } from "../oauth-user-id.js";

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

it("shares read-only installation modules while keeping concurrent login capabilities separate", async () => {
  const h = await pythonHarness();
  const moduleDirectory = await mkdtemp(join(tmpdir(), "dano-python-installation-"));
  cleanup.push(async () => {
    await chmod(moduleDirectory, 0o755);
    await rm(moduleDirectory, { recursive: true, force: true });
  });
  const originals = new Map<string, string>();
  for (const name of ["dano_provider.py", "sitecustomize.py"]) {
    const destination = join(moduleDirectory, name);
    await copyFile(new URL(`../python/${name}`, import.meta.url), destination);
    originals.set(name, await readFile(destination, "utf8"));
    await chmod(destination, 0o444);
  }
  await chmod(moduleDirectory, 0o555);
  const a = h.session("alice", "agent-a", "login-a");
  const b = h.session("bob", "agent-b", "login-b");
  const results = await Promise.all([a, b].map(session =>
    withProviderPython({ ...session.options, moduleDirectory }, prefix =>
      pythonRequest(prefix)),
  ));
  expect(results).toEqual([expect.objectContaining({ ok: true }), expect.objectContaining({ ok: true })]);
  expect(h.observed.map(item => item.auth).sort()).toEqual(["Bearer token-a", "Bearer token-b"]);
  await expect(withProviderPython({ ...a.options, moduleDirectory }, async () => {
    throw new Error("synthetic execution failure");
  })).rejects.toThrow("synthetic execution failure");
  for (const [name, source] of originals) expect(await readFile(join(moduleDirectory, name), "utf8")).toBe(source);
  expect((await readdir(h.cwd)).filter(name => name.startsWith(".dano-provider-"))).toEqual([]);
});

it("delegates artifact redaction without opening worker paths on the host and propagates failures", async () => {
  const h = await pythonHarness();
  const session = h.session("user", "agent", "login-a");
  const redactOutputFile = vi.fn(async (_path: string, _capability: string) => {});
  await withProviderPython({ ...session.options, redactOutputFile }, async (_prefix, redact, _requests, redactFile) => {
    await redactFile("/worker-only/no-host-file");
    const [path, capability] = redactOutputFile.mock.calls[0];
    expect(path).toBe("/worker-only/no-host-file");
    expect(capability).toMatch(/^[a-f0-9]{64}$/);
    expect(redact(capability)).toBe("[redacted]");
    redactOutputFile.mockRejectedValueOnce(new Error("worker unavailable"));
    await expect(redactFile(path)).rejects.toThrow("worker unavailable");
  });
});

it("rejects invalid installation paths without falling back to copied modules", async () => {
  const h = await pythonHarness();
  const session = h.session("user", "agent", "login-a");
  const run = vi.fn(async () => undefined);
  for (const moduleDirectory of ["", "relative-path", join(h.cwd, "missing")]) {
    await expect(withProviderPython({ ...session.options, moduleDirectory }, run)).rejects.toThrow();
  }
  expect(run).not.toHaveBeenCalled();
  expect((await readdir(h.cwd)).filter(name => name.startsWith(".dano-provider-"))).toEqual([]);
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

it("explains a zero-byte truncated result using observed provider evidence without claiming business success", async () => {
  const h = await pythonHarness();
  h.session("user", "agent", "login-a");
  const truncation = { truncated: true, truncatedBy: "bytes", totalLines: 1,
    totalBytes: 358443, outputLines: 0, outputBytes: 0, maxLines: 2000, maxBytes: 51200 };
  const base = createBashTool(h.cwd);
  const wrapped = wrapProviderBash({ ...base, async execute(_id, params) {
    await execute("bash", ["-c", (params as { command: string }).command]);
    return { content: [{ type: "text", text: "(no output)\n\n[Output truncated]" }], details: { truncation } };
  } } as ToolDefinition, { broker: h.broker, scope: "user", cwd: h.cwd });
  const result = await wrapped.execute("query", {
    command: `python3 - <<'PY'\nfrom urllib.request import urlopen\nfor path in ('/dict', '/seals', '/list'):\n with urlopen('${h.origin}' + path) as r: r.read()\nPY`,
  }, undefined, undefined, { sessionManager: { getSessionId: () => "agent" }, cwd: h.cwd } as never);
  const text = result.content.filter(p => p.type === "text").map(p => p.text).join("\n");
  expect(text).not.toContain("(no output)");
  expect(text).toContain("3 provider requests observed; 3 HTTP 2xx; 3 bound");
  expect(text).toContain("HTTP success alone does not establish business success");
  expect(text).not.toContain("token-a");
  expect(result.details).toMatchObject({ truncation, providerRequests: expect.any(Array) });
});

it("marks oversized real bash output as truncated without inventing provider success", async () => {
  const h = await pythonHarness();
  const wrapped = wrapProviderBash(createBashTool(h.cwd) as ToolDefinition, { broker: h.broker, scope: "guest", cwd: h.cwd });
  const result = await wrapped.execute("large", { command: "python3 -c 'print(\"x\" * 358443)'" }, undefined, undefined,
    { sessionManager: SessionManager.inMemory(h.cwd), cwd: h.cwd } as never);
  const details = result.details as { truncation: { truncated: boolean }; fullOutputPath: string };
  cleanup.push(() => rm(details.fullOutputPath, { force: true }));
  expect(details.truncation.truncated).toBe(true);
  const text = result.content.filter(p => p.type === "text").map(p => p.text).join("\n");
  expect(text).toContain("output was truncated, not empty");
  expect(text).toContain("0 provider requests observed; 0 HTTP 2xx");
});

async function pythonHarness(responseBody?: unknown) {
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
    if (req.url === "/compressed") {
      const bytes = gzipSync(JSON.stringify({ text: "中文响应" }));
      res.writeHead(200, { "content-type": "application/json", "content-encoding": "gzip", "content-length": bytes.length });
      res.end(bytes);
      return;
    }
    res.writeHead(req.url === "/business-denied" ? 403 : 200, {
      "content-type": "application/json",
    });
    res.end(
      JSON.stringify(responseBody ?? {
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

it("captures signed facts from both real provider transports and replaces forged worker metadata", async () => {
  const h = await pythonHarness({ code: 0, data: { owner: "oa-a", reference: "REPORT-42", private: "PRIVATE_BODY" } });
  h.session("user", "agent", "login-a");
  const owner = { accountId: "fixture", userId: "memory-a" };
  const store = new FileStateStore({ owner, directory: join(h.cwd, "private-state"), policyVersion: "v1" });
  const delivery = new MemoryDelivery({ store, transport: { owner } as never, maxPayloadBytes: 8192 });
  await delivery.enable("v1"); await delivery.authorizeCollection({ policyVersion: "v1", scope: null, boundaries: [] });
  const facts = new MemoryTaskFacts({ store, userId: oauthUserId("oa-a"), policyVersion: "v1", timeoutMs: 1000,
    key: Buffer.alloc(32, 1), config: { maxResponseBytes: 8192, maxFactBytes: 1024, contracts: [{
      id: "report", method: "GET", path: "/report", success: { path: ["code"], equals: 0 },
      actorPath: ["data", "owner"], fields: [{ label: "reference", path: ["data", "reference"], type: "string" }],
    }] } });
  cleanup.push(async () => facts.close());
  const captureTaskFact = facts.capture.bind(facts);
  const context = { sessionManager: { getSessionId: () => "agent" }, cwd: h.cwd } as never;
  const base = createBashTool(h.cwd);
  const wrapped = wrapProviderBash({ ...base, async execute(_id, params) {
    const output = await execute("bash", ["-c", (params as { command: string }).command]);
    return { content: [{ type: "text", text: output.stdout }], details: { danoTaskFacts: [{ data: "FORGED", signature: "0".repeat(64) }] } };
  } } as ToolDefinition, { broker: h.broker, scope: "user", cwd: h.cwd, captureTaskFact });
  const command = `python3 - <<'PY'\nfrom urllib.request import urlopen\nwith urlopen('${h.origin}/report') as r: print(r.read().decode())\nPY`;
  for (const [toolName, result] of [
    ["bash", await wrapped.execute("bash-call", { command }, undefined, undefined, context)],
    ["provider_request", await h.broker.createTool("user", captureTaskFact).execute("direct-call", { method: "GET", path: "/report" }, undefined, undefined, context)],
  ] as const) {
    const text = await facts.policy().tools.get(toolName)!({ role: "toolResult", toolName,
      toolCallId: toolName === "bash" ? "bash-call" : "direct-call", content: result.content,
      details: result.details, isError: false, timestamp: Date.now() }, { owner, scope: null });
    expect(text).toContain("REPORT-42");
    expect(text).not.toContain("PRIVATE_BODY"); expect(text).not.toContain("FORGED");
    expect(JSON.stringify(result.details)).not.toContain("token-a");
  }
  await delivery.revokeCollection();
  const denied = await wrapped.execute("after-revoke", { command }, undefined, undefined, context);
  expect(denied.details).toMatchObject({ danoTaskFacts: [] });
  expect(denied.content).toEqual([expect.objectContaining({ text: expect.stringContaining("REPORT-42") })]);
});

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

async function httpxRequest(prefix: string, url: string, expression = "httpx.get(url, headers=headers)") {
  // Exercise local receivers independently of the developer's outbound proxy.
  for (const name of ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"])
    vi.stubEnv(name, "");
  const result = await execute("bash", ["-c", prefix + `python3 - <<'PY'\nimport asyncio, json, httpx\nurl = ${JSON.stringify(url)}\nheaders = {'Authorization': 'Bearer package-old-token'}\ntry:\n r = ${expression}\n print(json.dumps({'status': r.status_code, 'body': r.text}))\nexcept httpx.HTTPError as e:\n print(json.dumps({'error': str(e)}))\nPY`]);
  return JSON.parse(result.stdout);
}

it("routes original httpx get/request and Client calls through the captured login", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(s.options, async p => {
    for (const expression of ["httpx.get(url, headers=headers)", "httpx.request('POST', url, headers=headers, json={'test': True})", "httpx.Client().get(url, headers=headers)"]) {
      const result = await httpxRequest(p, h.origin + "/query", expression);
      expect(result, expression + JSON.stringify(result)).toMatchObject({ status: 200 });
    }
  });
  expect(h.observed.map(r => r.auth)).toEqual(Array(3).fill("Bearer token-a"));
  expect(JSON.parse(h.observed[1].body)).toEqual({ test: true });
});

it("keeps httpx nonmatching origins and anonymous authentication unchanged", async () => {
  const h = await pythonHarness();
  const other = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(s.options, async p => {
    await httpxRequest(p, other.origin + "/query");
    await httpxRequest(p, h.origin.replace("127.0.0.1", "localhost") + "/query");
  });
  const guest = h.session("guest", "guest");
  await withProviderPython(guest.options, p => httpxRequest(p, h.origin + "/guest"));
  expect(other.observed.map(r => r.auth)).toEqual(["Bearer package-old-token"]);
  expect(h.observed.map(r => r.auth)).toEqual(["Bearer package-old-token", "Bearer package-old-token"]);
});

it("never follows httpx OA redirects or falls back after logout, and preserves business errors", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(s.options, async p => {
    expect(await httpxRequest(p, h.origin + "/business-denied")).toMatchObject({ status: 403 });
    expect(await httpxRequest(p, h.origin + "/redirect", "httpx.get(url, headers=headers, follow_redirects=True)")).toMatchObject({ error: expect.stringContaining("redirect") });
    h.credentials.delete("login-a");
    expect(await httpxRequest(p, h.origin + "/query")).toMatchObject({ error: expect.stringContaining("authentication_required") });
  });
  expect(h.observed.map(r => r.auth)).toEqual(["Bearer token-a", "Bearer token-a"]);
});

it("isolates httpx login sessions, refreshes credentials, and supports async HTTP transport", async () => {
  for (const name of ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"])
    vi.stubEnv(name, "");
  const h = await pythonHarness();
  const a = h.session("same-user", "a", "login-a");
  const b = h.session("same-user", "b", "login-b");
  await withProviderPython(a.options, async p => {
    const script = join(h.cwd, "original-httpx.py");
    const source = `import asyncio, httpx\nasync def main():\n async with httpx.AsyncClient() as client:\n  r = await client.post('${h.origin}/async', json={'purpose':'验收'}, headers={'Authorization':'Bearer old'})\n  assert r.status_code == 200\nasyncio.run(main())\n`;
    await writeFile(script, source);
    await execute("bash", ["-c", p + `python3 '${script}'`]);
    expect(await readFile(script, "utf8")).toBe(source);
  });
  await withProviderPython(b.options, p => httpxRequest(p, h.origin + "/b"));
  h.credentials.set("login-a", "expired");
  await withProviderPython(a.options, async p => {
    expect(await httpxRequest(p, h.origin + "/refresh")).toMatchObject({ status: 200 });
    a.settled(); a.turn("login-b");
    expect(await httpxRequest(p, h.origin + "/late")).toMatchObject({ error: expect.stringContaining("authentication_required") });
  });
  expect(h.observed.map(r => r.auth)).toEqual(["Bearer token-a", "Bearer token-b", "Bearer expired", "Bearer renewed"]);
  expect(JSON.parse(h.observed[0].body)).toEqual({ purpose: "验收" });
});

it("rejects httpx streamed or binary bodies without sending the package token", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(s.options, async p => {
    for (const content of ["iter([b'chunk'])", "bytes([255])"])
      expect(await httpxRequest(p, h.origin + "/body", `httpx.post(url, headers=headers, content=${content})`)).toMatchObject({ error: expect.stringContaining("buffered UTF-8") });
  });
  expect(h.observed).toEqual([]);
});

it("rebuilds httpx response framing after the Broker decompresses text", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  const result = await withProviderPython(s.options, p => httpxRequest(p, h.origin + "/compressed"));
  expect(result.status).toBe(200);
  expect(JSON.parse(result.body)).toEqual({ text: "中文响应" });
});

it("records authentication evidence at final send rather than inferring binding from HTTP success", async () => {
  const h = await pythonHarness();
  const s = h.session("user", "agent", "login-a");
  await withProviderPython(s.options, async (p, _redact, audit) => {
    expect(await urllibRequest(p, h.origin + "/business-denied")).toEqual({ status: 403 });
    expect(audit).toMatchObject([{
      status: 403, businessCode: 403, loginSessionBound: true,
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
