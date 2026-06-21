<script lang="ts">
  import type { RpcWorkspaceFile } from "@pi-web/bridge/types";
  import X from "lucide-svelte/icons/x";
  import FileViewerPanel from "../components/FileViewerPanel.svelte";
  import SessionTreeRail from "../components/SessionTreeRail.svelte";
  import type { TreeEntry } from "../composables/bridgeStore.svelte";

  type FileTab = {
    id: string;
    path: string;
    lineNumber: number;
  };

  let {
    treeEntries = [] as readonly TreeEntry[],
    sidebarOpen = false,
    sessionPath = null as string | null,
    hasTreeTab = false,
    activeTabId = "",
    activeFileTab = null as FileTab | null,
    fileViewerTabs = [] as readonly FileTab[],
    readWorkspaceFile = (_: string) =>
      Promise.resolve({} as RpcWorkspaceFile),
    onCloseSidebar = () => {},
    onSelectTab = (_: string) => {},
    onCloseFileTab = (_: string) => {},
    onSelectTreeEntry = (_: string) => {},
  }: {
    treeEntries?: readonly TreeEntry[];
    sidebarOpen?: boolean;
    sessionPath?: string | null;
    hasTreeTab?: boolean;
    activeTabId?: string;
    activeFileTab?: FileTab | null;
    fileViewerTabs?: readonly FileTab[];
    readWorkspaceFile?: (path: string) => Promise<RpcWorkspaceFile>;
    onCloseSidebar?: () => void;
    onSelectTab?: (tabId: string) => void;
    onCloseFileTab?: (tabId: string) => void;
    onSelectTreeEntry?: (entryId: string) => void;
  } = $props();

  let tabs = $derived([
    ...(hasTreeTab ? [{ id: "tree", path: "Tree", lineNumber: 0 }] : []),
    ...fileViewerTabs,
  ]);

  function isTreeTab(tabId: string): boolean {
    return tabId === "tree";
  }

  function fileTabLabel(filePath: string): string {
    const np = filePath.replace(/\\/g, "/");
    return np.split("/").pop() ?? np;
  }
</script>

<aside class="right-rail" class:open={sidebarOpen}>
  <div class="rail-shell">
    <div class="rail-tabs" role="tablist" aria-label="Right sidebar panels">
      {#each tabs as tab (tab.id)}
        <div
          class="rail-tab-item"
          class:active={activeTabId === tab.id}
        >
          <button
            id={`right-rail-tab-${tab.id}`}
            class="rail-tab"
            type="button"
            role="tab"
            aria-selected={activeTabId === tab.id}
            aria-controls={`right-rail-panel-${tab.id}`}
            title={isTreeTab(tab.id)
              ? "Session tree"
              : `${tab.path}:${tab.lineNumber}`}
            onclick={() => onSelectTab(tab.id)}
          >
            <span class="rail-tab-label">
              {isTreeTab(tab.id) ? "Tree" : fileTabLabel(tab.path)}
            </span>
          </button>
          {#if !isTreeTab(tab.id)}
            <button
              type="button"
              class="rail-tab-close"
              class:active={activeTabId === tab.id}
              aria-label={`Close ${tab.path}`}
              title={`Close ${tab.path}`}
              onclick={() => onCloseFileTab(tab.id)}
            >
              <X class="rail-tab-close-icon" aria-hidden="true" size={12} />
            </button>
          {/if}
        </div>
      {/each}
    </div>

    <div class="rail-panel">
      {#if activeTabId === "tree" && hasTreeTab}
        <div
          id="right-rail-panel-tree"
          class="tab-panel"
          role="tabpanel"
          aria-labelledby="right-rail-tab-tree"
        >
          <SessionTreeRail
            entries={treeEntries}
            {sessionPath}
            onSelect={(e: string) => onSelectTreeEntry(e)}
          />
        </div>
      {:else if activeFileTab}
        <div
          id={`right-rail-panel-${activeFileTab.id}`}
          class="tab-panel"
          role="tabpanel"
          aria-labelledby={`right-rail-tab-${activeFileTab.id}`}
        >
          <FileViewerPanel
            filePath={activeFileTab.path}
            lineNumber={activeFileTab.lineNumber}
            readWorkspaceFile={readWorkspaceFile}
          />
        </div>
      {/if}
    </div>
  </div>
</aside>
<div class="rail-backdrop" role="button" tabindex="0" onclick={() => onCloseSidebar()} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onCloseSidebar()}></div>

<style>
  .right-rail {
    min-width: 0;
    height: 100%;
    background: var(--rail-bg);
    border-left: 1px solid var(--border);
    overflow: hidden;
  }

  .rail-shell {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    height: 100%;
    background: var(--rail-bg);
  }

  .rail-tabs {
    display: flex;
    align-items: center;
    gap: 3px;
    min-height: 44px;
    padding: 6px 10px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 78%, transparent);
    overflow-x: auto;
    scrollbar-width: none;
  }

  .rail-tabs::-webkit-scrollbar {
    display: none;
  }

  .rail-tab-item {
    display: inline-flex;
    align-items: center;
    min-width: 0;
    flex-shrink: 0;
    padding: 0 8px;
    border: none;
    border-radius: 10px;
    background: transparent;
    transition: background 0.14s ease;
  }

  .rail-tab-item:hover {
    background: color-mix(in srgb, var(--panel-2) 44%, transparent);
  }

  .rail-tab-item:focus-within {
    background: color-mix(in srgb, var(--surface-active) 28%, var(--panel-2));
  }

  .rail-tab-item.active {
    background: color-mix(in srgb, var(--panel-2) 92%, var(--rail-bg));
  }

  .rail-tab {
    min-width: 0;
    height: 30px;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--text-subtle);
    font: inherit;
    text-align: left;
    cursor: pointer;
    transition: color 0.12s ease;
  }

  .rail-tab:hover,
  .rail-tab-close:hover,
  .rail-tab-item.active .rail-tab,
  .rail-tab-item.active .rail-tab-close {
    color: var(--text);
  }

  .rail-tab-label {
    font-size: 0.73rem;
    font-weight: 600;
    line-height: 1;
  }

  .rail-tab-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 30px;
    margin-left: 4px;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--text-subtle);
    cursor: pointer;
    transition: color 0.12s ease;
  }

  .rail-panel {
    flex: 1;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
  }

  .tab-panel {
    height: 100%;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
  }

  .rail-backdrop {
    display: none;
  }

  @media (max-width: 900px) {
    .right-rail {
      position: absolute;
      top: 0;
      right: 0;
      bottom: 0;
      width: min(100vw, 520px);
      max-width: 100vw;
      transform: translateX(100%);
      transition: transform 0.2s ease;
      z-index: 15;
    }

    .right-rail.open {
      transform: translateX(0);
      box-shadow: var(--shadow);
    }

    .rail-backdrop {
      display: block;
      position: absolute;
      inset: 0;
      background: var(--backdrop);
      z-index: 14;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.2s ease;
    }

    .right-rail.open ~ .rail-backdrop {
      pointer-events: auto;
      opacity: 1;
    }
  }

  @media (max-width: 640px) {
    .right-rail {
      width: 100vw;
      border-left: none;
    }
  }
</style>
