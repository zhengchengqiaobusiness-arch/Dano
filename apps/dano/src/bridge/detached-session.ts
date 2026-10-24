import {
  OPERATOR_SKILL_REVOCATION_NOTICE_TYPE,
  OPERATOR_SKILL_REVOCATION_NOTICE_TEXT,
} from "./operator-skill-notice.js";
import { operatorSkillResources } from "./operator-skill-resources.js";
import type { BridgeAuthenticationState } from "../../types/protocol.js";
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
import { createAssistantAuthenticationExtension, type CredentialBroker } from "./credential-broker.js";
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
  authenticationStatus?: BridgeAuthenticationState["status"];
  protectedTools?: ProtectedSessionTools;
  scheduleTaskTool?: ToolDefinition;
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
  let disposeOperatorSkills: (() => void) | undefined;
  let providerExecutionLifetime: AbortController | undefined;
  const createRuntime: CreateAgentSessionRuntimeFactory = async runtimeOptions => {
    providerExecutionLifetime?.abort();
    disposeOperatorSkills?.();
    const lifetime = new AbortController();
    providerExecutionLifetime = lifetime;
    disposeCredentialBinding?.();
    disposeCredentialBinding = undefined;
    const protectedProfile = options.protectedTools;
    const hostSystemPromptPath = join(getAgentDir(), "SYSTEM.md");
    const protectedSettings = protectedProfile
      ? options.settingsManager ?? SettingsManager.create(runtimeOptions.cwd, getAgentDir(), { projectTrusted: false })
      : undefined;
    let revocationNotified = false;
    let activeSession: import("@earendil-works/pi-coding-agent").AgentSession | undefined;
    const skillResources = protectedProfile?.operatorSkills
      ? await operatorSkillResources(protectedProfile.operatorSkills, runtimeOptions.cwd, protectedProfile.agentDir, () => {
          if (activeSession && !activeSession.isIdle && !revocationNotified) {
            revocationNotified = true;
            void activeSession.sendCustomMessage({ customType: OPERATOR_SKILL_REVOCATION_NOTICE_TYPE, display: true,
              content: OPERATOR_SKILL_REVOCATION_NOTICE_TEXT },
            { triggerTurn: false }).catch(() => {});
            void activeSession.abort().catch(() => {});
          }
        }) : undefined;
    disposeOperatorSkills = skillResources?.dispose;
    let skillRevision: number | undefined;
    const protectedResources = protectedProfile && protectedSettings
      ? protectedMemoryResources(protectedSettings,
          await protectedSessionFactory(protectedProfile, runtimeOptions.cwd, {
            signal: lifetime.signal,
            skillRevision: () => skillRevision,
            credentialBroker: options.credentialBroker,
            credentialBrokerScope: options.credentialBrokerScope,
          }), skillResources ? [] : protectedProfile.trustedSkillPaths)
      : undefined;
    const extensionFactories = [
      ...(protectedResources?.extensionFactories ?? []),
      ...(skillResources ? [{ name: "dano-operator-skills", factory: ((pi) => {
        pi.on("input", async () => { await skillResources.refresh(); return { action: "continue" as const }; });
        pi.on("tool_call", async () => {
          if (skillRevision !== await skillResources.refresh()) return { block: true,
            reason: "OPERATOR_SKILLS_CHANGED: this turn was invalidated by an administrator skill update. Start a new turn." };
        });
        pi.on("before_agent_start", async event => {
          skillRevision = await skillResources.refresh();
          revocationNotified = false;
          event.systemPromptOptions.skills = skillResources.getSkills().skills;
        });
      }) satisfies import("@earendil-works/pi-coding-agent").ExtensionFactory }] : []),
      { name: "dano-authentication", factory: options.credentialBroker && options.credentialBrokerScope
        ? options.credentialBroker.createAuthenticationExtension(options.credentialBrokerScope)
        : createAssistantAuthenticationExtension(() => options.authenticationStatus ?? "unknown") },
    ];
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
        extensionFactories,
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
        extensionFactories,
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
    if (skillResources) services.resourceLoader.getSkills = skillResources.getSkills;
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
        ...(options.scheduleTaskTool ? [options.scheduleTaskTool] : []),
        ...(options.credentialBroker && options.credentialBrokerScope
          ? [options.credentialBroker.createTool(options.credentialBrokerScope, protectedProfile?.captureTaskFact)]
          : []),
      ] as unknown as ToolDefinition[],
    });
    activeSession = result.session;
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

  const runtime = await createAgentSessionRuntime(async runtimeOptions => {
    try { return await createRuntime(runtimeOptions); }
    catch (error) {
      providerExecutionLifetime?.abort();
      disposeOperatorSkills?.();
      disposeOperatorSkills = undefined;
      throw error;
    }
  }, {
    cwd,
    agentDir: options.protectedTools?.agentDir ?? getAgentDir(),
    sessionManager,
  });
  runtime.setBeforeSessionInvalidate(() => {
    providerExecutionLifetime?.abort();
    disposeOperatorSkills?.();
    disposeActiveDanoLlmResilience?.();
    disposeActiveDanoLlmResilience = undefined;
    disposeCredentialBinding?.();
    disposeCredentialBinding = undefined;
  });

  return {
    runtime,
    disposeDanoLlmResilience() {
      providerExecutionLifetime?.abort();
      disposeOperatorSkills?.();
      disposeActiveDanoLlmResilience?.();
      disposeActiveDanoLlmResilience = undefined;
      disposeCredentialBinding?.();
      disposeCredentialBinding = undefined;
    },
  };
}
