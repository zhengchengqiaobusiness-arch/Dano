import {
  createAgentSessionFromServices,
  createAgentSessionServices,
  createEditToolDefinition,
  createReadToolDefinition,
  createWriteToolDefinition,
  type CreateAgentSessionFromServicesOptions,
  type CreateAgentSessionResult,
  type SessionManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { askUserQuestionTool } from "./ask-user-question.js";
import { createCurlTool } from "./curl-tool.js";
import { danoVersionTool } from "./dano-version-tool.js";

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
  defaultModel?: { provider?: string; modelId?: string };
  defaultThinkingLevel?: CreateAgentSessionFromServicesOptions["thinkingLevel"];
}

export async function createDetachedAgentSession(
  cwd: string,
  sessionManager: SessionManager,
  options: CreateDetachedAgentSessionOptions = {},
): Promise<CreateAgentSessionResult> {
  const services = await createAgentSessionServices({
    cwd,
    resourceLoaderOptions: {
      additionalExtensionPaths: [HEIMDALL_EXTENSION_PATH],
    },
  });
  const defaultModel =
    options.defaultModel?.provider && options.defaultModel.modelId
      ? services.modelRegistry
          .getAvailable()
          .find(
            model =>
              model.provider === options.defaultModel?.provider &&
              model.id === options.defaultModel?.modelId,
          )
      : undefined;

  return createAgentSessionFromServices({
    services,
    sessionManager,
    noTools: "builtin",
    model: options.model ?? defaultModel,
    thinkingLevel: options.thinkingLevel ?? options.defaultThinkingLevel,
    customTools: [
      createReadToolDefinition(cwd, {
        autoResizeImages: services.settingsManager.getImageAutoResize(),
      }),
      createCurlTool(cwd),
      createEditToolDefinition(cwd),
      createWriteToolDefinition(cwd),
      danoVersionTool,
      askUserQuestionTool,
    ] as unknown as ToolDefinition[],
  });
}
