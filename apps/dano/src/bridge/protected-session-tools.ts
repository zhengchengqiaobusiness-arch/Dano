import type { UserMemoryControls } from "./user-memory-controls.js";
import type { MemoryInputCapture } from "./memory-user-provenance.js";
import type { CaptureProviderTaskFact } from "./memory-task-facts.js";
import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import {
  createIsolatedBashOperations,
  createIsolatedToolDefinitions,
  type IsolatedToolExecutor,
} from "@josephyoung/pi-openviking/worker-tools";
import { resolve } from "node:path";
import type { CredentialBroker } from "./credential-broker.js";
import { wrapProviderBash } from "./provider-python.js";
import { createWorkerOutputRedactor } from "./worker-output-redaction.js";

/** Trusted launcher inputs only. The resolver must enforce this backend's owner. */
export interface ProtectedSessionTools {
  readonly agentDir: string;
  /** Supervisor-owned private state, outside every tool workspace. */
  readonly memoryStateDirectory?: string;
  readonly memory?: UserMemoryControls;
  /** Chat may continue when memory initialization fails; account retirement may not. */
  readonly memoryRetirementBlocked?: true;
  readonly captureMemoryInput?: MemoryInputCapture;
  readonly captureTaskFact?: CaptureProviderTaskFact;
  readonly trustedSkillPaths: readonly string[];
  readonly providerPythonModuleDirectory?: string;
  resolveWorker(workspace: string): Promise<IsolatedToolExecutor>;
  /** Release this user runtime's workers after all its sessions have stopped. */
  dispose?(): Promise<void>;
  createMemoryExtension?(workspace: string, worker: IsolatedToolExecutor): ExtensionFactory;
}

export async function protectedSessionFactory(
  profile: ProtectedSessionTools,
  workspace: string,
  options: { signal: AbortSignal; credentialBroker?: CredentialBroker; credentialBrokerScope?: string },
): Promise<ExtensionFactory> {
  options.signal.throwIfAborted();
  if (options.credentialBroker && options.credentialBrokerScope && !profile.providerPythonModuleDirectory) {
    throw new Error("PROTECTED_PROVIDER_MODULES_REQUIRED");
  }
  const resolvedWorker = await profile.resolveWorker(workspace);
  if (resolve(resolvedWorker.workspace) !== resolve(workspace)) throw new Error("MEMORY_WORKER_WORKSPACE_MISMATCH");
  const worker: IsolatedToolExecutor = {
    workspace: resolvedWorker.workspace,
    async assertIsolated() {
      options.signal.throwIfAborted();
      await resolvedWorker.assertIsolated();
      options.signal.throwIfAborted();
    },
    execute(name, parameters, signal, onUpdate) {
      const combined = signal ? AbortSignal.any([signal, options.signal]) : options.signal;
      combined.throwIfAborted();
      return resolvedWorker.execute(name, parameters, combined, onUpdate);
    },
  };
  await worker.assertIsolated();
  const definitions = createIsolatedToolDefinitions(worker);
  const operations = createIsolatedBashOperations(worker);
  const memory = profile.createMemoryExtension?.(workspace, worker);
  return pi => {
    // The worker owns Heimdall. Loading its host copy would intercept user_bash
    // first and execute Shell in the credential-bearing host process.
    pi.on("project_trust", () => ({ trusted: "no", remember: false }));
    for (const definition of definitions) {
      const tool = definition.name === "bash" && options.credentialBroker && options.credentialBrokerScope
        ? wrapProviderBash(definition, { broker: options.credentialBroker,
            scope: options.credentialBrokerScope, cwd: workspace, signal: options.signal,
            moduleDirectory: profile.providerPythonModuleDirectory,
            captureTaskFact: profile.captureTaskFact,
            redactOutputFile: createWorkerOutputRedactor(worker) })
        : definition;
      pi.registerTool(tool);
    }
    pi.on("user_bash", event => {
      if (resolve(event.cwd) !== resolve(workspace)) throw new Error("MEMORY_WORKER_WORKSPACE_MISMATCH");
      return { operations };
    });
    memory?.(pi);
  };
}
