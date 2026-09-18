import {
  ModelRuntime, SessionManager, SettingsManager,
  createAgentSessionServices, createAgentSessionFromServices,
  createReadToolDefinition, createWriteToolDefinition, createEditToolDefinition,
  createGrepToolDefinition, createFindToolDefinition, createLsToolDefinition,
  getAgentDir, type ToolDefinition, type ToolResultEvent,
} from "@earendil-works/pi-coding-agent";
import { InMemoryCredentialStore } from "@earendil-works/pi-ai";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { createHeadlessUIContext } from "./headless-ui-context.js";
import { assertWorkerProcessPrivacy } from "./linux-process-privacy.js";

/** Load only inside the isolated tool process, whose HOME/config is tool-only. */
export async function createWorkerTools({ workspace }: { workspace: string }) {
  await assertWorkerProcessPrivacy();
  // The no_new_privs worker cannot create a fresh devpts mount. Reuse the
  // container's devices and omit procfs inside each Shell sandbox so a new
  // proc mount cannot undo the launcher's cross-user process privacy policy.
  process.env.HEIMDALL_BWRAP_BIND_KERNEL_FS = "1";
  process.env.HEIMDALL_BWRAP_BIND_PROC = "0";
  process.env.HEIMDALL_BWRAP_BIND_ROOT = workspace;
  const settingsManager = SettingsManager.inMemory();
  settingsManager.setProjectTrusted(false);
  const modelRuntime = await ModelRuntime.create({ credentials: new InMemoryCredentialStore(),
    modelsPath: null, allowModelNetwork: false, refreshOnCreate: false });
  const services = await createAgentSessionServices({
    cwd: workspace, agentDir: getAgentDir(), settingsManager, modelRuntime,
    resourceLoaderOptions: {
      noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true,
      additionalExtensionPaths: [fileURLToPath(import.meta.resolve("@josephyoung/pi-heimdall/extensions/heimdall.ts"))],
    },
  });
  if (services.diagnostics.some(item => item.type === "error")) throw new Error("WORKER_GUARDS_UNAVAILABLE");
  const { session } = await createAgentSessionFromServices({ services,
    sessionManager: SessionManager.inMemory(workspace), noTools: "all" });
  let failed = false;
  let closed = false;
  const lifetime = new AbortController();
  try {
    await session.bindExtensions({ mode: "rpc", uiContext: createHeadlessUIContext(),
      onError: () => { failed = true; } });
    const runner = session.extensionRunner;
    const bash = runner.getToolDefinition("bash");
    if (failed || !bash || !runner.hasHandlers("tool_call") || !runner.hasHandlers("user_bash")) {
      throw new Error("WORKER_GUARDS_UNAVAILABLE");
    }
    const definitions = [createReadToolDefinition(workspace), createWriteToolDefinition(workspace),
      createEditToolDefinition(workspace), createGrepToolDefinition(workspace),
      createFindToolDefinition(workspace), createLsToolDefinition(workspace), bash] as unknown as ToolDefinition[];
    const tools = new Map(definitions.map(tool => [tool.name, tool]));
    const check = async () => {
      if (closed || failed) throw new Error("WORKER_GUARDS_UNAVAILABLE");
      await assertWorkerProcessPrivacy();
      if (closed || failed) throw new Error("WORKER_GUARDS_UNAVAILABLE");
    };
    return {
      async execute(name: string, parameters: Record<string, unknown>, signal: AbortSignal,
        onUpdate: (value: unknown) => void): Promise<unknown> {
        await check();
        const operationSignal = AbortSignal.any([signal, lifetime.signal]);
        operationSignal.throwIfAborted();
        if (name === "user_bash") {
          if (typeof parameters.command !== "string") throw new Error("INVALID_WORKER_COMMAND");
          const intercepted = await runner.emitUserBash({ type: "user_bash", command: parameters.command,
            cwd: workspace, excludeFromContext: true });
          await check();
          if (!intercepted?.operations) throw new Error("WORKER_SHELL_GUARD_UNAVAILABLE");
          operationSignal.throwIfAborted();
          return intercepted.operations.exec(parameters.command, workspace, {
            signal: operationSignal,
            timeout: typeof parameters.timeout === "number" ? parameters.timeout : undefined,
            onData: data => onUpdate({ data: data.toString("base64") }),
          });
        }
        const tool = tools.get(name);
        if (!tool) throw new Error("INVALID_WORKER_TOOL");
        const id = randomUUID();
        const decision = await runner.emitToolCall({ type: "tool_call", toolName: name, toolCallId: id, input: parameters });
        await check();
        if (decision?.block) throw new Error("WORKER_TOOL_BLOCKED");
        operationSignal.throwIfAborted();
        const result = await tool.execute(id, parameters, operationSignal, onUpdate, runner.createContext());
        const projection = await runner.emitToolResult({ type: "tool_result", toolName: name,
          toolCallId: id, input: parameters, content: result.content, details: result.details,
          isError: false } as ToolResultEvent);
        await check();
        return projection ? { ...result, ...projection } : result;
      },
      close() {
        closed = true;
        lifetime.abort();
        session.dispose();
      },
    };
  } catch {
    session.dispose();
    throw new Error("WORKER_GUARDS_UNAVAILABLE");
  }
}
