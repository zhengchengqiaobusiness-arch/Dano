<script lang="ts">
  import * as Alert from "$lib/components/ui/alert";
  import * as AlertDialog from "$lib/components/ui/alert-dialog";
  import { Button, buttonVariants } from "$lib/components/ui/button";
  import { Textarea } from "$lib/components/ui/textarea";
  import { t } from "../i18n";
  import { exportAllMemories, exportMemoryPage, governanceReview, pendingGovernance, startGovernance,
    submitGovernanceReview, type ExportPage, type GovernanceReceipt, type GovernanceReview } from "../utils/memoryGovernance";

  let { url, exportUrl }: { url: string; exportUrl: string } = $props();
  let pending = $state<GovernanceReceipt | null>(null);
  let review = $state<GovernanceReview | null>(null);
  let page = $state<ExportPage | null>(null);
  let activeUri = $state<string | null>(null);
  let selectedText = $state(""), replacementText = $state("");
  let mergedTexts = $state<Record<string, string>>({});
  let loading = $state(false), saving = $state(false), exporting = $state(false);
  let error = $state(false), ambiguous = $state(false);
  let refresh = $state(0), generation = 0;
  let exportController: AbortController | null = null;

  $effect(() => {
    const target = url, exportTarget = exportUrl;
    void refresh;
    const current = ++generation;
    const controller = new AbortController();
    loading = true; error = false; ambiguous = false;
    void (async () => {
      const job = await pendingGovernance(target, controller.signal);
      const [nextReview, nextPage] = job
        ? [job.errorCode === "MEMORY_GOVERNANCE_REVIEW_REQUIRED"
          ? await governanceReview(target, job.jobId, controller.signal) : null, null]
        : [null, await exportMemoryPage(exportTarget, controller.signal)];
      if (generation !== current || controller.signal.aborted) return;
      pending = job; review = nextReview; page = nextPage;
      if (!job && activeUri && !nextPage?.items.some(item => item.uri === activeUri)) activeUri = null;
    })().catch(() => { if (generation === current && !controller.signal.aborted) error = true; })
      .finally(() => { if (generation === current) loading = false; });
    const timer = setInterval(() => { if (pending && !saving) refresh++; }, 5000);
    return () => { generation++; controller.abort(); exportController?.abort(); clearInterval(timer); };
  });

  async function mutate(action: Parameters<typeof startGovernance>[2]) {
    if (saving || exporting) return;
    saving = true; error = false; ambiguous = false;
    try { pending = await startGovernance(url, new AbortController().signal, action); refresh++; }
    catch (cause) { if (cause instanceof Error && cause.message === "MEMORY_TARGET_AMBIGUOUS") ambiguous = true;
      else error = true; }
    finally { saving = false; }
  }

  async function decide(decision: Parameters<typeof submitGovernanceReview>[3]) {
    if (!pending || saving || exporting) return;
    saving = true; error = false;
    try {
      pending = await submitGovernanceReview(url, pending.jobId, new AbortController().signal, decision);
      if ("exactText" in decision) {
        const remaining = { ...mergedTexts }; delete remaining[decision.operationId]; mergedTexts = remaining;
      }
      refresh++;
    } catch { error = true; }
    finally { saving = false; }
  }

  async function downloadAll() {
    if (!page || loading || saving || exporting) return;
    const controller = new AbortController(), current = generation;
    exportController = controller; exporting = true; error = false;
    try {
      const complete = await exportAllMemories(exportUrl, controller.signal);
      if (controller.signal.aborted || current !== generation) return;
      const blob = new Blob([JSON.stringify(complete, null, 2)], { type: "application/json" });
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href; link.download = "dano-memory-export.json"; link.click();
      setTimeout(() => URL.revokeObjectURL(href), 0);
    } catch { if (!controller.signal.aborted) error = true; }
    finally { if (exportController === controller) { exportController = null; exporting = false; } }
  }

  async function loadMore() {
    const cursor = page?.nextCursor;
    if (!cursor || loading || exporting) return;
    loading = true; error = false;
    try {
      const next = await exportMemoryPage(exportUrl, new AbortController().signal, cursor);
      if (page?.nextCursor === cursor) page = { items: [...page.items, ...next.items], nextCursor: next.nextCursor };
    } catch { error = true; }
    finally { loading = false; }
  }
</script>

