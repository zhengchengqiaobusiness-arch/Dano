<script lang="ts">
  import type { UserMemoryContent } from "@dano/types/memory";
  import { Button } from "$lib/components/ui/button";
  import * as Alert from "$lib/components/ui/alert";
  import { t } from "../i18n";

  let { url, operationId }: { url: string; operationId: string } = $props();
  let index = $state(0), refresh = $state(0);
  let content = $state<UserMemoryContent | null>(null);
  let total = $state(0);
  let error = $state(false), loading = $state(false);
  let generation = 0;
  $effect(() => {
    const target = url, id = operationId, currentIndex = index; void refresh;
    const current = ++generation;
    const controller = new AbortController();
    content = null; error = false; loading = true;
    void fetch(`${target}/${encodeURIComponent(id)}/content/${currentIndex}`, { signal: controller.signal, cache: "no-store" })
      .then(async response => {
        if (!response.ok) throw new Error();
        const value: UserMemoryContent = await response.json();
        if (!value || value.operationId !== id || value.index !== currentIndex
          || !Number.isSafeInteger(value.total) || value.total <= currentIndex || typeof value.text !== "string") throw new Error();
        if (generation === current && !controller.signal.aborted) { content = value; total = value.total; }
      }).catch(() => { if (generation === current && !controller.signal.aborted) error = true; })
      .finally(() => { if (generation === current) loading = false; });
    return () => { generation++; controller.abort(); };
  });
</script>

<section class="mt-3 flex flex-col gap-2" aria-label={t("memory.content")}>
  {#if loading}<p role="status">{t("memory.contentLoading")}</p>{/if}
  {#if error}
    <Alert.Root variant="destructive"><Alert.Description>{t("memory.contentUnavailable")}</Alert.Description></Alert.Root>
    <Button variant="outline" size="sm" onclick={() => refresh++}>{t("memory.refresh")}</Button>
  {/if}
  {#if content}
    <p class="whitespace-pre-wrap break-words">{content.text}</p>
  {/if}
  {#if total > 0}
    <div class="flex items-center gap-2">
      <Button variant="outline" size="sm" disabled={loading || index === 0} onclick={() => index--}>{t("memory.previous")}</Button>
      <span>{index + 1} / {total}</span>
      <Button variant="outline" size="sm" disabled={loading || index + 1 >= total} onclick={() => index++}>{t("memory.next")}</Button>
    </div>
  {/if}
</section>
