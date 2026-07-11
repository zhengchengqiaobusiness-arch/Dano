import type { ConnectionStatus } from "../composables/bridgeStore.svelte";
import { parseCompactSlashCommand } from "../utils/slashCommands";

export function canSubmitComposerMessage(input: {
  connectionStatus: ConnectionStatus;
  hasUploadingAttachments: boolean;
  hasFailedAttachments: boolean;
  hasText: boolean;
  hasSubmittableAttachments: boolean;
}): boolean {
  return (
    input.connectionStatus !== "connecting" &&
    !input.hasUploadingAttachments &&
    !input.hasFailedAttachments &&
    (input.hasText || input.hasSubmittableAttachments)
  );
}

export function shouldRejectCompactAttachments(input: {
  message: string;
  hasAttachments: boolean;
  slashCommandsEnabled: boolean;
}): boolean {
  return (
    input.hasAttachments &&
    parseCompactSlashCommand(input.message, input.slashCommandsEnabled) !== null
  );
}
