<script lang="ts">
  import FolderPlus from "lucide-svelte/icons/folder-plus";
  import RefreshCw from "lucide-svelte/icons/refresh-cw";
  import SessionRail from "../components/SessionRail.svelte";
  import type {
    SessionEntry,
    WorkspaceSummary,
  } from "../composables/bridgeStore.svelte";

  const RAIL_ACTION_ICON_SIZE = 16;
  const WORKSPACE_FOLDER_ICON_STYLE = "display: block;";

  let {
    workspaces,
    workspaceSessions,
    activeSessionPath,
    activeWorkspacePath,
    runningSessionPaths,
    workspaceSessionLoaded,
    workspaceSessionLoading,
    workspaceSessionCursors,
    sidebarOpen = false,
    collapsed = false,
    onCloseSidebar = () => {},
    onRegisterWorkspace = () => {},
    onSelectSession = (_: string) => {},
    onRefreshWorkspaces = () => {},
    onExpandWorkspace = (_: string) => {},
    onLoadOlderSessions = (_: {
      workspacePath: string;
      cursor?: string | null;
    }) => {},
    onNewSession = (_: string) => {},
    onDeleteSession = (_: string) => {},
  }: {
    workspaces: readonly WorkspaceSummary[];
    workspaceSessions: Readonly<Record<string, readonly SessionEntry[]>>;
    activeSessionPath: string | null;
    activeWorkspacePath: string | null;
    runningSessionPaths: readonly string[];
    workspaceSessionLoaded: Readonly<Record<string, boolean>>;
    workspaceSessionLoading: Readonly<Record<string, boolean>>;
    workspaceSessionCursors: Readonly<Record<string, string | null>>;
    sidebarOpen?: boolean;
    collapsed?: boolean;
    onCloseSidebar?: () => void;
    onRegisterWorkspace?: () => void;
    onSelectSession?: (sessionPath: string) => void;
    onRefreshWorkspaces?: () => void;
    onExpandWorkspace?: (workspacePath: string) => void;
    onLoadOlderSessions?: (payload: {
      workspacePath: string;
      cursor?: string | null;
    }) => void;
    onNewSession?: (workspacePath: string) => void;
    onDeleteSession?: (sessionPath: string) => void;
  } = $props();
</script>

<aside class="left-rail" class:open={sidebarOpen} class:collapsed>
  <SessionRail
    {workspaces}
    {workspaceSessions}
    {activeSessionPath}
    {activeWorkspacePath}
    {runningSessionPaths}
    {workspaceSessionLoaded}
    {workspaceSessionLoading}
    {workspaceSessionCursors}
    onSelect={(sp: string) => onSelectSession(sp)}
    onExpandWorkspace={(wp: string) => onExpandWorkspace(wp)}
    onDelete={(sp: string) => onDeleteSession(sp)}
    onNewSession={(wp: string) => onNewSession(wp)}
    onLoadOlderSessions={(e: { workspacePath: string; cursor?: string | null }) => onLoadOlderSessions(e)}
  >
    {#snippet headerActions()}
      <button
        class="rail-button"
        type="button"
        aria-label="Open workspace"
        title="Open workspace"
        onclick={onRegisterWorkspace}
      >
        <FolderPlus
          size={RAIL_ACTION_ICON_SIZE}
          color="var(--text-subtle)"
          style={WORKSPACE_FOLDER_ICON_STYLE}
          aria-hidden="true"
        />
      </button>
      <button
        class="rail-button"
        type="button"
        aria-label="Refresh workspaces"
        title="Refresh workspaces"
        onclick={onRefreshWorkspaces}
      >
          <RefreshCw size={RAIL_ACTION_ICON_SIZE} aria-hidden="true" />
      </button>
    {/snippet}
  </SessionRail>
</aside>
<div class="rail-backdrop" role="button" tabindex="0" onclick={onCloseSidebar} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onCloseSidebar()}></div>

<style>
  .left-rail {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    background: var(--rail-bg);
    overflow: hidden;
  }

  .left-rail.collapsed {
    display: none;
  }

  .rail-button {
    width: 22px;
    height: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-subtle);
    cursor: pointer;
    flex-shrink: 0;
    transition:
      background 0.15s ease,
      color 0.15s ease;
  }

  .rail-button:hover {
    background: var(--panel-2);
    color: var(--text-muted);
  }

  .rail-backdrop {
    display: none;
  }

  @media (max-width: 900px) {
    .left-rail {
      position: absolute;
      top: var(--mobile-header-offset, 0px);
      left: 0;
      bottom: 0;
      width: min(88vw, 360px);
      transform: translateX(-100%);
      transition: transform 0.2s ease;
      z-index: 15;
    }

    .left-rail.collapsed {
      display: flex;
    }

    .left-rail.open {
      transform: translateX(0);
      box-shadow: var(--shadow);
    }

    .rail-backdrop {
      display: block;
      position: absolute;
      inset: var(--mobile-header-offset, 0px) 0 0 0;
      background: var(--backdrop);
      z-index: 14;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.2s;
    }

    .left-rail.open ~ .rail-backdrop {
      pointer-events: auto;
      opacity: 1;
    }
  }
</style>
