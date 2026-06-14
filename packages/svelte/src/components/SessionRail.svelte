<script lang="ts">
  import type { Snippet } from "svelte";
  import ChevronDown from "lucide-svelte/icons/chevron-down";
  import ChevronRight from "lucide-svelte/icons/chevron-right";
  import Folder from "lucide-svelte/icons/folder";
  import FolderOpen from "lucide-svelte/icons/folder-open";
  import Plus from "lucide-svelte/icons/plus";
  import Search from "lucide-svelte/icons/search";
  import Trash2 from "lucide-svelte/icons/trash-2";
  import type {
    SessionEntry,
    WorkspaceSummary,
  } from "../composables/bridgeStore.svelte";

  let {
    workspaces = [] as readonly WorkspaceSummary[],
    workspaceSessions = {} as Readonly<Record<string, readonly SessionEntry[]>>,
    activeSessionPath = null as string | null,
    activeWorkspacePath = null as string | null,
    runningSessionPaths = [] as readonly string[],
    workspaceSessionLoaded = {} as Readonly<Record<string, boolean>>,
    workspaceSessionLoading = {} as Readonly<Record<string, boolean>>,
    workspaceSessionCursors = {} as Readonly<Record<string, string | null>>,
    onSelect = (_: string) => {},
    onDelete = (_: string) => {},
    onNewSession = (_: string) => {},
    onExpandWorkspace = (_: string) => {},
    onLoadOlderSessions = (_: {
      workspacePath: string;
      cursor?: string | null;
    }) => {},
    headerActions,
  }: {
    workspaces?: readonly WorkspaceSummary[];
    workspaceSessions?: Readonly<Record<string, readonly SessionEntry[]>>;
    activeSessionPath?: string | null;
    activeWorkspacePath?: string | null;
    runningSessionPaths?: readonly string[];
    workspaceSessionLoaded?: Readonly<Record<string, boolean>>;
    workspaceSessionLoading?: Readonly<Record<string, boolean>>;
    workspaceSessionCursors?: Readonly<Record<string, string | null>>;
    onSelect?: (sessionPath: string) => void;
    onDelete?: (sessionPath: string) => void;
    onNewSession?: (workspacePath: string) => void;
    onExpandWorkspace?: (workspacePath: string) => void;
    onLoadOlderSessions?: (payload: {
      workspacePath: string;
      cursor?: string | null;
    }) => void;
    headerActions?: Snippet;
  } = $props();

  const RECENT_SESSION_LIMIT = 5;
  const MENU_WIDTH = 136;
  const MENU_HEIGHT = 44;
  const WORKSPACE_FOLDER_ICON_SIZE = 14;
  const WORKSPACE_FOLDER_ICON_STYLE = "display: block; flex-shrink: 0;";

  interface WorkspaceGroup {
    id: string;
    name: string;
    path: string;
    sessions: SessionEntry[];
    isExpanded: boolean;
    isActive: boolean;
    isLoaded: boolean;
    isLoading: boolean;
    query: string;
    recentSessions: SessionEntry[];
    remainingSessions: SessionEntry[];
    filteredRemainingSessions: SessionEntry[];
    nextCursor: string | null;
  }

  interface MenuState {
    visible: boolean;
    sessionPath: string | null;
    x: number;
    y: number;
  }

  let expandedWorkspaceIds = $state<Set<string>>(new Set());
  let activeOlderWorkspaceId = $state<string | null>(null);
  let workspaceQueries = $state<Record<string, string>>({});
  let lastAutoExpandedWorkspacePath: string | null = null;
  let menu = $state<MenuState>({
    visible: false,
    sessionPath: null,
    x: 0,
    y: 0,
  });
  let workspacesRootExpanded = $state(true);

  function sessionActivityValue(session: SessionEntry): number {
    const parsed = Date.parse(session.updatedAt ?? session.timestamp ?? "");
    return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
  }

  function compareSessionsByActivity(
    left: SessionEntry,
    right: SessionEntry,
  ): number {
    const activityDelta = sessionActivityValue(right) - sessionActivityValue(left);
    if (activityDelta !== 0) return activityDelta;
    return right.path.localeCompare(left.path);
  }

  function sessionMatchesQuery(session: SessionEntry, query: string): boolean {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return true;
    return [
      session.name,
      session.path,
      session.workspaceName,
      session.workspacePath,
    ]
      .filter((value): value is string => typeof value === "string")
      .some(value => value.toLowerCase().includes(normalizedQuery));
  }

  let workspaceGroups = $derived.by((): WorkspaceGroup[] => {
    return [...workspaces]
      .map(workspace => {
        const groupSessions = [...(workspaceSessions[workspace.path] ?? [])].sort(
          compareSessionsByActivity,
        );
        const query = workspaceQueries[workspace.id] ?? "";
        const remainingSessions = groupSessions.slice(RECENT_SESSION_LIMIT);

        return {
          id: workspace.id,
          name: workspace.name,
          path: workspace.path,
          sessions: groupSessions,
          isExpanded: expandedWorkspaceIds.has(workspace.id),
          isActive: workspace.path === activeWorkspacePath,
          isLoaded: workspaceSessionLoaded[workspace.path] === true,
          isLoading: workspaceSessionLoading[workspace.path] === true,
          query,
          recentSessions: groupSessions.slice(0, RECENT_SESSION_LIMIT),
          remainingSessions,
          filteredRemainingSessions: remainingSessions.filter(session =>
            sessionMatchesQuery(session, query),
          ),
          nextCursor: workspaceSessionCursors[workspace.path] ?? null,
        };
      });
  });

  let activeOlderWorkspace = $derived(
    workspaceGroups.find(workspace => workspace.id === activeOlderWorkspaceId) ?? null,
  );

  let menuPanelStyle = $derived(`left: ${menu.x}px; top: ${menu.y}px`);

  function expandWorkspace(workspaceId: string) {
    if (expandedWorkspaceIds.has(workspaceId)) return;
    expandedWorkspaceIds = new Set([...expandedWorkspaceIds, workspaceId]);
  }

  function toggleWorkspace(workspaceId: string) {
    const next = new Set(expandedWorkspaceIds);
    if (next.has(workspaceId)) next.delete(workspaceId);
    else next.add(workspaceId);
    expandedWorkspaceIds = next;
  }

  function openOlderSessions(workspaceId: string) {
    if (workspaceQueries[workspaceId] === undefined) workspaceQueries[workspaceId] = "";
    activeOlderWorkspaceId = workspaceId;
    const workspace = workspaceGroups.find(group => group.id === workspaceId);
    if (workspace?.nextCursor && workspace.remainingSessions.length === 0) {
      onLoadOlderSessions({
        workspacePath: workspace.path,
        cursor: workspace.nextCursor,
      });
    }
  }

  function loadMoreOlderSessions(workspace: WorkspaceGroup) {
    if (!workspace.nextCursor) return;
    onLoadOlderSessions({
      workspacePath: workspace.path,
      cursor: workspace.nextCursor,
    });
  }

  function closeOlderSessions() {
    activeOlderWorkspaceId = null;
    closeMenu();
  }

  function handleOlderSessionsOverlayClick(event: MouseEvent) {
    if (event.target !== event.currentTarget) return;
    closeOlderSessions();
  }

  function isSessionRunning(sessionPath: string): boolean {
    return runningSessionPaths.includes(sessionPath);
  }

  function openMenu(event: MouseEvent, sessionPath: string) {
    event.preventDefault();
    event.stopPropagation();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    let x = event.clientX + 4;
    let y = event.clientY + 4;
    if (x + MENU_WIDTH > viewportWidth) x = event.clientX - MENU_WIDTH - 4;
    if (y + MENU_HEIGHT > viewportHeight) y = event.clientY - MENU_HEIGHT - 4;
    menu = { visible: true, sessionPath, x, y };
  }

  function closeMenu() {
    menu = { ...menu, visible: false };
  }

  function handleDelete(sessionPath: string) {
    closeMenu();
    if (!confirm("Delete this session? This cannot be undone.")) return;
    onDelete(sessionPath);
  }

  function handleSessionSelect(sessionPath: string, closeModal = false) {
    onSelect(sessionPath);
    if (closeModal) closeOlderSessions();
  }

  function handleWorkspaceNewSession(workspace: WorkspaceGroup) {
    closeMenu();
    onNewSession(workspace.path);
  }

  // Effects
  $effect(() => {
    workspaces.map(workspace => workspace.path).join(",");
    Object.values(workspaceSessions)
      .flat()
      .map(session => session.path)
      .join(",");
    queueMicrotask(closeMenu);
  });

  $effect(() => {
    const activeWorkspace = activeWorkspacePath
      ? workspaceGroups.find(workspace => workspace.path === activeWorkspacePath)
      : null;

    if (!activeWorkspace) {
      lastAutoExpandedWorkspacePath = null;
      return;
    }

    workspacesRootExpanded = true;
    if (activeWorkspace.path === lastAutoExpandedWorkspacePath) return;
    lastAutoExpandedWorkspacePath = activeWorkspace.path;
    expandWorkspace(activeWorkspace.id);
  });

  $effect(() => {
    for (const workspace of workspaceGroups) {
      if (!workspace.isExpanded || workspace.isLoaded || workspace.isLoading) continue;
      onExpandWorkspace(workspace.path);
    }
  });