<section class="flex flex-col gap-3" aria-label={t("memory.governanceTitle")}>
  <div class="flex items-center justify-between gap-2">
    <h3 class="font-medium">{t("memory.governanceTitle")}</h3>
    <Button variant="outline" size="sm" disabled={loading || saving} onclick={() => refresh++}>{t("memory.refresh")}</Button>
  </div>
  {#if error}
    <Alert.Root variant="destructive"><Alert.Description>{t("memory.governanceUnavailable")}</Alert.Description></Alert.Root>
  {/if}
  {#if ambiguous}
    <Alert.Root variant="destructive"><Alert.Description>{t("memory.targetAmbiguous")}</Alert.Description></Alert.Root>
  {/if}
  {#if loading}<p role="status">{t("memory.loading")}</p>{/if}
  {#if exporting}<p role="status">{t("memory.exporting")}</p>{/if}
  {#if pending}
    <Alert.Root><Alert.Description>
      {t("memory.governancePending")}{#if pending.errorCode} ({pending.errorCode}){/if}
    </Alert.Description></Alert.Root>
    {#if pending.errorCode === "MEMORY_GOVERNANCE_CLEAR_REQUIRED"}
      <p class="text-sm">{t("memory.clearRequired")}</p>
    {/if}
    {#if review?.stage === "classify"}
      <p>{t("memory.reviewPrompt")}</p>
      {#each review.candidates as candidate (candidate.operationId)}
        <div class="flex flex-col gap-2 rounded-md border p-3">
          <pre class="whitespace-pre-wrap break-words text-sm">{candidate.candidateText}</pre>
          <div class="flex flex-wrap gap-2">
            <Button disabled={saving || exporting} onclick={() => decide({ operationId: candidate.operationId, decision: "target" })}>{t("memory.reviewTarget")}</Button>
            <Button variant="outline" disabled={saving || exporting} onclick={() => decide({ operationId: candidate.operationId, decision: "unrelated" })}>{t("memory.reviewUnrelated")}</Button>
          </div>
        </div>
      {/each}
    {:else if review?.stage === "merged"}
      <p>{t("memory.mergedPrompt")}</p>
      {#each review.candidates as candidate (candidate.operationId)}
        <div class="flex flex-col gap-2 rounded-md border p-3">
          <p class="text-sm">{t("memory.candidateFact")}</p>
          <pre class="whitespace-pre-wrap break-words text-sm">{candidate.candidateText}</pre>
          <p class="text-sm">{t("memory.currentDocument")}</p>
          <pre class="max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-sm">{candidate.documentText}</pre>
          <label for={`merged-memory-text-${candidate.operationId}`}>{t("memory.mergedExactText")}</label>
          <Textarea id={`merged-memory-text-${candidate.operationId}`} value={mergedTexts[candidate.operationId] ?? ""}
            oninput={event => { mergedTexts[candidate.operationId] = event.currentTarget.value; }} />
          <Button disabled={saving || exporting || !mergedTexts[candidate.operationId]?.trim()}
            onclick={() => decide({ operationId: candidate.operationId, exactText: mergedTexts[candidate.operationId] })}>{t("memory.removeReviewedText")}</Button>
        </div>
      {/each}
    {/if}
  {:else if page}
    <p class="text-sm text-muted-foreground">{t("memory.governanceExplanation")}</p>
    <Button variant="outline" size="sm" disabled={loading || saving || exporting} onclick={downloadAll}>{t("memory.exportPage")}</Button>
    {#each page.items as item (item.uri)}
      <div class="flex flex-col gap-2 rounded-md border p-3">
        <p class="break-all text-sm text-muted-foreground">{item.uri}</p>
        <pre class="max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-sm">{item.content}</pre>
        {#if item.sources.length}
          <div class="space-y-1 text-sm text-muted-foreground">
            <p>{t("memory.sources")}</p>
            {#each item.sources as source (`${source.sessionId}:${source.entryId}`)}
              <p>{t(source.kind === "automatic" ? "memory.sourceAutomatic" : "memory.sourceExplicit")}
                · {t(source.status === "revoked" ? "memory.sourceRevoked"
                  : source.status === "preserved" ? "memory.sourcePreserved" : "memory.sourceCurrent")}
                · <time datetime={source.createdAt}>{new Date(source.createdAt).toLocaleString()}</time></p>
              <p class="break-all">{t("memory.sourceSession")}: {source.sessionId}</p>
            {/each}
          </div>
        {:else}
          <p class="text-sm text-muted-foreground">{t("memory.noSources")}</p>
        {/if}
        <Button variant="outline" size="sm" onclick={() => {
          activeUri = activeUri === item.uri ? null : item.uri; selectedText = ""; replacementText = "";
        }}>{activeUri === item.uri ? t("memory.hideContent") : t("memory.manageItem")}</Button>
        {#if activeUri === item.uri}
          <label for="selected-memory-text">{t("memory.selectedExactText")}</label>
          <Textarea id="selected-memory-text" bind:value={selectedText} />
          <label for="replacement-memory-text">{t("memory.replacementText")}</label>
          <Textarea id="replacement-memory-text" bind:value={replacementText} />
          <div class="flex flex-wrap gap-2">
            <Button disabled={saving || exporting || !selectedText.trim() || !replacementText.trim()}
              onclick={() => mutate({ action: "correct", memoryUri: item.uri, selectedText, replacementText })}>{t("memory.correct")}</Button>
            <Button variant="destructive" disabled={saving || exporting || !selectedText.trim()}
              onclick={() => mutate({ action: "forget", memoryUri: item.uri, selectedText })}>{t("memory.forget")}</Button>
          </div>
        {/if}
      </div>
    {/each}
    {#if page.nextCursor}<Button variant="outline" disabled={loading || exporting} onclick={loadMore}>{t("memory.morePages")}</Button>{/if}
  {/if}
  {#if pending?.errorCode === "MEMORY_GOVERNANCE_CLEAR_REQUIRED" || (!pending && page)}
    <AlertDialog.Root>
      <AlertDialog.Trigger disabled={saving || exporting || loading} class={buttonVariants({ variant: "destructive" })}>{t("memory.clearAll")}</AlertDialog.Trigger>
      <AlertDialog.Content>
        <AlertDialog.Header>
          <AlertDialog.Title>{t("memory.clearTitle")}</AlertDialog.Title>
          <AlertDialog.Description>{t("memory.clearDescription")}</AlertDialog.Description>
        </AlertDialog.Header>
        <AlertDialog.Footer>
          <AlertDialog.Cancel>{t("memory.cancel")}</AlertDialog.Cancel>
          <AlertDialog.Action variant="destructive" disabled={saving || exporting} onclick={() => mutate({ action: "clear", confirmed: true })}>{t("memory.confirmClear")}</AlertDialog.Action>
        </AlertDialog.Footer>
      </AlertDialog.Content>
    </AlertDialog.Root>
  {/if}
</section>
