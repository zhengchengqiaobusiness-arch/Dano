import { existsSync, readFileSync, readdirSync } from "node:fs";
import * as piAi from "@earendil-works/pi-ai";
import * as piCodingAgent from "@earendil-works/pi-coding-agent";
import type {
  Api,
  AuthContext,
  CredentialStore,
  Model,
  Models,
  Provider,
} from "@earendil-works/pi-ai";
import type {
  AgentSession,
  ExtensionRuntime,
  RpcCommand,
  RpcExtensionUIRequest,
  RpcExtensionUIResponse,
  SettingsManager,
  ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

const PI_BASELINE_VERSION = "0.85.1";

type PiAiPublicContracts = {
  authContext: AuthContext;
  credentialStore: CredentialStore;
  model: Model<Api>;
  models: Pick<
    Models,
    "stream" | "complete" | "streamSimple" | "completeSimple"
  >;
  provider: Provider;
};

type PiCodingAgentPublicContracts = {
  agentSession: Pick<
    AgentSession,
    "model" | "thinkingLevel" | "pendingMessageCount" | "abortCompaction"
  >;
  extensionRuntime: ExtensionRuntime;
  rpcCommand: RpcCommand;
  rpcExtensionUIRequest: RpcExtensionUIRequest;
  rpcExtensionUIResponse: RpcExtensionUIResponse;
  settingsManager: Pick<
    SettingsManager,
    "getDefaultProvider" | "getDefaultModel" | "getDefaultThinkingLevel"
  >;
  toolDefinition: ToolDefinition;
};

function acceptPublicContracts(
  _piAi: PiAiPublicContracts,
  _piCodingAgent: PiCodingAgentPublicContracts,
): void {}

describe("Pi 0.85.1 public interface baseline", () => {
  it("pins both Pi packages to the verified release", () => {
    const packageJson = JSON.parse(
      readFileSync(new URL("../../../package.json", import.meta.url), "utf8"),
    ) as { dependencies?: Record<string, string> };

    expect(packageJson.dependencies).toMatchObject({
      "@earendil-works/pi-ai": PI_BASELINE_VERSION,
      "@earendil-works/pi-coding-agent": PI_BASELINE_VERSION,
    });
    expect(piCodingAgent.VERSION).toBe(PI_BASELINE_VERSION);
  });

  it("exposes the later migration capabilities from package roots", () => {
    expect(acceptPublicContracts).toEqual(expect.any(Function));
    expect(piCodingAgent).toMatchObject({
      AgentSession: expect.any(Function),
      AgentSessionRuntime: expect.any(Function),
      ModelRuntime: expect.any(Function),
      SessionManager: expect.any(Function),
      SettingsManager: expect.any(Function),
      createAgentSession: expect.any(Function),
      createAgentSessionRuntime: expect.any(Function),
      createCodingTools: expect.any(Function),
      defineTool: expect.any(Function),
      createExtensionRuntime: expect.any(Function),
      RpcClient: expect.any(Function),
      runRpcMode: expect.any(Function),
    });
    expect(piAi).toMatchObject({
      AssistantMessageEventStream: expect.any(Function),
      InMemoryCredentialStore: expect.any(Function),
      createModels: expect.any(Function),
      createProvider: expect.any(Function),
      defaultProviderAuthContext: expect.any(Function),
      lazyStream: expect.any(Function),
      retryAssistantCall: expect.any(Function),
    });
  });

  it("keeps Pi-owned session defaults and pending state out of Dano mirrors", () => {
    const bridgeDirectory = new URL("..", import.meta.url);
    const bridgeSources = readdirSync(bridgeDirectory)
      .filter(name => name.endsWith(".ts"))
      .map(name => ({
        name,
        source: readFileSync(new URL(name, bridgeDirectory), "utf8"),
      }));
    const allBridgeSources = bridgeSources.map(({ source }) => source).join("\n");
    const adapterSource = bridgeSources.find(
      ({ name }) => name === "bridge-rpc-adapter.ts",
    )?.source;
    const detachedSessionSource = bridgeSources.find(
      ({ name }) => name === "detached-session.ts",
    )?.source;
    const danoConfigSource = readFileSync(
      new URL("../dano-config.ts", import.meta.url),
      "utf8",
    );
    const backendSource = readFileSync(
      new URL("../../backend.ts", import.meta.url),
      "utf8",
    );
    const storedSessionStateSource = readFileSync(
      new URL("../stored-session-state.ts", import.meta.url),
      "utf8",
    );
    const productConfig = readFileSync(
      new URL("../../../../../dano.config.json", import.meta.url),
      "utf8",
    );
    const piRuntimeDefaults = JSON.parse(
      readFileSync(
        new URL(
          "../../../../../deploy/runtime-defaults/settings.json",
          import.meta.url,
        ),
        "utf8",
      ),
    ) as { compaction?: { enabled?: boolean } };

    for (const source of [danoConfigSource, productConfig]) {
      expect(source).not.toMatch(
        /\bdefaultProvider\b|\bdefaultModel\b|\bdefaultThinkingLevel\b|\bdefaultProjectTrust\b/,
      );
    }
    expect(existsSync(new URL("../default-model.ts", import.meta.url))).toBe(
      false,
    );
    expect(allBridgeSources).not.toMatch(
      /DEFAULT_MODEL_PER_PROVIDER|appendModelChange|appendThinkingLevelChange|session\.state/,
    );
    expect(allBridgeSources).not.toMatch(
      /resolveAgentSessionDefaults|initializeSessionManagerDefaults/,
    );
    expect(storedSessionStateSource).not.toMatch(/pendingMessageCount|queue_update/);

    // Queue editing/cancellation is the only #386 exception allowed to touch
    // Pi's private queue storage. Keep that exception inside its helper block.
    expect(adapterSource).toBeDefined();
    const queueCompatibilityStart = adapterSource!.indexOf(
      "function queuedAgentMessages(",
    );
    const queueCompatibilityEnd = adapterSource!.indexOf(
      "function describeMessage(",
    );
    expect(queueCompatibilityStart).toBeGreaterThanOrEqual(0);
    expect(queueCompatibilityEnd).toBeGreaterThan(queueCompatibilityStart);
    const adapterWithoutQueueCompatibility =
      adapterSource!.slice(0, queueCompatibilityStart) +
      adapterSource!.slice(queueCompatibilityEnd);
    const privateFieldAccess =
      /\.\s*_[A-Za-z]\w*|\[\s*["']_[A-Za-z]\w*["']\s*\]/;
    for (const { name, source } of bridgeSources) {
      expect(
        name === "bridge-rpc-adapter.ts"
          ? adapterWithoutQueueCompatibility
          : source,
      ).not.toMatch(privateFieldAccess);
    }
    expect(backendSource).not.toMatch(privateFieldAccess);
    expect(backendSource).toContain("session.pendingMessageCount");
    expect(backendSource).not.toContain('event.type === "queue_update"');
    expect(piRuntimeDefaults.compaction?.enabled).toBe(true);
    expect(detachedSessionSource).toBeDefined();
    expect(detachedSessionSource!).not.toContain(
      "setAutoCompactionEnabled",
    );
  });

  it("keeps Field Assist on the public ModelRuntime boundary", () => {
    const fieldAssistSource = readFileSync(
      new URL("../field-assist.ts", import.meta.url),
      "utf8",
    );

    expect(fieldAssistSource).toContain("modelRuntime.complete");
    expect(fieldAssistSource).not.toMatch(
      /\bcreateAgentSession\b|\bSessionManager\b|\bSettingsManager\b/,
    );
  });

  it("keeps session lifecycle on the public AgentSessionRuntime boundary", () => {
    const registrySource = readFileSync(
      new URL("../session-registry.ts", import.meta.url),
      "utf8",
    );
    const detachedSessionSource = readFileSync(
      new URL("../detached-session.ts", import.meta.url),
      "utf8",
    );
    const adapterSource = readFileSync(
      new URL("../bridge-rpc-adapter.ts", import.meta.url),
      "utf8",
    );
    expect(registrySource).toMatch(/\bAgentSessionRuntime\b/);
    expect(detachedSessionSource).toContain("createAgentSessionRuntime");
    expect(detachedSessionSource).not.toMatch(
      /export async function createDetachedAgentSession\b/,
    );
    expect(adapterSource).not.toMatch(
      /class SessionRuntime\b|\.createBranchedSession\(/,
    );
  });

  it("derives shared browser runtime protocol semantics from Pi package roots", () => {
    const protocolSource = readFileSync(
      new URL("../../../types/protocol.ts", import.meta.url),
      "utf8",
    );
    const liveSessionSource = readFileSync(
      new URL("../live-session.ts", import.meta.url),
      "utf8",
    );
    const adapterSource = readFileSync(
      new URL("../bridge-rpc-adapter.ts", import.meta.url),
      "utf8",
    );
    const projectorSource = readFileSync(
      new URL("../pi-protocol-projector.ts", import.meta.url),
      "utf8",
    );

    expect(protocolSource).toContain('from "@earendil-works/pi-ai"');
    expect(protocolSource).toContain(
      'from "@earendil-works/pi-coding-agent"',
    );
    expect(protocolSource).toMatch(/type PiRpcCommandPayload</);
    expect(protocolSource).toMatch(/type PiRpcResponseData</);
    expect(protocolSource).not.toMatch(/export interface RpcModel\b/);
    expect(protocolSource).toMatch(
      /export type RpcModel = Pick<PiModel<Api>, "id" \| "provider">/,
    );
    expect(protocolSource).not.toMatch(
      /export type RpcThinkingLevel\s*=\s*\n\s*\|/,
    );
    expect(protocolSource).toMatch(
      /export type RpcThinkingLevel = Exclude<\s*AgentSession\["thinkingLevel"\]/,
    );
    expect(protocolSource).not.toMatch(
      /export interface RpcAgent(?:Text|Thinking|Usage|User|Assistant|ToolResult)/,
    );
    expect(protocolSource).toContain(
      'type PiAgentMessage = PiAgentEndEvent["messages"][number]',
    );
    expect(protocolSource).toMatch(
      /export type RpcSessionState = Omit<\s*PiRpcSessionState/,
    );
    expect(protocolSource).toContain(
      'export type RpcBashResult = PiRpcResponseData<"bash">',
    );
    expect(protocolSource).toContain(
      "export type RpcExtensionUIRequest = PiRpcExtensionUIRequest",
    );
    expect(protocolSource).toContain(
      "export type RpcExtensionUIResponse = PiRpcExtensionUIResponse",
    );
    expect(adapterSource).not.toMatch(/type PiModel\s*=\s*\{/);
    expect(liveSessionSource).not.toMatch(
      /getAvailableModels\(\): Array<\{/,
    );
    expect(projectorSource).not.toMatch(/type PiModel\w*\s*=/);

    for (const command of [
      "prompt",
      "steer",
      "follow_up",
      "abort",
      "new_session",
      "get_state",
      "set_model",
      "cycle_model",
      "get_available_models",
      "set_thinking_level",
      "cycle_thinking_level",
      "set_steering_mode",
      "set_follow_up_mode",
      "compact",
      "set_auto_compaction",
      "set_auto_retry",
      "abort_retry",
      "bash",
      "abort_bash",
      "export_html",
      "set_session_name",
      "switch_session",
      "fork",
      "get_fork_messages",
      "get_last_assistant_text",
      "get_commands",
    ]) {
      expect(protocolSource).toContain(`PiRpcCommandPayload<"${command}">`);
    }

    for (const command of [
      "prompt",
      "steer",
      "follow_up",
      "abort",
      "set_thinking_level",
      "set_steering_mode",
      "set_follow_up_mode",
      "set_auto_compaction",
      "set_auto_retry",
      "abort_retry",
      "abort_bash",
      "export_html",
      "fork",
      "get_fork_messages",
      "get_last_assistant_text",
      "set_session_name",
    ]) {
      expect(protocolSource).toContain(
        `${command}: PiRpcResponseData<"${command}">`,
      );
    }
    for (const command of ["new_session", "switch_session"]) {
      expect(protocolSource).toContain(
        `${command}: PiRpcResponseData<"${command}"> &`,
      );
    }
    for (const command of [
      "cycle_model",
      "get_available_models",
      "cycle_thinking_level",
      "get_commands",
    ]) {
      expect(protocolSource).toContain(`PiRpcResponseData<"${command}">`);
    }
    expect(protocolSource).toContain(
      'type PiCompactionResult = PiRpcResponseData<"compact">',
    );
  });

  it("uses only declared Pi package-root imports in Dano production sources", () => {
    const sourceRoots = [
      new URL("../..", import.meta.url),
      new URL("../../../types", import.meta.url),
      new URL("../../../web/src", import.meta.url),
    ];
    const sourceFiles = sourceRoots.flatMap(root =>
      readdirSync(root, { recursive: true, withFileTypes: true })
        .filter(entry => entry.isFile() && /\.(?:ts|svelte)$/.test(entry.name))
        .map(entry => readFileSync(entry.parentPath + "/" + entry.name, "utf8")),
    );

    for (const source of sourceFiles) {
      expect(source).not.toMatch(
        /from\s+["']@earendil-works\/pi-(?:ai|coding-agent)\//,
      );
    }
  });
});
