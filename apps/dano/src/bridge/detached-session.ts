import {
  createAgentSessionRuntime,
  createAgentSessionFromServices,
  createAgentSessionServices,
  createEditToolDefinition,
  getAgentDir,
  createReadToolDefinition,
  createWriteToolDefinition,
  type AgentSessionRuntime,
  type CreateAgentSessionRuntimeFactory,
  type CreateAgentSessionFromServicesOptions,
  type CreateAgentSessionServicesOptions,
  type SessionManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { askUserQuestionTool } from "./ask-user-question.js";
import { danoVersionTool } from "./dano-version-tool.js";
import { configureDanoLlmResilience } from "./llm-resilience.js";
import type { CredentialBroker } from "./credential-broker.js";
import { wrapProviderBash } from "./provider-python.js";

function resolveHeimdallExtensionPath(): string {
  try {
    return createRequire(join(process.cwd(), "package.json")).resolve(
      "@josephyoung/pi-heimdall/extensions/heimdall.ts",
    );
  } catch {
    return fileURLToPath(
      import.meta.resolve("@josephyoung/pi-heimdall/extensions/heimdall.ts"),
    );
  }
}

const HEIMDALL_EXTENSION_PATH = resolveHeimdallExtensionPath();

export interface CreateDetachedAgentSessionOptions {
  model?: CreateAgentSessionFromServicesOptions["model"];
  thinkingLevel?: CreateAgentSessionFromServicesOptions["thinkingLevel"];
  modelRuntime?: CreateAgentSessionServicesOptions["modelRuntime"];
  settingsManager?: CreateAgentSessionServicesOptions["settingsManager"];
  askUserQuestionTool?: ToolDefinition;
  credentialBroker?: CredentialBroker;
  credentialBrokerScope?: string;
}

export interface CreateDetachedAgentSessionRuntimeResult {
  runtime: AgentSessionRuntime;
  disposeDanoLlmResilience(): void;
}

export async function createDetachedAgentSessionRuntime(
  cwd: string,
  sessionManager: SessionManager,
  options: CreateDetachedAgentSessionOptions = {},
): Promise<CreateDetachedAgentSessionRuntimeResult> {
  let disposeActiveDanoLlmResilience: (() => void) | undefined;
  let disposeCredentialBinding: (() => void) | undefined;
  let providerExecutionLifetime: AbortController | undefined;
  const createRuntime: CreateAgentSessionRuntimeFactory = async runtimeOptions => {
    providerExecutionLifetime?.abort();
    const lifetime = new AbortController();
    providerExecutionLifetime = lifetime;
    disposeCredentialBinding?.();
    disposeCredentialBinding = undefined;
    const services = await createAgentSessionServices({
      cwd: runtimeOptions.cwd,
      agentDir: runtimeOptions.agentDir,
      modelRuntime: options.modelRuntime,
      settingsManager: options.settingsManager,
      resourceLoaderOptions: {
        additionalExtensionPaths: [HEIMDALL_EXTENSION_PATH],
        extensionsOverride: loaded => {
          if (options.credentialBroker && options.credentialBrokerScope) {
            const heimdall = loaded.extensions.find(extension => extension.path === HEIMDALL_EXTENSION_PATH);
            const bash = heimdall?.tools.get("bash");
            if (!bash) throw new Error("Heimdall bash is required for provider Python requests");
            bash.definition = wrapProviderBash(bash.definition, {
              broker: options.credentialBroker,
              scope: options.credentialBrokerScope,
              cwd: runtimeOptions.cwd,
              signal: lifetime.signal,
            });
          }
          return loaded;
        },
      },
    });
    const result = await createAgentSessionFromServices({
      services,
      sessionManager: runtimeOptions.sessionManager,
      sessionStartEvent: runtimeOptions.sessionStartEvent,
      noTools: "builtin",
      model: options.model,
      thinkingLevel: options.thinkingLevel,
      customTools: [
        createReadToolDefinition(runtimeOptions.cwd, {
          autoResizeImages: services.settingsManager.getImageAutoResize(),
        }),
        createEditToolDefinition(runtimeOptions.cwd),
        createWriteToolDefinition(runtimeOptions.cwd),
        danoVersionTool,
        options.askUserQuestionTool ?? askUserQuestionTool,
        ...(options.credentialBroker && options.credentialBrokerScope
          ? [options.credentialBroker.createTool(options.credentialBrokerScope)]
          : []),
      ] as unknown as ToolDefinition[],
    });
    disposeCredentialBinding =
      options.credentialBroker && options.credentialBrokerScope
        ? options.credentialBroker.observe(
            options.credentialBrokerScope,
            result.session,
          )
        : undefined;
    disposeActiveDanoLlmResilience = configureDanoLlmResilience(
      services.settingsManager,
      result.session,
    );
    return {
      ...result,
      services,
      diagnostics: services.diagnostics,
    };
  };

  const runtime = await createAgentSessionRuntime(createRuntime, {
    cwd,
    agentDir: getAgentDir(),
    sessionManager,
  });
  runtime.setBeforeSessionInvalidate(() => {
    providerExecutionLifetime?.abort();
    disposeActiveDanoLlmResilience?.();
    disposeActiveDanoLlmResilience = undefined;
    disposeCredentialBinding?.();
    disposeCredentialBinding = undefined;
  });

  return {
    runtime,
    disposeDanoLlmResilience() {
      providerExecutionLifetime?.abort();
      disposeActiveDanoLlmResilience?.();
      disposeActiveDanoLlmResilience = undefined;
      disposeCredentialBinding?.();
      disposeCredentialBinding = undefined;
    },
  };
}
