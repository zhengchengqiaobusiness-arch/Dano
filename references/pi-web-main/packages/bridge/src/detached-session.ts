import {
  createAgentSessionFromServices,
  createAgentSessionServices,
  createBashToolDefinition,
  createEditToolDefinition,
  createReadToolDefinition,
  createWriteToolDefinition,
  type CreateAgentSessionResult,
  type SessionManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { buildWorkspaceActivationPrefix } from "./workspace-environment.js";

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
): Promise<CreateAgentSessionResult> {
  const services = await createAgentSessionServices({ cwd });
  const shellCommandPrefix = buildDetachedShellCommandPrefix(
    cwd,
    services.settingsManager.getShellCommandPrefix(),
  );

  return createAgentSessionFromServices({
    services,
    sessionManager,
    customTools: [
      createReadToolDefinition(cwd, {
        autoResizeImages: services.settingsManager.getImageAutoResize(),
      }),
      createBashToolDefinition(cwd, {
        commandPrefix: shellCommandPrefix,
      }),
      createEditToolDefinition(cwd),
      createWriteToolDefinition(cwd),
    ] as unknown as ToolDefinition[],
  });
}
