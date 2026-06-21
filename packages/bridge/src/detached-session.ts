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
import { askUserQuestionTool } from "./ask-user-question.js";
import { createCurlTool } from "./curl-tool.js";

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
  const services = await createAgentSessionServices({ cwd });
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
      askUserQuestionTool,
    ] as unknown as ToolDefinition[],
  });
}
