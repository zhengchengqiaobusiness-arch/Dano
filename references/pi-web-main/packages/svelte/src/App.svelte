<script lang="ts">
  import type {
    RpcImageContent,
    RpcThinkingLevel,
    RpcWorkspaceEntry,
    RpcWorkspaceFile,
  } from "@pi-web/bridge/types";
  import { onMount } from "svelte";
  import ExtensionDialog from "./components/ExtensionDialog.svelte";
  import ReconnectBanner from "./components/ReconnectBanner.svelte";
  import ThemeSettingsDialog from "./components/ThemeSettingsDialog.svelte";
  import { initBridge } from "./composables/bridgeStore.svelte";
  import AppHeader from "./layout/AppHeader.svelte";
  import AppMainContent from "./layout/AppMainContent.svelte";
  import AppNotifications from "./layout/AppNotifications.svelte";
  import AppRightSidebar from "./layout/AppRightSidebar.svelte";
  import AppSidebar from "./layout/AppSidebar.svelte";
  import {
    listThemes,
    readStoredThemePreference,
    resolveActiveTheme,
    resolveAppThemeVars,
    serializeThemePreference,
    setThemePreferenceMode,
    setThemePreferenceTheme,
    toggleThemePreferenceMode,
    type ThemeMode,
    type ThemePreference,
  } from "./themes";
  import type { RpcModelInfo } from "./utils/models";
  import {
    DEBUG_WORKSPACE_NAME,
    DEBUG_WORKSPACE_PATH,
    applyDebugPrompt,
    createDebugSession,
    createDebugSessionEntry,
    createDebugWorkspaceSummary,
    debugSessionModelInfo,
    isDebugSessionPath,
    replaceDebugSessionMessage,
    setDebugSessionAutoCompaction,
    setDebugSessionModel,
    setDebugSessionStreaming,
    setDebugSessionThinkingLevel,
    type DebugSession,
    type DebugStreamPlan,
  } from "./utils/debugSession";
  import { parseCompactSlashCommand } from "./utils/slashCommands";

  type RightSidebarTabId = string;

  type FileViewerTab = {
    id: string;
    path: string;
    lineNumber: number;
  };

  const bridge = initBridge();

  const TREE_TAB_ID = "tree";

  let sidebarOpen = $state(false);
  let leftSidebarCollapsed = $state(false);
  let outlineSidebarOpen = $state(false);
  let themeSettingsOpen = $state(false);
  let activeRightSidebarTabId = $state<RightSidebarTabId>(TREE_TAB_ID);
  let fileViewerTabs = $state<FileViewerTab[]>([]);
  let mainContentRef: AppMainContent | null = $state(null);
  let pendingRevision = $state<{
    entryId: string;
    text: string;
    preview: string;
    hasImages: boolean;
    images: RpcImageContent[];
  } | null>(null);
  let editQueuedPayload = $state<{
    text: string;
    images: RpcImageContent[];
  } | null>(null);
  let debugSessions = $state<DebugSession[]>([]);
  let activeDebugSessionPath = $state<string | null>(null);
  let debugWorkspaceEntries = $state<RpcWorkspaceEntry[]>([]);
  let debugWorkspaceEntriesLoading = $state(false);
  let debugWorkspaceEntriesContextKey = $state<string | null>(null);
  let previousDisplayedSessionPath: string | null = null;

  const debugStreamTimers = new Map<string, ReturnType<typeof setTimeout>[]>();
  const debugWorkspaceSummary = createDebugWorkspaceSummary();
  const THEME_CACHE_KEY = "pi-web-theme";
  const LEFT_RAIL_WIDTH_CACHE_KEY = "pi-web-left-rail-width";
  const RIGHT_RAIL_WIDTH_CACHE_KEY = "pi-web-right-rail-width";
  const LEFT_RAIL_MIN_WIDTH = 260;
  const LEFT_RAIL_MAX_WIDTH = 520;
  const LEFT_RAIL_DEFAULT_WIDTH = 320;
  const RIGHT_RAIL_MIN_WIDTH = 240;
  const RIGHT_RAIL_MAX_WIDTH = 760;
  const RIGHT_RAIL_DEFAULT_WIDTH = 420;
  const MIN_CENTER_COLUMN_WIDTH = 360;
  const desktopPlatform =
    typeof window !== "undefined" ? window.piDesktop?.platform ?? null : null;
  const desktopTitleBarStyle =
    typeof window !== "undefined"
      ? window.piDesktop?.titleBarStyle ?? "system"
      : "system";

  type RailSide = "left" | "right";

  function hasStoredThemePreference(): boolean {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(THEME_CACHE_KEY) !== null;
  }

  function readSystemPrefersLight(): boolean {
    if (typeof window === "undefined") return false;
    const desktopTheme = window.piDesktop?.systemTheme;
    if (desktopTheme) {
      return desktopTheme === "light";
    }
    return window.matchMedia("(prefers-color-scheme: light)").matches;
  }

  function resolveDesktopChromeVars(): Record<string, string> {
    if (desktopTitleBarStyle === "hiddenInset") {
      return {
        "--desktop-top-inset": "6px",
        "--desktop-left-inset": desktopPlatform === "darwin" ? "76px" : "0px",
        "--desktop-rail-top-inset": desktopPlatform === "darwin" ? "30px" : "0px",
        "--desktop-rail-left-inset": "0px",
        "--desktop-right-inset": "0px",
      };
    }

    if (desktopTitleBarStyle === "overlay") {
      return {
        "--desktop-top-inset": "0px",
        "--desktop-left-inset": "0px",
        "--desktop-rail-top-inset": "0px",
        "--desktop-rail-left-inset": "0px",
        "--desktop-right-inset": desktopPlatform === "win32" ? "138px" : "0px",
      };
    }

    return {
      "--desktop-top-inset": "0px",
      "--desktop-left-inset": "0px",
      "--desktop-rail-top-inset": "0px",
      "--desktop-rail-left-inset": "0px",
      "--desktop-right-inset": "0px",
    };
  }

  function readCachedThemePreference(): ThemePreference {
    if (typeof window === "undefined") return readStoredThemePreference(null, false);
    return readStoredThemePreference(
      window.localStorage.getItem(THEME_CACHE_KEY),
      readSystemPrefersLight(),
    );
  }

  const debugModeAvailable =
    (typeof window !== "undefined" &&
      window.__PI_WEB_CONFIG__?.debugModeAvailable === true) ||
    (import.meta.env.DEV && __PI_WEB_DEV_DEBUG__);

  function readCachedRailWidth(
    cacheKey: string,
    fallback: number,
    min: number,
    max: number,
  ): number {
    if (typeof window === "undefined") return fallback;
    const cached = Number.parseInt(
      window.localStorage.getItem(cacheKey) ?? "",
      10,
    );
    return Number.isFinite(cached)
      ? Math.min(max, Math.max(min, cached))
      : fallback;
  }

  let themePreferenceExplicit = $state(hasStoredThemePreference());
  let themePreference = $state<ThemePreference>(readCachedThemePreference());
  let compactLayout = $state(isCompactLayout());
  let leftRailWidth = $state(
    readCachedRailWidth(LEFT_RAIL_WIDTH_CACHE_KEY, LEFT_RAIL_DEFAULT_WIDTH, LEFT_RAIL_MIN_WIDTH, LEFT_RAIL_MAX_WIDTH),
  );
  let rightRailWidth = $state(
    readCachedRailWidth(RIGHT_RAIL_WIDTH_CACHE_KEY, RIGHT_RAIL_DEFAULT_WIDTH, RIGHT_RAIL_MIN_WIDTH, RIGHT_RAIL_MAX_WIDTH),
  );
  let activeRailResize = $state<{
    side: RailSide;
    startX: number;
    startWidth: number;
  } | null>(null);

  const darkThemes = listThemes("dark");
  const lightThemes = listThemes("light");

  const desktopChromeVars = resolveDesktopChromeVars();

  let activeTheme = $derived(resolveActiveTheme(themePreference));
  let desktopNativeThemeSource = $derived<PiDesktopThemeSource>(
    themePreferenceExplicit ? activeTheme.mode : "system",
  );

  function styleString(styles: Record<string, string>): string {
    return Object.entries(styles)
      .map(([key, value]) => `${key}: ${value}`)
      .join("; ");
  }

  let allStyle = $derived.by(() => {
    const s: Record<string, string> = {
      ...resolveAppThemeVars(activeTheme),
      ...desktopChromeVars,
      "color-scheme": String(activeTheme.mode),
    };
    if (!compactLayout) {
      const columns = [
        ...(leftSidebarCollapsed ? [] : [`${leftRailWidth}px`]),
        "minmax(0, 1fr)",
        ...(shellRightRailOpen ? [`${rightRailWidth}px`] : []),
      ];
      s["grid-template-columns"] = columns.join(" ");
    }
    return styleString(s);
  });
  let nextThemeLabel = $derived<ThemeMode>(
    activeTheme.mode === "dark" ? "light" : "dark",
  );

  function getWorkspaceDisplayName(workspacePath?: string | null): string | null {
    const np = workspacePath?.trim();
    if (!np) return null;
    const parts = np.split(/[\\/]/).filter(Boolean);
    return parts.at(-1) ?? np;
  }

  function createLocalDebugSession(): DebugSession {
    const backingWorkspacePath = bridge.sessionState?.workspacePath ?? null;
    const backingWorkspace = backingWorkspacePath
      ? bridge.workspaces.find(workspace => workspace.path === backingWorkspacePath) ?? null
      : null;

    return createDebugSession({
      model: bridge.currentModel,
      thinkingLevel: bridge.currentThinkingLevel,
      backingWorkspacePath,
      backingWorkspaceName: backingWorkspace?.name ?? null,
    });
  }

  function updateDebugSession(
    sessionPath: string,
    updater: (session: DebugSession) => DebugSession,
  ): DebugSession | null {
    let updatedSession: DebugSession | null = null;
    debugSessions = debugSessions.map(session => {
      if (session.path !== sessionPath) return session;
      updatedSession = updater(session);
      return updatedSession;
    });
    return updatedSession;
  }

  function clearDebugStreamTimers(sessionPath: string) {
    const timers = debugStreamTimers.get(sessionPath);
    if (!timers) return;
    for (const timer of timers) clearTimeout(timer);
    debugStreamTimers.delete(sessionPath);
  }

  function stopDebugStream(sessionPath: string) {
    clearDebugStreamTimers(sessionPath);
    updateDebugSession(sessionPath, session =>
      session.sessionState.isStreaming
        ? setDebugSessionStreaming(session, false)
        : session
    );
  }

  function stopAllDebugStreams() {
    for (const sessionPath of debugStreamTimers.keys()) {
      stopDebugStream(sessionPath);
    }
  }

  function scheduleDebugStream(
    sessionPath: string,
    stream: DebugStreamPlan | undefined,
  ) {
    clearDebugStreamTimers(sessionPath);
    if (!stream || stream.chunks.length === 0) {
      updateDebugSession(sessionPath, session => setDebugSessionStreaming(session, false));
      return;
    }

    let elapsedMs = 0;
    const timers: ReturnType<typeof setTimeout>[] = [];
    stream.chunks.forEach((chunk, index) => {
      elapsedMs += Math.max(0, chunk.delayMs);
      const isLast = index === stream.chunks.length - 1;
      const timer = setTimeout(() => {
        updateDebugSession(sessionPath, session =>
          replaceDebugSessionMessage(session, chunk.message)
        );
        if (isLast) {
          updateDebugSession(sessionPath, session =>
            setDebugSessionStreaming(session, false)
          );
          clearDebugStreamTimers(sessionPath);
        }
      }, elapsedMs);
      timers.push(timer);
    });
    debugStreamTimers.set(sessionPath, timers);
  }

  async function ensureDisplayedWorkspaceEntries(
    force: boolean = false,
  ): Promise<RpcWorkspaceEntry[]> {
    const workspacePath = activeDebugSession?.backingWorkspacePath?.trim();
    if (!activeDebugSession) {
      return bridge.fetchWorkspaceEntries(force);
    }
    if (!workspacePath) {
      debugWorkspaceEntries = [];
      return [];
    }
    if (
      !force &&
      debugWorkspaceEntriesContextKey === workspacePath &&
      debugWorkspaceEntries.length > 0
    ) {
      return debugWorkspaceEntries;
    }

    debugWorkspaceEntriesLoading = true;
    debugWorkspaceEntriesContextKey = workspacePath;
    try {
      const response = await bridge.sendCommand({
        type: "list_workspace_entries",
        force,
        workspacePath,
      });
      if (!response.success) return debugWorkspaceEntries;
      const entries = Array.isArray((response.data as { entries?: RpcWorkspaceEntry[] } | undefined)?.entries)
        ? ((response.data as { entries?: RpcWorkspaceEntry[] }).entries ?? [])
        : [];
      debugWorkspaceEntries = entries;
      return debugWorkspaceEntries;
    } finally {
      debugWorkspaceEntriesLoading = false;
    }
  }

  async function readDisplayedWorkspaceFile(path: string): Promise<RpcWorkspaceFile> {
    const workspacePath = activeDebugSession?.backingWorkspacePath?.trim();
    if (!activeDebugSession) {
      return bridge.readWorkspaceFile(path);
    }
    if (!workspacePath) {
      throw new Error("Debug session is not bound to a workspace");
    }

    const response = await bridge.sendCommand({
      type: "read_workspace_file",
      path,
      workspacePath,
    });
    if (!response.success) {
      throw new Error(response.error ?? "Failed to read workspace file");
    }
    return response.data as RpcWorkspaceFile;
  }

  let debugSessionsEnabled = $derived(debugModeAvailable);
  let activeDebugSession = $derived(
    debugSessions.find(session => session.path === activeDebugSessionPath) ?? null,
  );
  let debugSessionEntries = $derived(debugSessions.map(createDebugSessionEntry));
  let displayedWorkspaces = $derived(
    debugSessionsEnabled ? [debugWorkspaceSummary, ...bridge.workspaces] : bridge.workspaces,
  );
  let displayedWorkspaceSessions = $derived.by(() => {
    if (!debugSessionsEnabled) return bridge.workspaceSessions;
    return {
      [DEBUG_WORKSPACE_PATH]: debugSessionEntries,
      ...bridge.workspaceSessions,
    };
  });
  let displayedWorkspaceSessionLoaded = $derived.by(() => {
    if (!debugSessionsEnabled) return bridge.workspaceSessionLoaded;
    return {
      [DEBUG_WORKSPACE_PATH]: true,
      ...bridge.workspaceSessionLoaded,
    };
  });
  let displayedWorkspaceSessionLoading = $derived.by(() => {
    if (!debugSessionsEnabled) return bridge.workspaceSessionLoading;
    return {
      [DEBUG_WORKSPACE_PATH]: false,
      ...bridge.workspaceSessionLoading,
    };
  });
  let displayedWorkspaceSessionCursors = $derived.by(() => {
    if (!debugSessionsEnabled) return bridge.workspaceSessionCursors;
    return {
      [DEBUG_WORKSPACE_PATH]: null,
      ...bridge.workspaceSessionCursors,
    };
  });
  let displayedSessions = $derived(
    debugSessionsEnabled ? [...debugSessionEntries, ...bridge.sessions] : bridge.sessions,
  );
  let displayedRunningSessionPaths = $derived(
    debugSessionsEnabled
      ? [
          ...bridge.runningSessionPaths,
          ...debugSessions
            .filter(session => session.sessionState.isStreaming)
            .map(session => session.path),
        ]
      : bridge.runningSessionPaths,
  );
  let displayedActiveSessionPath = $derived(
    activeDebugSession?.path ?? bridge.activeSessionPath,
  );
  let displayedSessionState = $derived(
    activeDebugSession?.sessionState ?? bridge.sessionState,
  );
  let displayedTranscript = $derived(
    activeDebugSession?.transcript ?? bridge.transcript,
  );
  let displayedTranscriptDeltas = $derived(
    activeDebugSession ? [] : bridge.transcriptDeltas,
  );
  let displayedTranscriptStreams = $derived(
    activeDebugSession ? [] : bridge.transcriptStreams,
  );
  let displayedTranscriptHasOlder = $derived(
    activeDebugSession ? false : bridge.transcriptHasOlder,
  );
  let displayedTranscriptInitialLoading = $derived(
    activeDebugSession ? false : bridge.transcriptInitialLoading,
  );
  let displayedTranscriptPageLoading = $derived(
    activeDebugSession ? false : bridge.transcriptPageLoading,
  );
  let displayedPendingTranscriptConfigEvent = $derived(
    activeDebugSession ? null : bridge.pendingTranscriptConfigEvent,
  );
  let displayedIsStreaming = $derived(
    activeDebugSession?.sessionState.isStreaming ?? bridge.isStreaming,
  );
  let displayedIsCompacting = $derived(activeDebugSession ? false : bridge.isCompacting);
  let displayedSessionStats = $derived(activeDebugSession ? null : bridge.sessionStats);
  let displayedTreeEntries = $derived(activeDebugSession ? [] : bridge.treeEntries);
  let displayedHasSessionOutline = $derived(
    activeDebugSession === null && bridge.hasSessionOutline,
  );
  let displayedWorkspaceEntries = $derived(
    activeDebugSession ? debugWorkspaceEntries : bridge.workspaceEntries,
  );
  let displayedWorkspaceEntriesLoading = $derived(
    activeDebugSession ? debugWorkspaceEntriesLoading : bridge.workspaceEntriesLoading,
  );
  let displayedWorkspaceContextKey = $derived(
    activeDebugSession?.backingWorkspacePath ??
      displayedSessionState?.workspacePath ??
      displayedActiveSessionPath,
  );
  let displayedCurrentModel = $derived(
    activeDebugSession ? debugSessionModelInfo(activeDebugSession) : bridge.currentModel,
  );
  let displayedCurrentThinkingLevel = $derived(
    activeDebugSession?.sessionState.thinkingLevel ?? bridge.currentThinkingLevel,
  );
  let displayedAutoCompactionEnabled = $derived(
    activeDebugSession
      ? activeDebugSession.sessionState.autoCompactionEnabled
      : bridge.sessionState?.autoCompactionEnabled ?? false,
  );
  let displayedPendingMessageCount = $derived(
    activeDebugSession ? 0 : bridge.pendingMessageCount,
  );
  let displayedQueuedUserMessages = $derived(
    activeDebugSession ? [] : bridge.queuedUserMessages,
  );
  let hasRightSidebarContent = $derived(
    displayedHasSessionOutline || fileViewerTabs.length > 0,
  );

  let activeSessionEntry = $derived(
    displayedSessions.find(
      s =>
        s.path === displayedActiveSessionPath ||
        s.id === displayedSessionState?.sessionId,
    ) ?? null,
  );
  let activeSessionLabel = $derived.by(() => {
    if (activeDebugSession) return activeDebugSession.name;
    if (!displayedHasSessionOutline) return "No active session";
    return (
      displayedSessionState?.sessionName ??
      activeSessionEntry?.name ??
      displayedSessionState?.sessionId ??
      "Untitled session"
    );
  });
  let activeWorkspaceLabel = $derived.by(() => {
    if (activeDebugSession) return DEBUG_WORKSPACE_NAME;
    const wn = activeSessionEntry?.workspaceName?.trim();
    return (
      wn ||
      getWorkspaceDisplayName(activeSessionEntry?.workspacePath) ||
      getWorkspaceDisplayName(displayedSessionState?.workspacePath)
    );
  });
  let displayedActiveWorkspacePath = $derived(
    activeDebugSession
      ? DEBUG_WORKSPACE_PATH
      : displayedSessionState?.workspacePath ?? activeSessionEntry?.workspacePath ?? null,
  );

  let activeFileViewerTab = $derived(
    fileViewerTabs.find(t => t.id === activeRightSidebarTabId) ?? null,
  );

  let showLeftRailResizer = $derived(
    !compactLayout && !leftSidebarCollapsed,
  );
  let showRightRailResizer = $derived(
    !compactLayout && hasRightSidebarContent && outlineSidebarOpen,
  );
  let shellRightRailOpen = $derived(
    !compactLayout && hasRightSidebarContent && outlineSidebarOpen,
  );
  let leftRailResizerStyle = $derived(`left: ${leftRailWidth - 5}px`);
  let rightRailResizerStyle = $derived(`right: ${rightRailWidth - 5}px`);

  function fileViewerTabId(path: string): string {
    return `file:${path.replace(/\\/g, "/")}`;
  }

  function defaultRightSidebarTabId(): RightSidebarTabId | null {
    if (displayedHasSessionOutline) return TREE_TAB_ID;
    return fileViewerTabs[0]?.id ?? null;
  }

  function ensureActiveRightSidebarTab() {
    if (activeRightSidebarTabId === TREE_TAB_ID && displayedHasSessionOutline) return;

    const activeFileTab = fileViewerTabs.find(
      t => t.id === activeRightSidebarTabId,
    );
    if (activeFileTab) return;

    activeRightSidebarTabId = defaultRightSidebarTabId() ?? TREE_TAB_ID;
  }

  function openFileViewer(path: string, lineNumber: number) {
    const tp = path.trim();
    if (!tp) return;

    const nl = Number.isInteger(lineNumber) && lineNumber > 0 ? lineNumber : 1;
    const id = fileViewerTabId(tp);
    const ei = fileViewerTabs.findIndex(t => t.id === id);
    if (ei >= 0) {
      const nt = [...fileViewerTabs];
      nt[ei] = { ...nt[ei], lineNumber: nl };
      fileViewerTabs = nt;
    } else {
      fileViewerTabs = [...fileViewerTabs, { id, path: tp, lineNumber: nl }];
    }

    activeRightSidebarTabId = id;
    outlineSidebarOpen = true;
    if (compactLayout) {
      sidebarOpen = false;
    } else {
      rightRailWidth = clampRailWidth("right", rightRailWidth);
    }
  }

  function closeFileViewerTab(tabId: string) {
    const ci = fileViewerTabs.findIndex(t => t.id === tabId);
    if (ci === -1) return;

    const nt = fileViewerTabs.filter(t => t.id !== tabId);
    fileViewerTabs = nt;

    if (activeRightSidebarTabId !== tabId) return;

    const fb = nt[ci] ?? nt[ci - 1];
    if (fb) {
      activeRightSidebarTabId = fb.id;
      return;
    }

    if (displayedHasSessionOutline) {
      activeRightSidebarTabId = TREE_TAB_ID;
      return;
    }

    outlineSidebarOpen = false;
  }

  function isCompactLayout(): boolean {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(max-width: 900px)").matches;
  }

  function maxRailWidth(side: RailSide): number {
    if (typeof window === "undefined")
      return side === "left" ? LEFT_RAIL_MAX_WIDTH : RIGHT_RAIL_MAX_WIDTH;

    const vw = window.innerWidth;
    if (side === "left") {
      const rrw = !compactLayout && hasRightSidebarContent && outlineSidebarOpen
        ? rightRailWidth
        : 0;
      return Math.max(
        LEFT_RAIL_MIN_WIDTH,
        Math.min(LEFT_RAIL_MAX_WIDTH, vw - rrw - MIN_CENTER_COLUMN_WIDTH),
      );
    }

    const rlw = !compactLayout && !leftSidebarCollapsed ? leftRailWidth : 0;
    return Math.max(
      RIGHT_RAIL_MIN_WIDTH,
      Math.min(RIGHT_RAIL_MAX_WIDTH, vw - rlw - MIN_CENTER_COLUMN_WIDTH),
    );
  }

  function clampRailWidth(side: RailSide, width: number): number {
    const minWidth =
      side === "left" ? LEFT_RAIL_MIN_WIDTH : RIGHT_RAIL_MIN_WIDTH;
    const maxWidth = maxRailWidth(side);
    return Math.round(Math.min(maxWidth, Math.max(minWidth, width)));
  }

  function normalizeRailWidths() {
    leftRailWidth = clampRailWidth("left", leftRailWidth);
    rightRailWidth = clampRailWidth("right", rightRailWidth);
  }

  function syncCompactLayout() {
    compactLayout = isCompactLayout();
    if (compactLayout) {
      stopRailResize();
      return;
    }
    normalizeRailWidths();
  }

  function startRailResize(side: RailSide, event: PointerEvent) {
    if (compactLayout) return;
    if (side === "left" && leftSidebarCollapsed) return;
    if (side === "right" && (!hasRightSidebarContent || !outlineSidebarOpen))
      return;

    event.preventDefault();
    activeRailResize = {
      side,
      startX: event.clientX,
      startWidth: side === "left" ? leftRailWidth : rightRailWidth,
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", handleRailResize);
    window.addEventListener("pointerup", stopRailResize);
    window.addEventListener("pointercancel", stopRailResize);
  }

  function handleRailResize(event: PointerEvent) {
    if (!activeRailResize) return;

    const delta = event.clientX - activeRailResize.startX;
    if (activeRailResize.side === "left") {
      leftRailWidth = clampRailWidth(
        "left",
        activeRailResize.startWidth + delta,
      );
      return;
    }

    rightRailWidth = clampRailWidth(
      "right",
      activeRailResize.startWidth - delta,
    );
  }

  function stopRailResize() {
    if (!activeRailResize && typeof window === "undefined") return;
    activeRailResize = null;
    if (typeof document !== "undefined") {
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");
    }
    if (typeof window !== "undefined") {
      window.removeEventListener("pointermove", handleRailResize);
      window.removeEventListener("pointerup", stopRailResize);
      window.removeEventListener("pointercancel", stopRailResize);
    }
  }

  function resetRailWidth(side: RailSide) {
    if (side === "left") {
      leftRailWidth = clampRailWidth("left", LEFT_RAIL_DEFAULT_WIDTH);
      return;
    }

    rightRailWidth = clampRailWidth("right", RIGHT_RAIL_DEFAULT_WIDTH);
  }

  function applyExplicitThemePreference(nextPreference: ThemePreference) {
    themePreferenceExplicit = true;
    themePreference = nextPreference;
  }

  function applySystemThemeMode(mode: ThemeMode) {
    if (themePreferenceExplicit) {
      return;
    }

    themePreference = setThemePreferenceMode(themePreference, mode);
  }

  function toggleTheme() {
    applyExplicitThemePreference(toggleThemePreferenceMode(themePreference));
  }

  function openThemeSettings() {
    themeSettingsOpen = true;
  }

  function closeThemeSettings() {
    themeSettingsOpen = false;
  }

  function handleThemePresetSelect(themeId: string) {
    applyExplicitThemePreference(
      setThemePreferenceTheme(
        themePreference,
        themePreference.mode,
        themeId,
      ),
    );
  }

  function toggleSessionSidebar() {
    const nextOpen = !sidebarOpen;
    sidebarOpen = nextOpen;
    if (nextOpen && compactLayout) {
      outlineSidebarOpen = false;
    }
  }

  function toggleLeftSidebarCollapse() {
    leftSidebarCollapsed = !leftSidebarCollapsed;
  }

  async function handleSessionSelect(sessionPath: string) {
    pendingRevision = null;

    if (isDebugSessionPath(sessionPath)) {
      activeDebugSessionPath = sessionPath;
      sidebarOpen = false;
      return;
    }

    activeDebugSessionPath = null;
    try {
      const response = await bridge.switchSession(sessionPath);
      if (response.success) {
        sidebarOpen = false;
      }
    } catch {
      // Keep current sidebar state
    }
  }

  function handleRefreshWorkspaces() {
    bridge.refreshWorkspaces().then(() => {
      for (const workspacePath of Object.keys(bridge.workspaceSessionLoaded)) {
        if (bridge.workspaceSessionLoaded[workspacePath]) {
          bridge.loadWorkspaceSessions({
            workspacePath,
            limit: 5,
            merge: "replace",
          }).catch(() => {});
        }
      }
    }).catch(() => {});
  }

  function handleExpandWorkspace(workspacePath: string) {
    if (bridge.workspaceSessionLoaded[workspacePath] || bridge.workspaceSessionLoading[workspacePath]) {
      return;
    }
    bridge.loadWorkspaceSessions({
      workspacePath,
      limit: 5,
      merge: "replace",
    }).catch(() => {});
  }

  function handleLoadOlderSessions(payload: {
    workspacePath: string;
    cursor?: string | null;
  }) {
    bridge.loadWorkspaceSessions({
      workspacePath: payload.workspacePath,
      cursor: payload.cursor,
      limit: 50,
      merge: "append",
    }).catch(() => {});
  }

  async function handleNewSession(workspacePath: string) {
    pendingRevision = null;

    if (debugSessionsEnabled && workspacePath === DEBUG_WORKSPACE_PATH) {
      const session = createLocalDebugSession();
      debugSessions = [session, ...debugSessions];
      activeDebugSessionPath = session.path;
      editQueuedPayload = null;
      sidebarOpen = false;
      return;
    }

    activeDebugSessionPath = null;
    try {
      const response = await bridge.newSession(workspacePath);
      if (response.success) {
        sidebarOpen = false;
      }
    } catch {
      // Keep current sidebar state
    }
  }

  async function handleRegisterWorkspace() {
    try {
      const workspacePath = window.piDesktop
        ? await window.piDesktop.pickWorkspace()
        : undefined;
      if (window.piDesktop && !workspacePath) {
        return;
      }

      const response = await bridge.registerWorkspace(workspacePath ?? undefined);
      if (response.success) {
        const data = response.data as
          | { cancelled?: boolean; workspacePath?: string }
          | undefined;
        if (data?.cancelled) return;
        handleRefreshWorkspaces();
      }
    } catch {
      // Ignore
    }
  }

  async function handleDeleteSession(sessionPath: string) {
    if (isDebugSessionPath(sessionPath)) {
      stopDebugStream(sessionPath);
      const remaining = debugSessions.filter(session => session.path !== sessionPath);
      debugSessions = remaining;
      if (activeDebugSessionPath === sessionPath) {
        activeDebugSessionPath = remaining[0]?.path ?? null;
      }
      return;
    }

    try {
      await bridge.deleteSession(sessionPath);
    } catch {
      // Ignore
    }
  }

  function toggleOutlineSidebar() {
    const nextOpen = !outlineSidebarOpen;
    outlineSidebarOpen = nextOpen;
    if (!nextOpen) return;

    ensureActiveRightSidebarTab();
    if (compactLayout) {
      sidebarOpen = false;
    } else {
      rightRailWidth = clampRailWidth("right", rightRailWidth);
    }

    if (activeRightSidebarTabId === TREE_TAB_ID) {
      handleRefreshTree();
    }
  }

  function handleRightSidebarTabSelect(tabId: string) {
    activeRightSidebarTabId = tabId;
    if (tabId === TREE_TAB_ID) handleRefreshTree();
  }

  function handleOpenFileReference(payload: {
    path: string;
    lineNumber: number;
  }) {
    openFileViewer(payload.path, payload.lineNumber);
  }

  function handleRefreshTree() {
    if (!displayedHasSessionOutline) return;

    const sp = displayedActiveSessionPath ?? undefined;
    bridge.sendCommand({ type: "list_tree_entries", sessionPath: sp }).catch(() => {});
  }

  async function revealTreeEntryInTranscript(entryId: string): Promise<boolean> {
    if (mainContentRef?.scrollToTranscriptEntry(entryId)) return true;

    try {
      await bridge.sendCommand({
        type: "get_messages",
        direction: "latest",
        limit: 40,
      });
      await tick();
      if (mainContentRef?.scrollToTranscriptEntry(entryId)) return true;
    } catch {
      // Keep current bridge.transcript
    }

    const MAX_HISTORY_PAGES = 50;
    for (let page = 0; page < MAX_HISTORY_PAGES && displayedTranscriptHasOlder; page++) {
      await bridge.loadOlderTranscriptPage();
      await tick();
      if (mainContentRef?.scrollToTranscriptEntry(entryId)) return true;
    }

    return false;
  }

  async function handleTreeEntrySelect(entryId: string) {
    pendingRevision = null;

    const entry = displayedTreeEntries.find(c => c.id === entryId);
    if (entry?.isOnActivePath) {
      const revealed = await revealTreeEntryInTranscript(entryId);
      if (revealed) {
        if (compactLayout) outlineSidebarOpen = false;
        return;
      }
    }

    try {
      const response = await bridge.sendCommand({
        type: "select_tree_entry",
        entryId,
      });
      if (response.success) {
        await tick();
        mainContentRef?.scrollToTranscriptEntry(entryId);
        if (compactLayout) outlineSidebarOpen = false;
      }
    } catch {
      // Keep state
    }
  }

  async function handlePrompt(payload: {
    message: string;
    images: RpcImageContent[];
    revisionEntryId?: string;
    steer?: boolean;
  }) {
    if (activeDebugSessionPath) {
      pendingRevision = null;
      editQueuedPayload = null;
      stopDebugStream(activeDebugSessionPath);
      let stream: DebugStreamPlan | undefined;
      updateDebugSession(activeDebugSessionPath, session => {
        const result = applyDebugPrompt(session, payload.message, payload.images);
        stream = result.stream;
        return result.session;
      });
      scheduleDebugStream(activeDebugSessionPath, stream);
      return;
    }

    const compactCommand = parseCompactSlashCommand(payload.message);
    if (compactCommand) {
      pendingRevision = null;
      bridge.compactSession(compactCommand.customInstructions).catch(() => {});
      return;
    }

    if (payload.revisionEntryId) {
      try {
        const response = await bridge.sendCommand({
          type: "navigate_tree",
          entryId: payload.revisionEntryId,
        });
        if (!response.success) return;
        const result = response.data as { cancelled?: boolean } | undefined;
        if (result?.cancelled) return;
      } catch {
        return;
      }
    }

    pendingRevision = null;
    bridge.sendPrompt(
      payload.message,
      payload.images,
      payload.steer ? "steer" : "followUp",
    );
  }

  function handleReviseMessage(payload: {
    entryId: string;
    text: string;
    preview: string;
    hasImages: boolean;
    images: RpcImageContent[];
  }) {
    pendingRevision = payload;
  }

  function handleCancelRevision() {
    pendingRevision = null;
  }

  async function handleCancelQueued(index: number) {
    if (activeDebugSessionPath) return;
    await bridge.cancelQueuedMessage(index);
  }

  async function handleEditQueued(index: number) {
    if (activeDebugSessionPath) return;
    const item = await bridge.editQueuedMessage(index);
    if (!item) return;
    editQueuedPayload = item;
  }

  function handleAbort() {
    if (activeDebugSessionPath) {
      stopDebugStream(activeDebugSessionPath);
      return;
    }
    bridge.abortGeneration().catch(() => {});
  }

  function handleModelSelect(model: RpcModelInfo) {
    if (
      displayedCurrentModel &&
      displayedCurrentModel.provider === model.provider &&
      displayedCurrentModel.id === model.id
    )
      return;

    if (activeDebugSessionPath) {
      updateDebugSession(activeDebugSessionPath, session =>
        setDebugSessionModel(session, model)
      );
      return;
    }

    bridge.sendCommand({
      type: "set_model",
      provider: model.provider,
      modelId: model.id,
    }).catch(() => {});
  }

  function handleThinkingLevelSelect(level: RpcThinkingLevel) {
    if (displayedCurrentThinkingLevel === level) return;
    if (activeDebugSessionPath) {
      updateDebugSession(activeDebugSessionPath, session =>
        setDebugSessionThinkingLevel(session, level)
      );
      return;
    }
    bridge.setThinkingLevel(level).catch(() => {});
  }

  function handleAutoCompactionToggle(enabled: boolean) {
    if (displayedAutoCompactionEnabled === enabled) return;
    if (activeDebugSessionPath) {
      updateDebugSession(activeDebugSessionPath, session =>
        setDebugSessionAutoCompaction(session, enabled)
      );
      return;
    }
    bridge.setAutoCompactionEnabled(enabled).catch(() => {});
  }

  function handleUIRespond(payload: Parameters<typeof bridge.respondToUIRequest>[0]) {
    bridge.respondToUIRequest(payload);
  }

  function handleDismissNotification(id: string) {
    bridge.dismissNotification(id);
  }

  function resolveDesktopMenuWorkspacePath(): string | null {
    const currentWorkspacePath = displayedSessionState?.workspacePath?.trim();
    if (currentWorkspacePath) {
      return currentWorkspacePath;
    }

    if (displayedActiveWorkspacePath && displayedActiveWorkspacePath !== DEBUG_WORKSPACE_PATH) {
      return displayedActiveWorkspacePath;
    }

    return (
      displayedWorkspaces.find(workspace => workspace.path !== DEBUG_WORKSPACE_PATH)?.path ??
      null
    );
  }

  function handleDesktopMenuAction(action: PiDesktopMenuAction) {
    switch (action) {
      case "open-workspace":
        void handleRegisterWorkspace();
        return;
      case "refresh-workspaces":
        handleRefreshWorkspaces();
        return;
      case "new-session": {
        const workspacePath = resolveDesktopMenuWorkspacePath();
        if (workspacePath) {
          void handleNewSession(workspacePath);
        }
        return;
      }
      case "toggle-theme":
        toggleTheme();
        return;
      case "open-theme-settings":
        openThemeSettings();
        return;
    }
  }

  function handleGlobalKeydown(event: KeyboardEvent) {
    if (event.defaultPrevented) return;
    if (event.key !== "Escape") return;
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (!displayedIsStreaming) return;
    event.preventDefault();
    handleAbort();
  }

  // Effects
  $effect(() => {
    if (typeof window !== "undefined" && themePreferenceExplicit) {
      window.localStorage.setItem(THEME_CACHE_KEY, serializeThemePreference(themePreference));
    }
  });

  $effect(() => {
    if (typeof window !== "undefined" && window.piDesktop) {
      void window.piDesktop.setNativeTheme(desktopNativeThemeSource).catch(() => {});
    }
  });

  $effect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(LEFT_RAIL_WIDTH_CACHE_KEY, String(leftRailWidth));
    }
  });

  $effect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(RIGHT_RAIL_WIDTH_CACHE_KEY, String(rightRailWidth));
    }
  });

  $effect(() => {
    if (bridge.connectionStatus === "disconnected") {
      pendingRevision = null;
      outlineSidebarOpen = false;
    }
  });

  $effect(() => {
    if (!debugSessionsEnabled && activeDebugSessionPath !== null) {
      activeDebugSessionPath = null;
    }
  });

  $effect(() => {
    const workspacePath = activeDebugSession?.backingWorkspacePath ?? null;
    if (workspacePath === debugWorkspaceEntriesContextKey) return;
    debugWorkspaceEntriesContextKey = workspacePath;
    debugWorkspaceEntries = [];
    debugWorkspaceEntriesLoading = false;
  });

  $effect(() => {
    const sessionPath = displayedActiveSessionPath ?? null;
    if (
      sessionPath &&
      previousDisplayedSessionPath !== null &&
      previousDisplayedSessionPath !== sessionPath
    ) {
      pendingRevision = null;
    }
    previousDisplayedSessionPath = sessionPath;
  });

  $effect(() => {
    if (!displayedHasSessionOutline && activeRightSidebarTabId === TREE_TAB_ID) {
      const fb = fileViewerTabs[0];
      if (fb) {
        activeRightSidebarTabId = fb.id;
        return;
      }
    }

    if (!displayedHasSessionOutline && fileViewerTabs.length === 0) {
      outlineSidebarOpen = false;
      return;
    }

    ensureActiveRightSidebarTab();
  });

  $effect(() => {
    if (fileViewerTabs.length === 0 && !displayedHasSessionOutline) {
      outlineSidebarOpen = false;
    }
    ensureActiveRightSidebarTab();
  });

  $effect(() => {
    // Normalize rail widths when layout changes
    if (compactLayout) {
      if (activeRailResize) stopRailResize();
      return;
    }

    if (activeRailResize?.side === "left" && leftSidebarCollapsed) {
      stopRailResize();
      return;
    }

    if (
      activeRailResize?.side === "right" &&
      (!hasRightSidebarContent || !outlineSidebarOpen)
    ) {
      stopRailResize();
      return;
    }

    normalizeRailWidths();
  });

  // Auto-dismiss bridge.notifications after 5 seconds
  let notificationTimerIds = new Set<string>();

  $effect(() => {
    for (const n of bridge.notifications) {
      if (notificationTimerIds.has(n.id)) continue;
      notificationTimerIds.add(n.id);
      setTimeout(() => bridge.dismissNotification(n.id), 5000);
    }
  });

  function tick(): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, 0));
  }

  const compatWarningVisible = $state(false);

  onMount(() => {
    syncCompactLayout();

    const disposers: Array<() => void> = [];
    const mediaQuery = window.matchMedia("(prefers-color-scheme: light)");
    const handleMediaThemeChange = (event: MediaQueryListEvent) => {
      applySystemThemeMode(event.matches ? "light" : "dark");
    };

    mediaQuery.addEventListener("change", handleMediaThemeChange);
    disposers.push(() => {
      mediaQuery.removeEventListener("change", handleMediaThemeChange);
    });

    if (window.piDesktop) {
      disposers.push(window.piDesktop.onMenuAction(handleDesktopMenuAction));
      disposers.push(
        window.piDesktop.onSystemThemeChange(mode => {
          applySystemThemeMode(mode);
        }),
      );
    }

    return () => {
      for (const dispose of disposers) {
        dispose();
      }
      stopAllDebugStreams();
      stopRailResize();
    };
  });
