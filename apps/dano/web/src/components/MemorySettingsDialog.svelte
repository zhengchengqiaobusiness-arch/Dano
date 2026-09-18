<script lang="ts">
  import type { UserMemoryStatus } from "@dano/types/memory";
  import * as Dialog from "$lib/components/ui/dialog";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import { t } from "../i18n";
  import { requestMemorySettings } from "../utils/memorySettings";

  let { open = false, authenticated = false, url = null, themeStyle = "", onClose = () => {} }:
    { open?: boolean; authenticated?: boolean; url?: string | null; themeStyle?: string; onClose?: () => void } = $props();
  let status = $state<UserMemoryStatus | null>(null);
  let loading = $state(false), saving = $state(false), error = $state(false);
  let reload = $state(0);
  let generation = 0;
  let saveController: AbortController | undefined;

  $effect(() => {
    const target = url;
    const active = open && authenticated && target;
    void reload;
    const current = ++generation;
    status = null; error = false; saving = false;
    loading = Boolean(active);
    if (!active) return;
    const controller = new AbortController();
    void requestMemorySettings(target!, controller.signal).then(value => {
      if (generation === current) status = value;
    }).catch(() => { if (generation === current && !controller.signal.aborted) error = true; })
      .finally(() => { if (generation === current) loading = false; });
    return () => { generation++; controller.abort(); saveController?.abort(); };
  });

  async function changeEnabled(enabled: boolean) {
    if (!url || !status || saving) return;
    const current = generation;
    saving = true; error = false;
    const controller = new AbortController(); saveController = controller;
    try {
      const result = await requestMemorySettings(url, controller.signal, enabled);
      if (generation === current) status = result;
    } catch {
      if (generation === current && !controller.signal.aborted) { status = null; error = true; }
    } finally { if (generation === current) saving = false; }
  }
</script>

<Dialog.Root {open} onOpenChange={(value) => { if (!value) onClose(); }}>
  <Dialog.Content style={themeStyle} overlayProps={{ style: themeStyle }}>
    <Dialog.Header>
      <Dialog.Title>{t("memory.title")}</Dialog.Title>
      <Dialog.Description>{t("memory.description")}</Dialog.Description>
    </Dialog.Header>
    {#if !authenticated}
      <Alert.Root><Alert.Description>{t("memory.loginRequired")}</Alert.Description></Alert.Root>
    {:else if !url}
      <Alert.Root><Alert.Description>{t("memory.disconnected")}</Alert.Description></Alert.Root>
    {:else if loading}
      <p role="status">{t("memory.loading")}</p>
    {:else if error}
      <Alert.Root variant="destructive"><Alert.Description>{t("memory.unavailable")}</Alert.Description></Alert.Root>
      <Button variant="outline" onclick={() => reload++}>{t("memory.refresh")}</Button>
    {:else if status}
      <div class="flex flex-col gap-3" aria-live="polite">
        <p>{status.enabled ? t("memory.enabled") : t("memory.disabled")}</p>
        <p>{t("memory.consent")}</p>
        <p>{t("memory.pauseDescription")}</p>
      </div>
      <Dialog.Footer>
        <Button disabled={saving} onclick={() => changeEnabled(!status!.enabled)}>
          {saving ? t("memory.saving") : status.enabled ? t("memory.pause") : t("memory.enable")}
        </Button>
      </Dialog.Footer>
    {/if}
  </Dialog.Content>
</Dialog.Root>
