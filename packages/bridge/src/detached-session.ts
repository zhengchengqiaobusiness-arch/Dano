import {
  createAgentSessionFromServices,
  createAgentSessionServices,
  createBashToolDefinition,
  createEditToolDefinition,
  createReadToolDefinition,
  createWriteToolDefinition,
  type CreateAgentSessionFromServicesOptions,
  type CreateAgentSessionResult,
  type SessionManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { buildWorkspaceActivationPrefix } from "./workspace-environment.js";
import { askUserQuestionTool } from "./ask-user-question.js";

export interface CreateDetachedAgentSessionOptions {
  model?: CreateAgentSessionFromServicesOptions["model"];
  thinkingLevel?: CreateAgentSessionFromServicesOptions["thinkingLevel"];
  defaultModel?: { provider?: string; modelId?: string };
  defaultThinkingLevel?: CreateAgentSessionFromServicesOptions["thinkingLevel"];
}

export function buildDetachedShellCommandPrefix(
  cwd: string,
  basePrefix?: string,
): string | undefined {
  const prefixes = [
    buildWorkspaceActivationPrefix(cwd),
    basePrefix?.trim(),
  ].filter((value): value is string => Boolean(value));

  return prefixes.length > 0 ? prefixes.join("\n") : undefined;
}

export async function createDetachedAgentSession(
  cwd: string,
  sessionManager: SessionManager,
  options: CreateDetachedAgentSessionOptions = {},
): Promise<CreateAgentSessionResult> {
  const services = await createAgentSessionServices({ cwd });
  const shellCommandPrefix = buildDetachedShellCommandPrefix(
    cwd,
    services.settingsManager.getShellCommandPrefix(),
  );
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
    model: options.model ?? defaultModel,
    thinkingLevel: options.thinkingLevel ?? options.defaultThinkingLevel,
    customTools: [
      createReadToolDefinition(cwd, {
        autoResizeImages: services.settingsManager.getImageAutoResize(),
      }),
      createBashToolDefinition(cwd, {
        commandPrefix: shellCommandPrefix,
      }),
      createEditToolDefinition(cwd),
      createWriteToolDefinition(cwd),
      askUserQuestionTool,
    ] as unknown as ToolDefinition[],
  });
}
