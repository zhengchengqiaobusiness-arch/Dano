<script lang="ts">
  import type {
    RpcSessionStats,
    RpcWorkspaceEnvironment,
  } from "@pi-web/bridge/types";

  const statsGap = 8;
  const statsSlack = 4;

  let {
    stats = null as RpcSessionStats | null,
    workspaceEnvironments = [] as RpcWorkspaceEnvironment[],
  } = $props();

  let statsLeadingEl = $state<HTMLDivElement | null>(null);
  let statsInnerWidth = $state(0);
  let statsLeadingScrollWidth = $state(0);
  let fullStatsWidth = $state(0);
  let compactStatsWidth = $state(0);

  function compactTokens(count: number) {
    if (count < 1_000) return `${count}`;
    if (count < 10_000) return `${(count / 1_000).toFixed(1)}k`;
    if (count < 1_000_000) return `${Math.round(count / 1_000)}k`;
    if (count < 10_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
    return `${Math.round(count / 1_000_000)}M`;
  }

  function formatTokens(count: number) {
    return count.toLocaleString("en-US");
  }

  let contextPercent = $derived(
    stats?.percent != null ? Math.min(stats.percent, 100) : null,
  );
  let windowLabel = $derived(
    stats ? compactTokens(stats.contextWindow) : null,
  );
  let costLabel = $derived(
    stats && stats.cost > 0 ? `$${stats.cost.toFixed(3)}` : null,
  );
  let inputLabel = $derived(
    stats && stats.inputTokens > 0 ? `↑${compactTokens(stats.inputTokens)}` : null,
  );
  let inputTitle = $derived(
    stats && stats.inputTokens > 0
      ? `Input tokens: ${formatTokens(stats.inputTokens)}`
      : null,
  );
  let outputLabel = $derived(
    stats && stats.outputTokens > 0 ? `↓${compactTokens(stats.outputTokens)}` : null,
  );
  let outputTitle = $derived(
    stats && stats.outputTokens > 0
      ? `Output tokens: ${formatTokens(stats.outputTokens)}`
      : null,
  );
  let cacheReadLabel = $derived(
    stats && stats.cacheReadTokens > 0 ? `R${compactTokens(stats.cacheReadTokens)}` : null,
  );
  let cacheReadTitle = $derived(
    stats && stats.cacheReadTokens > 0
      ? `Cache read tokens: ${formatTokens(stats.cacheReadTokens)}`
      : null,
  );
  let cacheWriteLabel = $derived(
    stats && stats.cacheWriteTokens > 0 ? `W${compactTokens(stats.cacheWriteTokens)}` : null,
  );
  let cacheWriteTitle = $derived(
    stats && stats.cacheWriteTokens > 0
      ? `Cache write tokens: ${formatTokens(stats.cacheWriteTokens)}`
      : null,
  );
  let wsEnvs = $derived(
    (workspaceEnvironments ?? []).filter(e => Boolean(e?.label?.trim())),
  );
  let hasStatsContent = $derived(
    inputLabel != null ||
      outputLabel != null ||
      cacheReadLabel != null ||
      cacheWriteLabel != null ||
      contextPercent != null ||
      costLabel != null,
  );
  let hasLeadingContent = $derived(
    wsEnvs.length > 0,
  );
  let hasVisibleContent = $derived(hasLeadingContent || hasStatsContent);
  let barColor = $derived.by(() => {
    if (contextPercent == null) return "var(--text-subtle)";
    if (contextPercent < 50) return "var(--text-subtle)";
    if (contextPercent < 80) return "var(--warning)";
    return "var(--danger)";
  });
  let statLabels = $derived.by(() => {
    const labels: string[] = [];
    if (inputLabel) labels.push(inputLabel);
    if (outputLabel) labels.push(outputLabel);
    if (cacheReadLabel) labels.push(cacheReadLabel);
    if (cacheWriteLabel) labels.push(cacheWriteLabel);
    if (costLabel) labels.push(costLabel);
    return labels;
  });
  let contextSummaryLabel = $derived(
    contextPercent != null && windowLabel
      ? `${contextPercent.toFixed(1)}%/${windowLabel}`
      : null,
  );
  let contextTitle = $derived.by(() => {
    if (contextPercent == null || !stats) return null;
    if (stats.tokens != null && stats.tokens > 0) {
      return `Context usage: ${formatTokens(stats.tokens)} of ${formatTokens(stats.contextWindow)} tokens (${contextPercent.toFixed(1)}%)`;
    }
    return `Context window: ${formatTokens(stats.contextWindow)} tokens (${contextPercent.toFixed(1)}% used)`;
  });
  let compactStatsLabel = $derived(
    statLabels.length > 0 ? statLabels.join(" | ") : null,
  );
  let mergedStatsLabel = $derived.by(() => {
    const labels: string[] = [];
    if (contextSummaryLabel) labels.push(contextSummaryLabel);
    labels.push(...statLabels);
    return labels.length > 0 ? labels.join(" | ") : null;
  });
  let availableTrailingWidth = $derived(
    Math.max(
      statsInnerWidth -
        (hasLeadingContent ? statsLeadingScrollWidth + statsGap : 0),
      0,
    ),
  );
  let statsDisplayMode = $derived.by(() => {
    if (!hasStatsContent) return "full";
    if (!fullStatsWidth || availableTrailingWidth + statsSlack >= fullStatsWidth) {
      return "full";
    }
    if (
      compactStatsWidth &&
      availableTrailingWidth + statsSlack >= compactStatsWidth
    ) {
      return "compact";
    }
    return "merged";
  });

  $effect(() => {
    void statsInnerWidth;
    void wsEnvs;
    statsLeadingScrollWidth = statsLeadingEl?.scrollWidth ?? 0;
  });
</script>

{#if hasVisibleContent}
  <div class="stats-bar">
    <div class="stats-inner" bind:clientWidth={statsInnerWidth}>
      <div
        bind:this={statsLeadingEl}
        class="stats-leading"
        class:empty-leading={!hasLeadingContent}
      >
        {#each wsEnvs as environment (`${environment.type}:${environment.label}`)}
          <div
            class="stat-chip env-chip"
            title={environment.detail || environment.label}
          >
            <span class="stat-label env-label">{environment.label}</span>
          </div>
        {/each}
      </div>
      {#if hasStatsContent}
        <div
          class="stats-trailing"
          class:compact={statsDisplayMode !== "full"}
          class:merged={statsDisplayMode === "merged"}
        >
          {#if statsDisplayMode === "full"}
            {#if inputLabel}
              <div class="stat-chip token-chip" title={inputTitle}>
                <span class="stat-label">{inputLabel}</span>
              </div>
            {/if}
            {#if outputLabel}
              <div class="stat-chip token-chip" title={outputTitle}>
                <span class="stat-label">{outputLabel}</span>
              </div>
            {/if}
            {#if cacheReadLabel}
              <div class="stat-chip token-chip" title={cacheReadTitle}>
                <span class="stat-label">{cacheReadLabel}</span>
              </div>
            {/if}
            {#if cacheWriteLabel}
              <div class="stat-chip token-chip" title={cacheWriteTitle}>
                <span class="stat-label">{cacheWriteLabel}</span>
              </div>
            {/if}
            {#if costLabel}
              <div class="stat-chip cost-chip">
                <span class="stat-label">{costLabel}</span>
              </div>
            {/if}
            {#if contextPercent != null}
              <div class="stat-chip context-chip" title={contextTitle}>
                <div class="context-bar-track">
                  <div
                    class="context-bar-fill"
                    style="width: {contextPercent}%; background: {barColor}"
                  ></div>
                </div>
                <span class="stat-label">{contextSummaryLabel}</span>
              </div>
            {/if}
          {:else if statsDisplayMode === "compact"}
            {#if compactStatsLabel}
              <div
                class="stat-chip token-chip combined-chip"
                title={compactStatsLabel}
              >
                <span class="stat-label combined-label">{compactStatsLabel}</span>
              </div>
            {/if}
            {#if contextPercent != null}
              <div
                class="stat-chip context-chip compact-context-chip"
                title={contextTitle}
              >
                <span class="stat-label">{contextSummaryLabel}</span>
              </div>
            {/if}
          {:else if mergedStatsLabel}
            <div
              class="stat-chip context-chip summary-chip"
              title={mergedStatsLabel}
            >
              <span class="stat-label combined-label">{mergedStatsLabel}</span>
            </div>
          {/if}
        </div>
      {/if}
    </div>
  </div>

  {#if hasStatsContent}
    <div class="stats-measure" aria-hidden="true">
      <div class="stats-trailing" bind:clientWidth={fullStatsWidth}>
        {#if inputLabel}
          <div class="stat-chip token-chip">
            <span class="stat-label">{inputLabel}</span>
          </div>
        {/if}
        {#if outputLabel}
          <div class="stat-chip token-chip">
            <span class="stat-label">{outputLabel}</span>
          </div>
        {/if}
        {#if cacheReadLabel}
          <div class="stat-chip token-chip">
            <span class="stat-label">{cacheReadLabel}</span>
          </div>
        {/if}
        {#if cacheWriteLabel}
          <div class="stat-chip token-chip">
            <span class="stat-label">{cacheWriteLabel}</span>
          </div>
        {/if}
        {#if costLabel}
          <div class="stat-chip cost-chip">
            <span class="stat-label">{costLabel}</span>
          </div>
        {/if}
        {#if contextPercent != null}
          <div class="stat-chip context-chip">
            <div class="context-bar-track">
              <div
                class="context-bar-fill"
                style="width: {contextPercent}%; background: {barColor}"
              ></div>
            </div>
            <span class="stat-label">{contextSummaryLabel}</span>
          </div>
        {/if}
      </div>
      <div class="stats-trailing compact" bind:clientWidth={compactStatsWidth}>
        {#if compactStatsLabel}
          <div class="stat-chip token-chip combined-chip">
            <span class="stat-label combined-label">{compactStatsLabel}</span>
          </div>
        {/if}
        {#if contextPercent != null}
          <div class="stat-chip context-chip compact-context-chip">
            <span class="stat-label">{contextSummaryLabel}</span>
          </div>
        {/if}
      </div>
    </div>
  {/if}
{/if}

<style>
  .stats-bar {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    padding: 6px 24px 0;
  }

  .stats-inner {
    display: flex;
    align-items: center;
    gap: 8px;
    width: min(960px, 100%);
    min-width: 0;
    margin: 0 auto;
  }

  .stats-leading {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    flex: 1 1 auto;
    overflow: hidden;
  }

  .env-chip {
    border-color: color-mix(in srgb, var(--accent) 22%, var(--border));
    background: color-mix(in srgb, var(--accent) 8%, var(--panel));
  }

  .env-label {
    color: var(--text-subtle);
  }

  .stats-trailing {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    margin-left: auto;
    min-width: 0;
    max-width: 100%;
    flex: 0 0 auto;
  }

  .stats-trailing.compact {
    overflow: hidden;
  }

  .stat-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    height: 26px;
    padding: 0 10px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
    background: var(--bg);
    min-width: 0;
  }

  .context-chip {
    gap: 8px;
  }

  .token-chip,
  .cost-chip {
    border-color: color-mix(in srgb, var(--border) 50%, transparent);
  }

  .combined-chip,
  .summary-chip {
    max-width: 100%;
  }

  .compact-context-chip,
  .summary-chip {
    gap: 0;
  }

  .context-bar-track {
    width: 48px;
    height: 4px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--border) 80%, transparent);
    overflow: hidden;
    flex-shrink: 0;
  }

  .context-bar-fill {
    height: 100%;
    border-radius: 2px;
    transition:
      width 0.4s ease,
      background 0.3s ease;
  }

  .stat-label {
    font-family: var(--pi-font-sans);
    font-size: 0.64rem;
    font-variant-numeric: tabular-nums;
    color: var(--text-muted);
    white-space: nowrap;
  }

  .combined-label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .stats-measure {
    position: absolute;
    left: -9999px;
    top: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: max-content;
    visibility: hidden;
    pointer-events: none;
  }

  .stats-measure .stats-trailing {
    margin-left: 0;
    max-width: none;
    width: max-content;
    overflow: visible;
  }

  .stats-measure .stat-chip {
    flex-shrink: 0;
  }

  @media (max-width: 900px) {
    .stats-bar {
      justify-content: flex-start;
      padding: 6px 16px 0;
    }

    .stats-inner {
      gap: 6px;
    }

    .stats-leading,
    .stats-trailing {
      gap: 6px;
    }
  }

  @media (max-width: 640px) {
    .stats-bar {
      padding: 4px 12px 0;
    }

    .context-bar-track {
      width: 40px;
    }
  }
</style>
