import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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
  const root = await mkdtemp(join(tmpdir(), "dano-protected-session-"));
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
  const resolveWorker = vi.fn(async () => worker);
  const bindMemory = vi.fn();
  const trustedSkillPaths: string[] = [];
  const profile: ProtectedSessionTools = { agentDir, trustedSkillPaths, resolveWorker,
    createMemoryExtension: () => pi => { bindMemory(); pi.on("context", () => {}); } };
  return { root, workspace, worker, resolveWorker, bindMemory, profile, modelRuntime, settingsManager, trustedSkillPaths,
    async start(options: Pick<CreateDetachedAgentSessionOptions, "model" | "thinkingLevel"> = {}) {
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
  expect(await read.execute("read", { path: "missing-on-host.txt" }, undefined, undefined, runner.createContext()))
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
      undefined, undefined, runner.createContext());
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
  const oldContext = oldRunner.createContext();
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
    undefined, undefined, runner.createContext());
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
