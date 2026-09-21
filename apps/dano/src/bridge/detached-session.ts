import {
  createAgentSessionRuntime,
  createAgentSessionFromServices,
  createAgentSessionServices,
  createEditToolDefinition,
  getAgentDir,
  createReadToolDefinition,
  createWriteToolDefinition,
  SettingsManager,
  ModelRuntime,
  type AgentSessionRuntime,
  type CreateAgentSessionRuntimeFactory,
  type CreateAgentSessionFromServicesOptions,
  type CreateAgentSessionServicesOptions,
  type SessionManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { askUserQuestionTool } from "./ask-user-question.js";
import { danoVersionTool } from "./dano-version-tool.js";
import { configureDanoLlmResilience } from "./llm-resilience.js";
import type { CredentialBroker } from "./credential-broker.js";
import { wrapProviderBash } from "./provider-python.js";
import { protectedMemoryResources } from "@josephyoung/pi-openviking/host";
import { protectedSessionFactory, type ProtectedSessionTools } from "./protected-session-tools.js";

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
  protectedTools?: ProtectedSessionTools;
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
    const protectedProfile = options.protectedTools;
    const hostSystemPromptPath = join(getAgentDir(), "SYSTEM.md");
    const protectedSettings = protectedProfile
      ? options.settingsManager ?? SettingsManager.create(runtimeOptions.cwd, getAgentDir(), { projectTrusted: false })
      : undefined;
    const protectedResources = protectedProfile && protectedSettings
      ? protectedMemoryResources(protectedSettings,
          await protectedSessionFactory(protectedProfile, runtimeOptions.cwd, {
            signal: lifetime.signal,
            credentialBroker: options.credentialBroker,
            credentialBrokerScope: options.credentialBrokerScope,
          }), protectedProfile.trustedSkillPaths)
      : undefined;
    const services = await createAgentSessionServices({
      cwd: runtimeOptions.cwd,
      agentDir: runtimeOptions.agentDir,
      // User resource directories must not become model credential stores.
      // Resolve deployment model configuration in the trusted host only.
      modelRuntime: options.modelRuntime ?? (protectedProfile
        ? await ModelRuntime.create({
            authPath: join(getAgentDir(), "auth.json"),
            modelsPath: join(getAgentDir(), "models.json"),
            signal: lifetime.signal,
          })
        : undefined),
      settingsManager: protectedSettings ?? options.settingsManager,
      resourceLoaderOptions: protectedResources ? {
        ...protectedResources,
        // Keep the deployment prompt in the trusted host configuration. A
        // user's isolated resource directory must not replace the host prompt.
        systemPromptOverride: () => {
          try { return readFileSync(hostSystemPromptPath, "utf8"); }
          catch (error) {
            if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
            throw error;
          }
        },
      } : {
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
        ...(protectedProfile ? [] : [createReadToolDefinition(runtimeOptions.cwd, {
          autoResizeImages: services.settingsManager.getImageAutoResize(),
        }),
        createEditToolDefinition(runtimeOptions.cwd),
        createWriteToolDefinition(runtimeOptions.cwd)]),
        danoVersionTool,
        options.askUserQuestionTool ?? askUserQuestionTool,
        ...(options.credentialBroker && options.credentialBrokerScope
          ? [options.credentialBroker.createTool(options.credentialBrokerScope, protectedProfile?.captureTaskFact)]
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
    agentDir: options.protectedTools?.agentDir ?? getAgentDir(),
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
