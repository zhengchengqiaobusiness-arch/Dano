import type { ConnectionStatus } from "../composables/bridgeStore.svelte";

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
