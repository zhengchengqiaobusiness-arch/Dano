/** @vitest-environment happy-dom */

import { mount, tick, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

const bridge = vi.hoisted(() => ({
  abortGeneration: vi.fn(),
  accentColorPreset: "green",
  activeSessionPath: "/sessions/history.jsonl",
  authentication: { status: "anonymous" },
  availableModels: [],
  cancelQueuedMessage: vi.fn(),
  commands: [],
  compactSession: vi.fn(),
  connectionError: null,
  connectionStatus: "connected",
  createGitBranch: vi.fn(),
  currentModel: null,
  currentThinkingLevel: "medium",
  currentUser: null,
  deleteSession: vi.fn(),
  disconnect: vi.fn(),
  dismissNotification: vi.fn(),
  editQueuedMessage: vi.fn(),
  explicitNewSession: vi.fn(),
  fetchWorkspaceEntries: vi.fn(),
  fieldAssist: vi.fn(),
  gitBranchSwitching: false,
  gitRepoLoading: false,
  gitRepoState: null,
  hasSessionOutline: true,
  isCompacting: false,
  isPromptPending: false,
  isReconnecting: false,
  isStreaming: false,
  lastDisconnectReason: null,
  loadGitRepoState: vi.fn(),
  loadOlderTranscriptPage: vi.fn(),
  loadWorkspaceSessions: vi.fn(),
  newSession: vi.fn(),
  notifications: [],
  pendingExtensionRequest: null,
  pendingMessageCount: 0,
  pendingTranscriptConfigEvent: null,
  prefillText: "",
  queuedUserMessages: [],
  readWorkspaceFile: vi.fn(),
  reconnect: vi.fn(),
  reconnectCount: 0,
  refreshWorkspaces: vi.fn().mockResolvedValue(undefined),
  registerWorkspace: vi.fn(),
  respondToUIRequest: vi.fn(),
  runningSessionPaths: [],
  sendCommand: vi.fn(),
  sendPrompt: vi.fn(),
  sessionState: {
    sessionFile: "/sessions/history.jsonl",
    sessionId: "history-session",
    thinkingLevel: "medium",
    isStreaming: false,
    isCompacting: false,
    steeringMode: "all",
    followUpMode: "all",
    autoCompactionEnabled: false,
    messageCount: 2,
    pendingMessageCount: 0,
  },
  sessions: [],
  setAccentColorPreset: vi.fn(),
  setAutoCompactionEnabled: vi.fn(),
  setThinkingLevel: vi.fn(),
  statusEntries: [],
  switchGitBranch: vi.fn(),
  switchSession: vi.fn(),
  transcript: [{ id: "history-node-a", role: "user", content: "historical message" }],
  transcriptDeltas: [],
  transcriptHasOlder: false,
  transcriptInitialLoading: false,
  transcriptPageLoading: false,
  transcriptStreams: [],
  treeEntries: [
    { id: "history-node-a", isActive: true },
    { id: "history-node-b", isActive: false },
  ],
  workspaceEntries: [],
  workspaceEntriesLoading: false,
  workspaceSessionCursors: {},
  workspaceSessionLoaded: {},
  workspaceSessionLoading: {},
  workspaceSessions: {},
  workspaces: [],
}));

vi.mock("./composables/bridgeStore.svelte", () => ({
  initBridge: () => bridge,
  TRANSCRIPT_PAGE_LIMIT: 50,
}));

vi.mock("./layout/AppMainContent.svelte", async () => ({
  default: (await import("./test/AppRevisionMainContentHarness.svelte")).default,
}));

vi.mock("./components/ExtensionDialog.svelte", async () => ({
  default: (await import("./test/EmptyComponentHarness.svelte")).default,
}));
vi.mock("./components/ReconnectBanner.svelte", async () => ({
  default: (await import("./test/EmptyComponentHarness.svelte")).default,
}));
vi.mock("./components/ReauthenticationDialog.svelte", async () => ({
  default: (await import("./test/EmptyComponentHarness.svelte")).default,
}));
vi.mock("./components/ThemeSettingsDialog.svelte", async () => ({
  default: (await import("./test/EmptyComponentHarness.svelte")).default,
}));
vi.mock("./components/MemorySettingsDialog.svelte", async () => ({
  default: (await import("./test/EmptyComponentHarness.svelte")).default,
}));
vi.mock("./layout/AppHeader.svelte", async () => ({
  default: (await import("./test/EmptyComponentHarness.svelte")).default,
}));
vi.mock("./layout/AppNotifications.svelte", async () => ({
  default: (await import("./test/EmptyComponentHarness.svelte")).default,
}));
vi.mock("./layout/AppRightSidebar.svelte", async () => ({
  default: (await import("./test/EmptyComponentHarness.svelte")).default,
}));

describe("App historical-message editing", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    document.body.replaceChildren();
  });

  it("preserves the selected history node and session without navigating on enter or cancel", async () => {
    vi.stubGlobal("__PI_WEB_DEV_DEBUG__", false);
    const { default: App } = await import("./App.svelte");
    const target = document.createElement("div");
    document.body.appendChild(target);
    const selectedEntry = () => bridge.treeEntries.find(entry => entry.isActive)?.id;
    const component = mount(App, { target });

    try {
      await tick();
      expect(target.querySelector('[data-testid="active-session"]')?.textContent)
        .toBe("/sessions/history.jsonl");
      expect(selectedEntry()).toBe("history-node-a");

      target.querySelector<HTMLButtonElement>('[data-testid="begin-revision"]')?.click();
      await tick();
      expect(target.querySelector('[data-testid="pending-revision"]')?.textContent)
        .toBe("history-node-a");
      expect(bridge.sendCommand).not.toHaveBeenCalled();
      expect(bridge.activeSessionPath).toBe("/sessions/history.jsonl");
      expect(selectedEntry()).toBe("history-node-a");

      target.querySelector<HTMLButtonElement>('[data-testid="cancel-revision"]')?.click();
      await tick();
      expect(target.querySelector('[data-testid="pending-revision"]')?.textContent)
        .toBe("none");
      expect(bridge.sendCommand).not.toHaveBeenCalled();
      expect(bridge.activeSessionPath).toBe("/sessions/history.jsonl");
      expect(selectedEntry()).toBe("history-node-a");
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("navigates to the historical node before sending the edited prompt", async () => {
    vi.stubGlobal("__PI_WEB_DEV_DEBUG__", false);
    const calls: string[] = [];
    bridge.sendCommand.mockImplementationOnce(async command => {
      calls.push(`navigate:${command.entryId}`);
      return { success: true };
    });
    bridge.sendPrompt.mockImplementationOnce(async (message: string) => {
      calls.push(`send:${message}`);
      return true;
    });
    const { default: App } = await import("./App.svelte");
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(App, { target });

    try {
      await tick();
      target.querySelector<HTMLButtonElement>('[data-testid="begin-revision"]')?.click();
      await tick();
      target.querySelector<HTMLButtonElement>('[data-testid="submit-revision"]')?.click();
      await vi.waitFor(() => expect(bridge.sendPrompt).toHaveBeenCalledTimes(1));

      expect(calls).toEqual([
        "navigate:history-node-a",
        "send:edited historical message",
      ]);
      expect(target.querySelector('[data-testid="pending-revision"]')?.textContent)
        .toBe("none");
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it.each([
    ["fails", () => Promise.resolve({ success: false })],
    ["is cancelled", () => Promise.resolve({ success: true, data: { cancelled: true } })],
    ["rejects", () => Promise.reject(new Error("navigation failed"))],
  ])("retains the edit and does not send when history navigation %s", async (_case, navigate) => {
    vi.stubGlobal("__PI_WEB_DEV_DEBUG__", false);
    bridge.sendCommand.mockImplementationOnce(navigate);
    const { default: App } = await import("./App.svelte");
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(App, { target });

    try {
      await tick();
      target.querySelector<HTMLButtonElement>('[data-testid="begin-revision"]')?.click();
      await tick();
      target.querySelector<HTMLButtonElement>('[data-testid="submit-revision"]')?.click();
      await vi.waitFor(() => expect(bridge.sendCommand).toHaveBeenCalledTimes(1));

      expect(bridge.sendPrompt).not.toHaveBeenCalled();
      expect(target.querySelector('[data-testid="pending-revision"]')?.textContent)
        .toBe("history-node-a");
    } finally {
      await unmount(component);
      target.remove();
    }
  });

  it("retains the edit when prompt dispatch is rejected", async () => {
    vi.stubGlobal("__PI_WEB_DEV_DEBUG__", false);
    bridge.sendCommand.mockResolvedValueOnce({ success: true });
    bridge.sendPrompt.mockResolvedValueOnce(false);
    const { default: App } = await import("./App.svelte");
    const target = document.createElement("div");
    document.body.appendChild(target);
    const component = mount(App, { target });

    try {
      await tick();
      target.querySelector<HTMLButtonElement>('[data-testid="begin-revision"]')?.click();
      await tick();
      target.querySelector<HTMLButtonElement>('[data-testid="submit-revision"]')?.click();
      await vi.waitFor(() => expect(bridge.sendPrompt).toHaveBeenCalledTimes(1));

      expect(target.querySelector('[data-testid="pending-revision"]')?.textContent)
        .toBe("history-node-a");
    } finally {
      await unmount(component);
      target.remove();
    }
  });
});
