<script lang="ts">
  import type { UserMemoryOperation, UserMemoryOperationPage } from "@dano/types/memory";
  import { Button } from "$lib/components/ui/button";
  import * as Alert from "$lib/components/ui/alert";
  import { t } from "../i18n";

  let { url }: { url: string } = $props();
  let items = $state<UserMemoryOperation[]>([]);
  let nextCursor = $state<string | null>(null);
  let loading = $state(false), error = $state(false);
  let refresh = $state(0);
  let generation = 0;
  let controller: AbortController | undefined;

  const phaseKeys = {
    queued: "memory.queued", session_unknown: "memory.uncertain", session_created: "memory.queued",
    message_unknown: "memory.uncertain", message_delivered: "memory.queued", commit_unknown: "memory.uncertain",
    processing: "memory.processing", ready: "memory.ready", failed: "memory.failed",
    blocked_by_pause: "memory.blockedByPause", blocked: "memory.blocked",
  } as const;

  async function load(target: string, current: number, signal: AbortSignal, cursor?: string) {
    loading = true; error = false;
    try {
      const response = await fetch(cursor ? `${target}?cursor=${encodeURIComponent(cursor)}` : target,
        { signal, cache: "no-store" });
      if (!response.ok) throw new Error();
      const page: UserMemoryOperationPage = await response.json();
      if (!page || !Array.isArray(page.items) || !(page.nextCursor === null || typeof page.nextCursor === "string")
        || page.items.some(item => !item || typeof item.id !== "string" || !Object.hasOwn(phaseKeys, item.phase)
          || typeof item.createdAt !== "string" || typeof item.updatedAt !== "string"
          || typeof item.source?.sessionId !== "string" || typeof item.source?.entryId !== "string"
          || typeof item.source?.branchId !== "string")) throw new Error();
      if (generation !== current || signal.aborted) return;
      const merged = new Map((cursor ? items : []).map(item => [item.id, item]));
      for (const item of page.items) merged.set(item.id, item);
      items = [...merged.values()]; nextCursor = page.nextCursor;
    } catch { if (generation === current && !signal.aborted) error = true; }
    finally { if (generation === current) loading = false; }
  }

  $effect(() => {
    const target = url; void refresh;
    const current = ++generation;
    items = []; nextCursor = null;
    controller = new AbortController();
    void load(target, current, controller.signal);
    return () => { generation++; controller?.abort(); };
  });
</script>

<section class="flex flex-col gap-3" aria-label={t("memory.deliveries")}>
  <div class="flex items-center justify-between gap-2">
    <h3 class="font-medium">{t("memory.deliveries")}</h3>
    <Button variant="outline" size="sm" disabled={loading} onclick={() => refresh++}>{t("memory.refresh")}</Button>
  </div>
  <p class="text-sm text-muted-foreground">{t("memory.receiptExplanation")}</p>
  {#if error}
    <Alert.Root variant="destructive"><Alert.Description>{t("memory.receiptsUnavailable")}</Alert.Description></Alert.Root>
  {:else if !loading && !items.length}
    <p class="text-sm">{t("memory.empty")}</p>
  {/if}
  <ul class="flex flex-col gap-3">
    {#each items as item (item.id)}
      <li class="rounded-md border p-3 text-sm">
        <p class="font-medium">{t(phaseKeys[item.phase])}</p>
        <p>{t("memory.createdAt")}: <time datetime={item.createdAt}>{new Date(item.createdAt).toLocaleString()}</time></p>
        <p>{t("memory.updatedAt")}: <time datetime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString()}</time></p>
        <p class="break-all text-muted-foreground">{t("memory.sourceSession")}: {item.source.sessionId}</p>
      </li>
    {/each}
  </ul>
  {#if loading}<p role="status">{t("memory.receiptsLoading")}</p>{/if}
  {#if nextCursor}
    <Button variant="outline" disabled={loading} onclick={() => {
      if (nextCursor && controller) void load(url, generation, controller.signal, nextCursor);
    }}>{t("memory.loadMore")}</Button>
  {/if}
</section>