</script>

<svelte:window onresize={syncCompactLayout} onkeydown={handleGlobalKeydown} />

<div
  class="app-shell"
  class:left-rail-collapsed={leftSidebarCollapsed}
  class:right-rail-open={shellRightRailOpen}
  data-theme={activeTheme.id}
  data-theme-mode={activeTheme.mode}
  data-dark-theme={themePreference.darkThemeId}
  data-light-theme={themePreference.lightThemeId}
  data-desktop-platform={desktopPlatform ?? undefined}
  data-desktop-titlebar={desktopTitleBarStyle}
  style={allStyle}
>
  <AppSidebar
    workspaces={displayedWorkspaces}
    workspaceSessions={displayedWorkspaceSessions}
    activeSessionPath={displayedActiveSessionPath}
    activeWorkspacePath={displayedActiveWorkspacePath}
    runningSessionPaths={displayedRunningSessionPaths}
    workspaceSessionLoaded={displayedWorkspaceSessionLoaded}
    workspaceSessionLoading={displayedWorkspaceSessionLoading}
    workspaceSessionCursors={displayedWorkspaceSessionCursors}
    {sidebarOpen}
    collapsed={leftSidebarCollapsed}
    onRegisterWorkspace={handleRegisterWorkspace}
    onCloseSidebar={() => (sidebarOpen = false)}
    onSelectSession={handleSessionSelect}
    onRefreshWorkspaces={handleRefreshWorkspaces}
    onExpandWorkspace={handleExpandWorkspace}
    onLoadOlderSessions={handleLoadOlderSessions}
    onNewSession={handleNewSession}
    onDeleteSession={handleDeleteSession}
  />

  {#if showLeftRailResizer}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <div
      class="rail-resizer left"
      class:active={activeRailResize?.side === "left"}
      style={leftRailResizerStyle}
      role="separator"
      aria-label="Resize left sidebar. Double-click to reset."
      tabindex="0"
      title="Drag to resize bridge.sessions sidebar. Double-click to reset."
      onpointerdown={(e) => startRailResize("left", e)}
      ondblclick={() => resetRailWidth("left")}
    ></div>
  {/if}

  <div class="app-main-column">
    <AppHeader
      theme={activeTheme.mode}
      {nextThemeLabel}
      sessionTitle={activeSessionLabel}
      workspaceName={activeWorkspaceLabel}
      sidebarCollapsed={leftSidebarCollapsed}
      showOutlineToggle={hasRightSidebarContent}
      outlineSidebarOpen={outlineSidebarOpen}
      desktopPlatform={desktopPlatform}
      desktopTitleBarStyle={desktopTitleBarStyle}
      onToggleSidebar={toggleSessionSidebar}
      onToggleSidebarCollapse={toggleLeftSidebarCollapse}
      onToggleOutlineSidebar={toggleOutlineSidebar}
      onToggleTheme={toggleTheme}
      onOpenThemeSettings={openThemeSettings}
    />

    <ReconnectBanner
      visible={bridge.isReconnecting}
      reason={bridge.lastDisconnectReason}
      reconnectCount={bridge.reconnectCount}
    />

    <div class="app-body">
      <AppMainContent
        bind:this={mainContentRef}
        {compatWarningVisible}
        statusEntries={bridge.statusEntries}
        activeSessionPath={displayedActiveSessionPath}
        transcript={displayedTranscript}
        transcriptDeltas={displayedTranscriptDeltas}
        transcriptStreams={displayedTranscriptStreams}
        transcriptHasOlder={displayedTranscriptHasOlder}
        transcriptInitialLoading={displayedTranscriptInitialLoading}
        transcriptPageLoading={displayedTranscriptPageLoading}
        pendingTranscriptConfigEvent={displayedPendingTranscriptConfigEvent}
        isStreaming={displayedIsStreaming}
        isCompacting={displayedIsCompacting}
        isDebugMode={debugModeAvailable}
        connectionStatus={bridge.connectionStatus}
        commands={bridge.commands}
        workspaceEntries={displayedWorkspaceEntries}
        workspaceEntriesLoading={displayedWorkspaceEntriesLoading}
        workspaceContextKey={displayedWorkspaceContextKey}
        ensureWorkspaceEntries={ensureDisplayedWorkspaceEntries}
        availableModels={bridge.availableModels}
        currentModel={displayedCurrentModel}
        currentThinkingLevel={displayedCurrentThinkingLevel}
        autoCompactionEnabled={displayedAutoCompactionEnabled}
        sessionStats={displayedSessionStats}
        sessionState={displayedSessionState}
        gitRepoState={bridge.gitRepoState}
        gitRepoLoading={bridge.gitRepoLoading}
        gitBranchSwitching={bridge.gitBranchSwitching}
        refreshGitRepoState={bridge.loadGitRepoState}
        switchGitBranch={bridge.switchGitBranch}
        createGitBranch={bridge.createGitBranch}
        prefillText={bridge.prefillText}
        {pendingRevision}
        allowRevision={!activeDebugSessionPath && bridge.connectionStatus === "connected"}
        pendingMessageCount={displayedPendingMessageCount}
        queuedUserMessages={displayedQueuedUserMessages}
        {editQueuedPayload}
        onSubmit={handlePrompt}
        onLoadOlderTranscript={bridge.loadOlderTranscriptPage}
        onAbort={handleAbort}
        onReviseMessage={handleReviseMessage}
        onCancelRevision={handleCancelRevision}
        onCancelQueued={handleCancelQueued}
        onEditQueued={handleEditQueued}
        onSelectModel={handleModelSelect}
        onSelectThinkingLevel={handleThinkingLevelSelect}
        onToggleAutoCompaction={handleAutoCompactionToggle}
        onOpenFileReference={handleOpenFileReference}
        readWorkspaceFile={readDisplayedWorkspaceFile}
      />

    </div>
  </div>

  {#if showRightRailResizer}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <div
      class="rail-resizer right"
      class:active={activeRailResize?.side === "right"}
      style={rightRailResizerStyle}
      role="separator"
      aria-label="Resize right sidebar. Double-click to reset."
      tabindex="0"
      title="Drag to resize the right sidebar. Double-click to reset."
      onpointerdown={(e) => startRailResize("right", e)}
      ondblclick={() => resetRailWidth("right")}
    ></div>
  {/if}

  {#if hasRightSidebarContent && outlineSidebarOpen}
    <AppRightSidebar
      treeEntries={displayedTreeEntries}
      sidebarOpen={outlineSidebarOpen}
      sessionPath={displayedActiveSessionPath}
      hasTreeTab={displayedHasSessionOutline}
      activeTabId={activeRightSidebarTabId}
      activeFileTab={activeFileViewerTab}
      {fileViewerTabs}
      readWorkspaceFile={readDisplayedWorkspaceFile}
      onCloseSidebar={() => (outlineSidebarOpen = false)}
      onSelectTab={handleRightSidebarTabSelect}
      onCloseFileTab={closeFileViewerTab}
      onSelectTreeEntry={handleTreeEntrySelect}
    />
  {/if}

  <AppNotifications
    connectionError={bridge.connectionError}
    notifications={bridge.notifications}
    onDismiss={handleDismissNotification}
  />

  <ThemeSettingsDialog
    open={themeSettingsOpen}
    mode={themePreference.mode}
    darkThemeId={themePreference.darkThemeId}
    lightThemeId={themePreference.lightThemeId}
    {darkThemes}
    {lightThemes}
    themeStyle={allStyle}
    onClose={closeThemeSettings}
    onSetTheme={handleThemePresetSelect}
  />

  <ExtensionDialog
    request={bridge.pendingExtensionRequest}
    onRespond={handleUIRespond}
  />
</div>

<style>
  .app-shell {
    --pi-font-sans:
      -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    --pi-font-mono:
      ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono",
      monospace;
    display: grid;
    grid-template-columns: clamp(280px, 24vw, 360px) minmax(0, 1fr);
    height: 100vh;
    height: 100dvh;
    width: 100vw;
    overflow: hidden;
    background: var(--bg, #0d1117);
    color: var(--text, #e6edf3);
    font-family: var(--pi-font-sans);
    position: relative;
  }

  .rail-resizer {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 10px;
    cursor: col-resize;
    z-index: 25;
    touch-action: none;
  }


  .app-shell ::selection {
    background: var(--selection-bg);
  }

  .app-shell[data-theme-mode="dark"] :global(pre.shiki) {
    background-color: var(--shiki-dark-bg) !important;
  }

  .app-shell[data-theme-mode="dark"] :global(pre.shiki),
  .app-shell[data-theme-mode="dark"] :global(pre.shiki span) {
    color: var(--shiki-dark) !important;
  }

  .app-shell.left-rail-collapsed {
    grid-template-columns: minmax(0, 1fr);
  }

  .app-shell.left-rail-collapsed .app-main-column {
    background: var(--bg);
  }

  .app-main-column {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    background: var(--rail-bg);
  }

  .app-body {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    flex: 1;
    min-height: 0;
    overflow: hidden;
    position: relative;
  }

  @media (max-width: 900px) {
    .app-shell {
      --mobile-header-offset: calc(env(safe-area-inset-top) + 50px);
      display: flex;
      flex-direction: column;
    }

    .rail-resizer {
      display: none;
    }

    .app-body {
      grid-template-columns: 1fr;
      position: relative;
    }

    .app-main-column {
      flex: 1;
    }
  }
</style>
