import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { InMemoryCredentialStore, fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";
import { ModelRuntime, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { afterEach, expect, it, vi } from "vitest";
import { createDetachedAgentSessionRuntime, type CreateDetachedAgentSessionOptions } from "../detached-session.js";
import { createHeadlessUIContext } from "../headless-ui-context.js";
import type { ProtectedSessionTools } from "../protected-session-tools.js";
import { DetachedSessionRegistry } from "../session-registry.js";

const roots: string[] = [];
const created: Awaited<ReturnType<typeof createDetachedAgentSessionRuntime>>[] = [];
afterEach(async () => {
  for (const result of created.splice(0)) {
    result.disposeDanoLlmResilience();
    await result.runtime.dispose();
  }
  vi.unstubAllEnvs();
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
});

async function harness() {
  const root = await realpath(await mkdtemp(join(tmpdir(), "dano-protected-session-")));
  roots.push(root);
  const workspace = join(root, "workspace");
  const agentDir = join(root, "private-agent");
  await mkdir(workspace);
  await mkdir(agentDir);
  vi.stubEnv("PI_CODING_AGENT_DIR", agentDir);
  const settingsManager = SettingsManager.inMemory();
  const modelRuntime = await ModelRuntime.create({ credentials: new InMemoryCredentialStore(),
    modelsPath: null, refreshOnCreate: false, allowModelNetwork: false });
  // This double proves runtime routing/lifecycle only. Kernel isolation is
  // exercised separately by the committed disposable Linux fixture.
  const worker = {
    workspace,
    assertIsolated: vi.fn(async () => {}),
    execute: vi.fn(async (name: string, _parameters: Record<string, unknown>,
      _signal?: AbortSignal, update?: (value: unknown) => void) => {
      if (name === "user_bash") {
        update?.({ data: Buffer.from("WORKER_SHELL").toString("base64") });
        return { exitCode: 0 };
      }
      return { content: [{ type: "text", text: "WORKER_RESULT" }] };
    }),
  };
  const resolveWorker = vi.fn(async (_workspace: string, _revision?: () => number | undefined) => worker);
  const bindMemory = vi.fn();
  const trustedSkillPaths: string[] = [];
  const profile: ProtectedSessionTools = { agentDir, trustedSkillPaths, resolveWorker,
    createMemoryExtension: () => pi => { bindMemory(); pi.on("context", () => {}); } };
  return { root, workspace, worker, resolveWorker, bindMemory, profile, modelRuntime, settingsManager, trustedSkillPaths,
    async start(options: Pick<CreateDetachedAgentSessionOptions, "model" | "thinkingLevel" | "askUserQuestionTool"> = {}) {
      const result = await createDetachedAgentSessionRuntime(workspace, SessionManager.inMemory(workspace),
        { modelRuntime, settingsManager, protectedTools: profile, ...options });
      created.push(result);
      await result.runtime.session.bindExtensions({ mode: "rpc", uiContext: createHeadlessUIContext() });
      return result.runtime;
    } };
}

it("routes file and interactive Shell operations through the worker without loading workspace code", async () => {
  const h = await harness();
  await mkdir(join(h.workspace, ".pi/extensions"), { recursive: true });
  const marker = join(h.root, "untrusted-extension-ran");
  await writeFile(join(h.workspace, ".pi/extensions/untrusted.ts"),
    `import {writeFileSync} from 'node:fs'; export default () => writeFileSync(${JSON.stringify(marker)}, 'bad');`);
  const runtime = await h.start();
  const runner = runtime.session.extensionRunner;
  expect(runtime.session.getAllTools().filter(tool => tool.name === "read")).toHaveLength(1);
  const read = runner.getToolDefinition("read")!;
  expect(await read.execute("read", { path: "missing-on-host.txt" }, undefined, undefined, runner.createToolContext("test-call", undefined)))
    .toMatchObject({ content: [{ text: "WORKER_RESULT" }] });
  expect(h.worker.execute).toHaveBeenCalledWith("read", { path: "missing-on-host.txt" },
    expect.any(AbortSignal), expect.any(Function));
  const intercepted = await runner.emitUserBash({ type: "user_bash", command: "echo not-run-on-host",
    cwd: h.workspace, excludeFromContext: true });
  expect(intercepted?.operations).toBeDefined();
  const shell = await runtime.session.executeBash("echo not-run-on-host", undefined,
    { operations: intercepted?.operations });
  expect(shell.output).toContain("WORKER_SHELL");
  expect(h.worker.execute.mock.calls.some(([name]) => name === "user_bash")).toBe(true);
  await expect(readFile(marker)).rejects.toMatchObject({ code: "ENOENT" });
  expect(h.bindMemory).toHaveBeenCalledOnce();
}, 30_000);

it("loads deployment models from the host without copying credentials into the protected user directory", async () => {
  const h = await harness();
  const hostAgentDir = join(h.root, "host-agent");
  await mkdir(hostAgentDir);
  vi.stubEnv("PI_CODING_AGENT_DIR", hostAgentDir);
  await writeFile(join(hostAgentDir, "models.json"), JSON.stringify({ providers: {
    "host-model-fixture": {
      baseUrl: "http://127.0.0.1:1/v1", api: "openai-completions", apiKey: "synthetic-host-key",
      models: [{ id: "host-model", name: "Host model", reasoning: false, input: ["text"],
        contextWindow: 16000, maxTokens: 1024,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
    },
  } }));
  const result = await createDetachedAgentSessionRuntime(h.workspace, SessionManager.inMemory(h.workspace),
    { settingsManager: h.settingsManager, protectedTools: h.profile });
  created.push(result);
  const checkModel = () => {
    expect(result.runtime.session.modelRuntime.getModel("host-model-fixture", "host-model"))
      .toMatchObject({ id: "host-model", provider: "host-model-fixture" });
    expect(result.runtime.session.modelRuntime.hasConfiguredAuth("host-model-fixture")).toBe(true);
  };
  checkModel();
  await result.runtime.session.reload();
  checkModel();
  for (const name of ["models.json", "auth.json"]) {
    await expect(readFile(join(h.profile.agentDir, name))).rejects.toMatchObject({ code: "ENOENT" });
  }
}, 30_000);

it("retains deployment settings without trusting workspace settings or copying host configuration", async () => {
  const h = await harness();
  const hostAgentDir = join(h.root, "host-agent");
  await mkdir(hostAgentDir);
  vi.stubEnv("PI_CODING_AGENT_DIR", hostAgentDir);
  await writeFile(join(hostAgentDir, "settings.json"), JSON.stringify({
    defaultProvider: "deployment-provider", defaultModel: "deployment-model", defaultThinkingLevel: "high",
    defaultProjectTrust: "always",
  }));
  await mkdir(join(h.workspace, ".pi"));
  await writeFile(join(h.workspace, ".pi/settings.json"), JSON.stringify({ defaultModel: "untrusted-workspace-model" }));
  const result = await createDetachedAgentSessionRuntime(h.workspace, SessionManager.inMemory(h.workspace),
    { modelRuntime: h.modelRuntime, protectedTools: h.profile });
  created.push(result);
  const check = () => {
    const settings = result.runtime.session.settingsManager;
    expect(settings.getDefaultProvider()).toBe("deployment-provider");
    expect(settings.getDefaultModel()).toBe("deployment-model");
    expect(settings.getDefaultThinkingLevel()).toBe("high");
    expect(settings.isProjectTrusted()).toBe(false);
  };
  check();
  await result.runtime.session.reload();
  check();
  await expect(readFile(join(h.profile.agentDir, "settings.json"))).rejects.toMatchObject({ code: "ENOENT" });
}, 30_000);

it("loads the deployment system prompt and refreshes it without copying it into user resources", async () => {
  const h = await harness();
  const hostAgentDir = join(h.root, "host-agent");
  await mkdir(hostAgentDir);
  vi.stubEnv("PI_CODING_AGENT_DIR", hostAgentDir);
  await writeFile(join(hostAgentDir, "SYSTEM.md"), "Trusted deployment prompt one.");
  await writeFile(join(h.profile.agentDir, "SYSTEM.md"), "User resource prompt.");
  const runtime = await h.start();
  expect(runtime.session.resourceLoader.getSystemPrompt()).toBe("Trusted deployment prompt one.");
  await writeFile(join(hostAgentDir, "SYSTEM.md"), "Trusted deployment prompt two.");
  await runtime.session.reload();
  expect(runtime.session.resourceLoader.getSystemPrompt()).toBe("Trusted deployment prompt two.");
  expect(await readFile(join(h.profile.agentDir, "SYSTEM.md"), "utf8")).toBe("User resource prompt.");
}, 30_000);

it("retains explicitly approved Skills across reload without discovering workspace Skills", async () => {
  const h = await harness();
  const trusted = join(h.root, "approved-skill");
  const untrusted = join(h.workspace, ".pi/skills/unapproved-skill");
  for (const [directory, name] of [[trusted, "approved-skill"], [untrusted, "unapproved-skill"]]) {
    await mkdir(directory, { recursive: true });
    await writeFile(join(directory, "SKILL.md"), `---\nname: ${name}\ndescription: Synthetic fixture\n---\nFixture instructions.\n`);
  }
  h.trustedSkillPaths.push(trusted);
  const runtime = await h.start();
  const names = () => runtime.session.resourceLoader.getSkills().skills.map(skill => skill.name);
  expect(names()).toEqual(["approved-skill"]);
  await runtime.session.reload();
  expect(names()).toEqual(["approved-skill"]);
  expect(runtime.session.getAllTools().filter(tool => tool.name === "read")).toHaveLength(1);
  expect(h.bindMemory).toHaveBeenCalledTimes(2);
}, 30_000);

it("routes model-triggered Bash to the worker instead of executing the command on the host", async () => {
  const h = await harness();
  const provider = fauxProvider({ provider: "protected-session-fixture" });
  provider.setResponses([
    fauxAssistantMessage([fauxToolCall("bash", { command: "printf unsafe > host-marker.txt" })],
      { stopReason: "toolUse" }),
    fauxAssistantMessage("finished"),
  ]);
  h.modelRuntime.registerNativeProvider(provider.provider);
  await h.modelRuntime.setRuntimeApiKey(provider.provider.id, "synthetic-test-key");
  const runtime = await h.start({ model: provider.getModel(), thinkingLevel: "off" });
  await runtime.session.prompt("Run the fixture command");
  expect(h.worker.execute.mock.calls.some(([name, input]) =>
    name === "bash" && input.command === "printf unsafe > host-marker.txt")).toBe(true);
  await expect(readFile(join(h.workspace, "host-marker.txt"))).rejects.toMatchObject({ code: "ENOENT" });
}, 30_000);

it("keeps the protected binding during registry forks and rejects foreign workspaces", async () => {
  const h = await harness();
  const registry = new DetachedSessionRegistry(h.workspace, undefined, {
    modelRuntime: h.modelRuntime, settingsManager: h.settingsManager, protectedTools: h.profile,
  });
  try {
    const foreign = join(h.root, "foreign");
    await mkdir(foreign);
    const outside = registry.createSession({ cwd: foreign, sessionDir: join(h.root, "foreign-sessions") });
    await expect(outside.ensureSession()).rejects.toThrow("MEMORY_WORKER_WORKSPACE_MISMATCH");
    const saved = SessionManager.create(h.workspace, join(h.root, "sessions"));
    const entry = saved.appendMessage({ role: "user", content: "fork fixture", timestamp: Date.now() });
    saved.appendMessage(fauxAssistantMessage("saved"));
    const fork = await registry.forkSession(saved.getSessionFile()!, entry);
    expect(fork.cancelled).toBe(false);
    const session = await registry.ensureSession(fork.sessionPath);
    const runner = session.extensionRunner;
    await runner.getToolDefinition("read")!.execute("fork", { path: "worker.txt" },
      undefined, undefined, runner.createToolContext("test-call", undefined));
    expect(h.worker.execute.mock.calls.some(([name]) => name === "read")).toBe(true);
  } finally {
    await registry.dispose();
  }
}, 30_000);

it("rebinds new and restored runtimes and invalidates old tool references", async () => {
  const h = await harness();
  const runtime = await h.start();
  const oldRunner = runtime.session.extensionRunner;
  const oldRead = oldRunner.getToolDefinition("read")!;
  const oldContext = oldRunner.createToolContext("test-call", undefined);
  await runtime.newSession();
  await runtime.session.bindExtensions({ mode: "rpc", uiContext: createHeadlessUIContext() });
  await expect(oldRead.execute("old", { path: "old.txt" }, undefined, undefined, oldContext)).rejects.toThrow();
  expect(h.resolveWorker).toHaveBeenCalledTimes(2);
  expect(h.bindMemory).toHaveBeenCalledTimes(2);
  const saved = SessionManager.create(h.workspace, join(h.root, "sessions"));
  saved.appendMessage({ role: "user", content: "saved fixture", timestamp: Date.now() });
  saved.appendMessage(fauxAssistantMessage("saved response"));
  await runtime.switchSession(saved.getSessionFile()!);
  await runtime.session.bindExtensions({ mode: "rpc", uiContext: createHeadlessUIContext() });
  expect(h.resolveWorker).toHaveBeenCalledTimes(3);
  expect(h.bindMemory).toHaveBeenCalledTimes(3);
  const runner = runtime.session.extensionRunner;
  await runner.getToolDefinition("write")!.execute("new", { path: "worker-only.txt", content: "fixture" },
    undefined, undefined, runner.createToolContext("test-call", undefined));
  await expect(readFile(join(h.workspace, "worker-only.txt"))).rejects.toMatchObject({ code: "ENOENT" });
}, 30_000);

it("rejects a mismatched or unavailable worker before enabling the protected runtime", async () => {
  const h = await harness();
  h.worker.workspace = join(h.root, "foreign");
  await expect(h.start()).rejects.toThrow("MEMORY_WORKER_WORKSPACE_MISMATCH");
  h.worker.workspace = h.workspace;
  h.worker.assertIsolated.mockRejectedValue(new Error("isolation unavailable"));
  await expect(h.start()).rejects.toThrow("isolation unavailable");
  expect(h.bindMemory).not.toHaveBeenCalled();
  expect(h.worker.execute).not.toHaveBeenCalled();
}, 30_000);

it("pins each existing session turn to current operator skills through Pi input and prompt hooks", async () => {
  const h = await harness();
  const { snapshotOperatorSkills } = await import("../operator-skill-snapshot.js");
  const installation = join(h.root, "installation"), source = join(h.root, "skills");
  await mkdir(installation); await mkdir(join(source, "example"), { recursive: true });
  await writeFile(join(source, "example/SKILL.md"), "---\nname: example\ndescription: A\n---\nA\n");
  const publication = await snapshotOperatorSkills(installation, [source], process.getuid!());
  Object.assign(h.profile, { operatorSkills: { current: publication.refresh, subscribe: publication.subscribe } });
  try {
    const runtime = await h.start();
    expect(runtime.session.resourceLoader.getSkills().skills[0]?.description).toBe("A");
    const runner = runtime.session.extensionRunner;
    const options = () => ({ cwd: h.workspace, selectedTools: [], skills: [], toolSnippets: {}, toolGuidelines: {},
      promptGuidelines: [], appendSystemPrompt: "", sections: {}, contextFiles: [] });
    const first = await runner.emitBeforeAgentStart({ messageType: "user", prompt: "test" }, options());
    expect(first.systemPromptOptions.skills[0]?.description).toBe("A");
    const getter = h.resolveWorker.mock.calls[0]?.[1] as (() => number) | undefined;
    const previous = getter?.();
    await writeFile(join(source, "example/SKILL.md"), "---\nname: example\ndescription: B\n---\nB\n");
    await publication.refresh();
    expect(getter?.()).toBe(previous);
    expect(await runner.emitToolCall({ type: "tool_call", toolName: "ask_user_question", toolCallId: "stale-native", input: {} }))
      .toMatchObject({ block: true, reason: expect.stringContaining("OPERATOR_SKILLS_CHANGED") });
    await runner.emitInput("next", undefined, "interactive");
    expect(runtime.session.resourceLoader.getSkills().skills[0]?.description).toBe("B");
    const next = await runner.emitBeforeAgentStart({ messageType: "user", prompt: "next" }, options());
    expect(next.systemPromptOptions.skills[0]?.description).toBe("B");
    expect(getter?.()).toBeGreaterThan(previous!);
    expect(await runner.emitToolCall({ type: "tool_call", toolName: "ask_user_question", toolCallId: "current-native", input: {} }))
      .toBeUndefined();
    await rm(join(source, "example"), { recursive: true });
    await publication.refresh();
    await runner.emitInput("next", undefined, "interactive");
    expect(runtime.session.resourceLoader.getSkills().skills).toEqual([]);
  } finally { await publication.cleanup(); }
});

it("releases operator resource subscriptions when session initialization fails", async () => {
  const h = await harness();
  const listeners = new Set<unknown>();
  Object.assign(h.profile, { operatorSkills: {
    current: async () => ({ revision: 1, paths: [] }),
    subscribe: (listener: unknown) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
  } });
  h.resolveWorker.mockRejectedValueOnce(new Error("SYNTHETIC_WORKER_UNAVAILABLE"));
  await expect(h.start()).rejects.toThrow("SYNTHETIC_WORKER_UNAVAILABLE");
  expect(listeners.size).toBe(0);
});


it("aborts an already running host tool when the administrator revision is revoked", async () => {
  const h = await harness();
  const listeners = new Set<(state: { revision: number; paths: string[] }) => void>();
  let revision = 1;
  Object.assign(h.profile, { operatorSkills: {
    current: async () => ({ revision, paths: [] }),
    subscribe: (listener: (state: { revision: number; paths: string[] }) => void) => {
      listeners.add(listener); return () => { listeners.delete(listener); };
    },
  } });
  const { askUserQuestionTool } = await import("../ask-user-question.js");
  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  let signal: AbortSignal | undefined;
  const provider = fauxProvider({ provider: "operator-native-revocation" });
  provider.setResponses([
    fauxAssistantMessage([fauxToolCall("ask_user_question", { question: "Hold synthetic native call", inputType: "text" })],
      { stopReason: "toolUse" }),
    fauxAssistantMessage("settled"),
  ]);
  h.modelRuntime.registerNativeProvider(provider.provider);
  await h.modelRuntime.setRuntimeApiKey(provider.provider.id, "synthetic-test-key");
  const runtime = await h.start({ model: provider.getModel(), thinkingLevel: "off", askUserQuestionTool: {
    ...askUserQuestionTool,
    execute: async (_id, _input, operationSignal) => {
      signal = operationSignal; entered();
      await new Promise<void>(resolve => operationSignal?.addEventListener("abort", () => resolve(), { once: true }));
      return { content: [{ type: "text", text: "cancelled synthetic call" }], details: {} };
    },
  } });
  const turn = runtime.session.prompt("Run the synthetic native tool");
  await started;
  revision++;
  for (const listener of listeners) listener({ revision, paths: [] });
  await turn;
  expect(signal?.aborted).toBe(true);
  expect(runtime.session.messages.some(message => message.role === "custom"
    && message.customType === "dano.operator-skills-changed")).toBe(true);
  expect(h.worker.execute).not.toHaveBeenCalled();
}, 10_000);