</script>

<div class="session-rail" role="button" tabindex="0" onclick={closeMenu} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && closeMenu()}>
  <div class="rail-list">
    <section class="workspace-root" class:expanded={workspacesRootExpanded}>
      <div class="workspace-row workspace-root-row">
        <div class="workspace-root-chip">
          <button
            class="workspace-toggle workspace-root-toggle"
            type="button"
            aria-expanded={workspacesRootExpanded}
            aria-label={workspacesRootExpanded ? "Collapse workspaces" : "Expand workspaces"}
            onclick={() => (workspacesRootExpanded = !workspacesRootExpanded)}
            onpointerup={(event) => {
              if (event.currentTarget instanceof HTMLButtonElement) {
                event.currentTarget.blur();
              }
            }}
          >
            {#if workspacesRootExpanded}
              <ChevronDown
                aria-hidden="true"
                size={16}
                color="var(--text-subtle)"
                style="display: block; flex-shrink: 0;"
              />
            {:else}
              <ChevronRight
                aria-hidden="true"
                size={16}
                color="var(--text-subtle)"
                style="display: block; flex-shrink: 0;"
              />
            {/if}
            <span class="workspace-copy workspace-root-copy">
              <span class="workspace-root-label">Workspaces</span>
            </span>
          </button>
          <div class="rail-actions">
            {#if headerActions}
              {@render headerActions()}
            {/if}
          </div>
        </div>
      </div>

      {#if workspacesRootExpanded}
        {#if workspaceGroups.length > 0}
          <div class="workspace-tree">
            {#each workspaceGroups as workspace (workspace.id)}
              <section
                class="workspace-group"
                class:expanded={workspace.isExpanded}
                class:active={workspace.isActive}
              >
                <div class="workspace-row" title={workspace.path}>
                  <button
                    class="workspace-toggle"
                    type="button"
                    aria-expanded={workspace.isExpanded}
                    onclick={() => toggleWorkspace(workspace.id)}
                  >
                    {#if workspace.isExpanded}
                      <FolderOpen
                        aria-hidden="true"
                        size={WORKSPACE_FOLDER_ICON_SIZE}
                        color="var(--text-subtle)"
                        style={WORKSPACE_FOLDER_ICON_STYLE}
                      />
                    {:else}
                      <Folder
                        aria-hidden="true"
                        size={WORKSPACE_FOLDER_ICON_SIZE}
                        color="var(--text-subtle)"
                        style={WORKSPACE_FOLDER_ICON_STYLE}
                      />
                    {/if}
                    <span class="workspace-copy">
                      <span class="workspace-name">{workspace.name}</span>
                      <span class="workspace-path">{workspace.path}</span>
                    </span>
                  </button>
                  <button
                    class="workspace-new-session"
                    type="button"
                    aria-label={`New session in ${workspace.name}`}
                    title={`New session in ${workspace.path}`}
                    onclick={(event) => {
                      event.stopPropagation();
                      handleWorkspaceNewSession(workspace);
                    }}
                  >
                    <Plus size={14} aria-hidden="true" />
                  </button>
                </div>

                {#if workspace.isExpanded}
                  <div class="session-list">
                    {#if !workspace.isLoaded}
                      <p class="workspace-empty">Loading sessions...</p>
                    {:else if workspace.sessions.length === 0}
                      <p class="workspace-empty">No sessions yet</p>
                    {/if}

                    {#each workspace.recentSessions as session (session.path)}
                      <div
                        class="rail-item"
                        role="button"
                        tabindex="0"
                        class:active={session.path === activeSessionPath}
                        class:running={isSessionRunning(session.path)}
                        onclick={() => handleSessionSelect(session.path)}
                        onkeydown={(event) => event.key === "Enter" && handleSessionSelect(session.path)}
                        oncontextmenu={(event) => openMenu(event, session.path)}
                      >
                        <span class="item-indicator"></span>
                        <span class="item-label">{session.name}</span>
                        {#if isSessionRunning(session.path)}
                          <span class="item-status" role="status" aria-label="Agent running" title="Agent running">
                            <span class="item-status-dot" aria-hidden="true"></span>
                          </span>
                        {/if}
                      </div>
                    {/each}

                    {#if workspace.remainingSessions.length > 0 || workspace.nextCursor}
                      <div class="older-sessions">
                        <button
                          class="older-toggle"
                          type="button"
                          aria-haspopup="dialog"
                          onclick={() => openOlderSessions(workspace.id)}
                        >
                          <span>Browse older sessions</span>
                        </button>
                      </div>
                    {/if}
                  </div>
                {/if}
              </section>
            {/each}
          </div>
        {:else}
          <p class="rail-empty nested">No workspaces</p>
        {/if}
      {/if}
    </section>
  </div>
</div>

{#if activeOlderWorkspace}
  <div
    class="older-modal-overlay"
    role="button"
    tabindex="0"
    onclick={handleOlderSessionsOverlayClick}
    onkeydown={(event) => event.key === "Escape" ? closeOlderSessions() : (event.key === "Enter" || event.key === " ") ? handleOlderSessionsOverlayClick(event as unknown as MouseEvent) : undefined}
  >
    <div
      class="older-modal"
      role="dialog"
      aria-modal="true"
      aria-label={`${activeOlderWorkspace.name} older sessions`}
    >
      <label class="modal-session-search">
        <Search size={16} aria-hidden="true" />
        <input
          bind:value={workspaceQueries[activeOlderWorkspace.id]}
          type="search"
          autocomplete="off"
          spellcheck="false"
          placeholder="Search older sessions"
        />
      </label>

      <div class="older-modal-list">
        {#each activeOlderWorkspace.filteredRemainingSessions as session (session.path)}
          <div
            class="modal-session-item"
            role="button"
            tabindex="0"
            class:active={session.path === activeSessionPath}
            class:running={isSessionRunning(session.path)}
            onclick={() => handleSessionSelect(session.path, true)}
            onkeydown={(event) => event.key === "Enter" && handleSessionSelect(session.path, true)}
            oncontextmenu={(event) => openMenu(event, session.path)}
          >
            <span class="item-indicator"></span>
            <span class="modal-session-copy">
              <span class="modal-session-name">{session.name}</span>
            </span>
            {#if isSessionRunning(session.path)}
              <span class="item-status" role="status" aria-label="Agent running" title="Agent running">
                <span class="item-status-dot" aria-hidden="true"></span>
              </span>
            {/if}
          </div>
        {/each}

        {#if activeOlderWorkspace.nextCursor}
          <button
            class="modal-load-more"
            type="button"
            disabled={activeOlderWorkspace.isLoading}
            onclick={() => loadMoreOlderSessions(activeOlderWorkspace)}
          >
            {activeOlderWorkspace.isLoading ? "Loading..." : "Load more"}
          </button>
        {/if}

        {#if !activeOlderWorkspace.isLoading && activeOlderWorkspace.filteredRemainingSessions.length === 0}
          <p class="modal-empty">No matching sessions</p>
        {/if}
      </div>
    </div>
  </div>
{/if}

{#if menu.visible}
  <div
    class="menu-overlay"
    role="button"
    tabindex="0"
    onclick={closeMenu}
    onkeydown={(e) => (e.key === "Enter" || e.key === " " || e.key === "Escape") && closeMenu()}
    oncontextmenu={(event) => {
      event.preventDefault();
      event.stopPropagation();
      closeMenu();
    }}
  >
    <div class="menu-panel show" style={menuPanelStyle} role="presentation" onclick={(event) => event.stopPropagation()} onkeydown={(event) => event.stopPropagation()}>
      <button
        class="menu-item danger"
        type="button"
        onclick={() => menu.sessionPath && handleDelete(menu.sessionPath)}
      >
        <Trash2 aria-hidden="true" size={13} style="opacity: 0.7; flex-shrink: 0" />
        <span>Delete</span>
      </button>
    </div>
  </div>
{/if}

<style>
  .session-rail {
    display: flex;
    flex-direction: column;
    padding: 0px 4px 0;
    overflow: hidden;
    position: relative;
  }

  .rail-actions {
    display: flex;
    align-items: center;
    gap: 2px;
    flex-shrink: 0;
    opacity: 0;
    transform: translateX(2px);
    transition:
      opacity 0.14s ease,
      transform 0.14s ease;
  }

  .workspace-root-row {
    min-height: calc(44px + var(--desktop-rail-top-inset, 0px));
  }

  .workspace-root-chip {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 28px;
    padding: 0 4px 0 0;
    border-radius: 10px;
    transition:
      background 0.12s ease,
      color 0.12s ease;
  }

  .workspace-root-toggle {
    min-height: 24px;
    padding: 2px 0 2px 10px;
    gap: 6px;
  }

  .workspace-root-copy {
    justify-content: center;
    gap: 0;
  }

  .workspace-root-label {
    font-size: 0.9rem;
    font-weight: 500;
    letter-spacing: 0;
    color: var(--text-muted);
  }

  .workspace-tree {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 2px 4px 0 0;
  }

  .rail-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    overflow-y: auto;
    margin-right: -8px;
    padding: 0 14px 8px 0;
    scrollbar-gutter: auto;
    scrollbar-width: none;
  }

  .rail-list::-webkit-scrollbar {
    display: none;
  }

  .workspace-group {
    border-radius: 10px;
  }

  .workspace-toggle,
  .rail-item,
  .older-toggle {
    width: 100%;
    border: 0;
    font: inherit;
    text-align: left;
  }

  .workspace-row {
    display: flex;
    align-items: center;
    gap: 6px;
    min-height: 34px;
    padding: 2px 4px 2px 8px;
    border-radius: 10px;
    background: transparent;
    color: var(--text-muted);
    transition:
      background 0.12s ease,
      color 0.12s ease;
  }

  .workspace-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    align-self: stretch;
    padding: 2px 0;
    background: transparent;
    color: inherit;
    cursor: pointer;
  }

  .workspace-row:hover,
  .workspace-row:focus-within {
    background: var(--surface-hover);
    color: var(--text);
  }

  .workspace-row.workspace-root-row:hover,
  .workspace-row.workspace-root-row:focus-within {
    background: transparent;
    color: var(--text-muted);
  }

  .workspace-root-chip:hover,
  .workspace-root-chip:focus-within {
    background: var(--surface-hover);
    color: var(--text);
  }

  .workspace-root-chip:hover .rail-actions,
  .workspace-root-chip:focus-within .rail-actions {
    opacity: 1;
    transform: translateX(0);
  }

  .workspace-root-toggle:focus-visible {
    outline: none;
  }

  .workspace-row.workspace-root-row {
    padding: calc(4px + var(--desktop-rail-top-inset, 0px)) 6px 2px
      calc(8px + var(--desktop-rail-left-inset, 0px));
  }

  .workspace-copy {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .workspace-name,
  .workspace-path,
  .item-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .workspace-name {
    font-size: 0.82rem;
    font-weight: 600;
    line-height: 1.2;
  }

  .workspace-path {
    display: none;
    font-size: 0.66rem;
    line-height: 1.15;
    color: var(--text-subtle);
  }

  .workspace-group.expanded .workspace-path,
  .workspace-group.active .workspace-path {
    display: block;
  }

  .workspace-new-session {
    width: 24px;
    height: 24px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: var(--text-subtle);
    cursor: pointer;
    flex-shrink: 0;
    opacity: 0;
    transform: translateX(2px) scale(0.96);
    transition:
      background 0.14s ease,
      box-shadow 0.14s ease,
      color 0.14s ease,
      opacity 0.14s ease,
      transform 0.14s ease;
  }

  .workspace-row:hover .workspace-new-session,
  .workspace-row:focus-within .workspace-new-session {
    opacity: 1;
    transform: translateX(0) scale(1);
  }

  .workspace-new-session:hover {
    background: var(--surface-hover);
    color: var(--text-muted);
  }

  .session-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 0 4px 6px 28px;
  }

  .workspace-empty {
    margin: 0;
    padding: 6px 10px;
    font-size: 0.76rem;
    color: var(--text-subtle);
  }

  .rail-item {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 30px;
    padding: 0 10px;
    border-radius: 8px;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 0.82rem;
    position: relative;
    user-select: none;
    transition:
      background 0.12s ease,
      color 0.12s ease;
  }

  .rail-item:hover {
    background: var(--surface-hover);
  }

  .rail-item.active {
    background: var(--surface-selected);
    color: var(--text);
  }

  .item-indicator {
    position: absolute;
    left: 4px;
    top: 50%;
    width: 2px;
    height: 14px;
    border-radius: 999px;
    background: transparent;
    transform: translateY(-50%);
    flex-shrink: 0;
  }

  .rail-item.active .item-indicator {
    background: var(--text);
  }

  .rail-item.running .item-indicator,
  .modal-session-item.running .item-indicator {
    background: var(--accent);
  }

  .item-label {
    flex: 1 1 auto;
    min-width: 0;
  }

  .item-status {
    width: 20px;
    height: 20px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .item-status-dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    background: var(--accent);
    animation: session-running-blink 1.1s ease-in-out infinite;
  }

  .older-sessions {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding-top: 2px;
  }

  .older-toggle {
    height: 28px;
    padding: 0 10px;
    border-radius: 8px;
    background: transparent;
    color: var(--text-subtle);
    cursor: pointer;
    font-size: 0.74rem;
    transition:
      background 0.12s ease,
      color 0.12s ease;
  }

  .older-toggle:hover {
    background: var(--surface-hover);
    color: var(--text-muted);
  }

  .rail-empty {
    margin: 0;
    padding: 8px 10px;
    font-size: 0.78rem;
    color: var(--text-subtle);
  }

  .rail-empty.nested {
    padding-left: 34px;
  }

  .older-modal-overlay {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    background: var(--overlay);
  }

  .older-modal {
    width: min(720px, 100%);
    max-height: min(720px, calc(100vh - 48px));
    display: flex;
    flex-direction: column;
    overflow: hidden;
    border: 1px solid var(--border-strong);
    border-radius: 16px;
    background: var(--bg-elevated);
    color: var(--text);
    box-shadow: var(--shadow-floating);
  }

  .modal-session-search {
    display: flex;
    align-items: center;
    gap: 12px;
    height: 52px;
    margin: 18px;
    padding: 0 16px;
    border: 1px solid var(--border);
    border-radius: 14px;
    color: var(--text-subtle);
    background: var(--panel);
  }

  .modal-session-search:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--focus-ring);
  }

  .modal-session-search input {
    width: 100%;
    min-width: 0;
    height: 40px;
    border: 0;
    outline: 0;
    padding: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 1rem;
  }

  .modal-session-search input::placeholder {
    color: var(--text-subtle);
  }

  .older-modal-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
    min-height: 0;
    overflow-y: auto;
    padding: 0 14px 12px;
  }

  .modal-session-item {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 34px;
    padding: 3px 10px;
    border-radius: 8px;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    font: inherit;
    text-align: left;
    user-select: none;
  }

  .modal-session-item:hover {
    background: var(--surface-hover);
  }

  .modal-session-item.active {
    background: var(--surface-selected);
    color: var(--text);
  }

  .modal-session-copy {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    align-items: center;
  }

  .modal-session-name {
    overflow: hidden;
    font-size: 0.82rem;
    line-height: 1.25;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .modal-load-more {
    margin: 8px 10px 2px;
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--panel-2);
    color: var(--text-muted);
    cursor: pointer;
    font: inherit;
    font-size: 0.82rem;
  }

  .modal-load-more:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  .modal-load-more:disabled {
    opacity: 0.65;
    cursor: progress;
  }

  .modal-empty {
    margin: 0;
    padding: 18px 10px;
    color: var(--text-subtle);
    font-size: 0.84rem;
  }

  .menu-overlay {
    position: fixed;
    inset: 0;
    z-index: 200;
    background: transparent;
  }

  .menu-panel {
    position: fixed;
    min-width: 136px;
    padding: 4px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--bg);
    box-shadow:
      0 0 0 1px color-mix(in srgb, var(--border) 50%, transparent),
      var(--shadow-raised);
    display: flex;
    flex-direction: column;
    gap: 1px;
    opacity: 0;
    transform: scale(0.96);
    transition:
      opacity 0.1s ease,
      transform 0.1s ease;
    pointer-events: auto;
  }

  .menu-panel.show {
    opacity: 1;
    transform: scale(1);
  }

  .menu-item {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    height: 30px;
    padding: 0 8px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-muted);
    font-size: 0.78rem;
    cursor: pointer;
    text-align: left;
    transition:
      background 0.1s ease,
      color 0.1s ease;
  }

  .menu-item:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  .menu-item.danger:hover {
    background: var(--error-bg);
    color: var(--error-text);
  }

  @keyframes session-running-blink {
    0%,
    100% {
      opacity: 1;
      transform: scale(1);
    }
    50% {
      opacity: 0.38;
      transform: scale(0.86);
    }
  }

  @media (max-width: 700px) {
    .older-modal-overlay {
      align-items: stretch;
      padding: 8px;
    }

    .older-modal {
      width: 100%;
      max-height: calc(100vh - 16px);
      border-radius: 14px;
    }

    .modal-session-search {
      height: 46px;
      margin: 14px;
      padding: 0 12px;
      gap: 10px;
      border-radius: 12px;
    }

    .modal-session-search input {
      height: 36px;
      font-size: 0.95rem;
    }

    .modal-session-item {
      align-items: flex-start;
      gap: 8px;
      min-height: 0;
      padding: 8px 10px;
    }

    .modal-session-name {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      line-clamp: 2;
      white-space: normal;
      overflow-wrap: anywhere;
    }
  }
</style>
