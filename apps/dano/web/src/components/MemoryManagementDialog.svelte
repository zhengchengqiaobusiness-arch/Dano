<script lang="ts">
  import * as Dialog from "$lib/components/ui/dialog";
  import * as Alert from "$lib/components/ui/alert";
  import { Separator } from "$lib/components/ui/separator";
  import { t } from "../i18n";
  import MemoryOperations from "./MemoryOperations.svelte";
  import MemoryGovernance from "./MemoryGovernance.svelte";

  let { open = false, authenticated = false, operationsUrl = null,
    governanceUrl = null, exportUrl = null, themeStyle = "", onClose = () => {} }:
    { open?: boolean; authenticated?: boolean; operationsUrl?: string | null;
      governanceUrl?: string | null; exportUrl?: string | null; themeStyle?: string; onClose?: () => void } = $props();
</script>

<Dialog.Root {open} onOpenChange={(value) => { if (!value) onClose(); }}>
  <Dialog.Content class="max-h-[85dvh] overflow-y-auto" style={themeStyle} overlayProps={{ style: themeStyle }}>
    <Dialog.Header>
      <Dialog.Title>{t("memory.title")}</Dialog.Title>
      <Dialog.Description>{t("memory.description")}</Dialog.Description>
    </Dialog.Header>
    {#if !authenticated}
      <Alert.Root><Alert.Description>{t("memory.loginRequired")}</Alert.Description></Alert.Root>
    {:else if !operationsUrl || !governanceUrl || !exportUrl}
      <Alert.Root><Alert.Description>{t("memory.disconnected")}</Alert.Description></Alert.Root>
    {:else if open}
      <MemoryOperations url={operationsUrl} />
      <Separator />
      <MemoryGovernance url={governanceUrl} {exportUrl} />
    {/if}
  </Dialog.Content>
</Dialog.Root>
