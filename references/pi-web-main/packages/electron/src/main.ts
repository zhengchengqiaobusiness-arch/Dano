import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  startStandaloneBridge,
  type StandaloneBridgeController,
} from "@pi-web/bridge/standalone/server";
import { DEFAULT_BRIDGE_CONFIG } from "@pi-web/bridge/types";
import {
  app,
  BrowserWindow,
  Menu,
  dialog,
  ipcMain,
  nativeTheme,
  shell,
  type BrowserWindowConstructorOptions,
  type MenuItemConstructorOptions,
  type OpenDialogOptions,
} from "electron";
import {
  DESKTOP_CHANNELS,
  type DesktopBootstrap,
  type DesktopMenuAction,
  type DesktopThemeMode,
  type DesktopThemeSource,
  type DesktopTitleBarStyle,
} from "./ipc.js";

const DEV_RENDERER_URL = "http://127.0.0.1:5173";
const DEV_BRIDGE_PORT = 8080;
const DEV_BRIDGE_HOST = "127.0.0.1";
const DEFAULT_WINDOW_WIDTH = 1440;
const DEFAULT_WINDOW_HEIGHT = 960;
const MIN_WINDOW_WIDTH = 1100;
const MIN_WINDOW_HEIGHT = 720;
const WINDOW_STATE_FILE = "window-state.json";
const projectRoot = fileURLToPath(new URL("../../..", import.meta.url));
const preloadFile = fileURLToPath(new URL("./preload.cjs", import.meta.url));

interface WindowState {
  width: number;
  height: number;
  x?: number;
  y?: number;
  isMaximized: boolean;
}

let mainWindow: BrowserWindow | null = null;
let bridgeController: StandaloneBridgeController | null = null;
let shuttingDown = false;
let windowStateSaveTimer: ReturnType<typeof setTimeout> | null = null;

function readIntEnv(name: string, fallback: number): number {
  const raw = process.env[name]?.trim();
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function resolveRendererUrl(): string | null {
  const raw = process.env.PI_WEB_ELECTRON_RENDERER_URL?.trim();
  if (raw) return raw;
  return app.isPackaged ? null : DEV_RENDERER_URL;
}

function resolveWorkspacePath(): string {
  const raw = process.env.PI_WEB_ELECTRON_WORKSPACE?.trim();
  if (raw) return raw;

  // Packaged builds need a safe default until a workspace picker is added.
  return app.isPackaged ? app.getPath("home") : projectRoot;
}

function resolveStaticDir(): string {
  return app.isPackaged
    ? join(process.resourcesPath, "web-dist")
    : join(projectRoot, "web-dist");
}

function resolveTitleBarStyle(): DesktopTitleBarStyle {
  if (process.platform === "darwin") {
    return "hiddenInset";
  }

  // Keep the native Windows title bar so caption buttons and snap behavior
  // match the platform instead of relying on a custom overlay header.
  return "system";
}

function getSystemTheme(): DesktopThemeMode {
  return nativeTheme.shouldUseDarkColors ? "dark" : "light";
}

function getDesktopBootstrap(): DesktopBootstrap {
  return {
    platform: process.platform,
    systemTheme: getSystemTheme(),
    titleBarStyle: resolveTitleBarStyle(),
  };
}

function getWindowStatePath(): string {
  return join(app.getPath("userData"), WINDOW_STATE_FILE);
}

function readWindowState(): WindowState {
  const fallback: WindowState = {
    width: DEFAULT_WINDOW_WIDTH,
    height: DEFAULT_WINDOW_HEIGHT,
    isMaximized: false,
  };

  try {
    const raw = readFileSync(getWindowStatePath(), "utf8");
    const parsed = JSON.parse(raw) as Partial<WindowState>;

    return {
      width:
        typeof parsed.width === "number" && parsed.width >= MIN_WINDOW_WIDTH
          ? parsed.width
          : fallback.width,
      height:
        typeof parsed.height === "number" && parsed.height >= MIN_WINDOW_HEIGHT
          ? parsed.height
          : fallback.height,
      x: typeof parsed.x === "number" ? parsed.x : undefined,
      y: typeof parsed.y === "number" ? parsed.y : undefined,
      isMaximized: parsed.isMaximized === true,
    };
  } catch {
    return fallback;
  }
}

function writeWindowState(win: BrowserWindow): void {
  if (win.isMinimized() || win.isFullScreen()) {
    return;
  }

  const bounds = win.isMaximized() ? win.getNormalBounds() : win.getBounds();
  const state: WindowState = {
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    isMaximized: win.isMaximized(),
  };

  const stateFile = getWindowStatePath();
  mkdirSync(dirname(stateFile), { recursive: true });
  writeFileSync(stateFile, JSON.stringify(state, null, 2));
}

function scheduleWindowStateSave(win: BrowserWindow): void {
  if (windowStateSaveTimer) {
    clearTimeout(windowStateSaveTimer);
  }

  windowStateSaveTimer = setTimeout(() => {
    writeWindowState(win);
    windowStateSaveTimer = null;
  }, 150);
}

function applyWindowState(options: BrowserWindowConstructorOptions): void {
  const state = readWindowState();
  options.width = state.width;
  options.height = state.height;
  if (typeof state.x === "number") {
    options.x = state.x;
  }
  if (typeof state.y === "number") {
    options.y = state.y;
  }
}

function emitMenuAction(action: DesktopMenuAction): void {
  mainWindow?.webContents.send(DESKTOP_CHANNELS.menuAction, action);
}

function createAppMenu(): Menu {
  const isMac = process.platform === "darwin";
  const template: MenuItemConstructorOptions[] = [];

  if (isMac) {
    template.push({
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    });
  }

  template.push(
    {
      label: "File",
      submenu: [
        {
          label: "Open Workspace...",
          accelerator: "CmdOrCtrl+O",
          click: () => emitMenuAction("open-workspace"),
        },
        {
          label: "New Session",
          accelerator: "CmdOrCtrl+N",
          click: () => emitMenuAction("new-session"),
        },
        {
          label: "Refresh Workspaces",
          accelerator: "CmdOrCtrl+Shift+R",
          click: () => emitMenuAction("refresh-workspaces"),
        },
        ...(isMac ? [] : ([{ type: "separator" }, { role: "quit" }] as const)),
      ],
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Appearance",
      submenu: [
        {
          label: "Toggle Theme",
          accelerator: isMac ? "Cmd+Ctrl+T" : "Ctrl+Alt+T",
          click: () => emitMenuAction("toggle-theme"),
        },
        {
          label: "Theme Settings",
          accelerator: "CmdOrCtrl+,",
          click: () => emitMenuAction("open-theme-settings"),
        },
      ],
    },
    {
      label: "Window",
      submenu: isMac
        ? [
            { role: "minimize" },
            { role: "zoom" },
            { type: "separator" },
            { role: "front" },
          ]
        : [{ role: "minimize" }, { role: "close" }],
    },
    {
      role: "help",
      submenu: [
        {
          label: "Pi Web on GitHub",
          click: () => {
            void shell.openExternal("https://github.com/woxQAQ/pi-web");
          },
        },
      ],
    },
  );

  return Menu.buildFromTemplate(template);
}

function registerDesktopIpc(): void {
  ipcMain.on(DESKTOP_CHANNELS.getBootstrap, event => {
    event.returnValue = getDesktopBootstrap();
  });

  ipcMain.handle(DESKTOP_CHANNELS.pickWorkspace, async () => {
    const options: OpenDialogOptions = {
      title: "Open Workspace",
      properties: ["openDirectory", "createDirectory"],
      defaultPath: resolveWorkspacePath(),
    };
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options);

    return result.canceled ? null : (result.filePaths[0] ?? null);
  });

  ipcMain.handle(
    DESKTOP_CHANNELS.setNativeTheme,
    async (_event, source: DesktopThemeSource) => {
      nativeTheme.themeSource = source;
    },
  );
}

function setupExternalNavigation(win: BrowserWindow, appUrl: string): void {
  const appOrigin = new URL(appUrl).origin;

  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  win.webContents.on("will-navigate", (event, url) => {
    try {
      if (new URL(url).origin === appOrigin) {
        return;
      }
    } catch {
      return;
    }

    event.preventDefault();
    void shell.openExternal(url);
  });
}

async function ensureBridgeStarted(): Promise<StandaloneBridgeController> {
  if (bridgeController) {
    return bridgeController;
  }

  const staticDir = resolveStaticDir();
  const rendererUrl = resolveRendererUrl();
  const preferredPort = rendererUrl
    ? readIntEnv("PI_WEB_ELECTRON_BRIDGE_PORT", DEV_BRIDGE_PORT)
    : 0;

  bridgeController = await startStandaloneBridge(
    {
      ...DEFAULT_BRIDGE_CONFIG,
      host: DEV_BRIDGE_HOST,
      port: preferredPort,
      staticDir: existsSync(staticDir) ? staticDir : undefined,
    },
    {
      captureSigint: false,
      cwd: resolveWorkspacePath(),
    },
  );

  return bridgeController;
}

function createWindowOptions(): BrowserWindowConstructorOptions {
  const titleBarStyle = resolveTitleBarStyle();
  const options: BrowserWindowConstructorOptions = {
    minWidth: MIN_WINDOW_WIDTH,
    minHeight: MIN_WINDOW_HEIGHT,
    show: false,
    backgroundColor: "#0f1117",
    autoHideMenuBar: process.platform !== "darwin",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: preloadFile,
    },
  };

  applyWindowState(options);

  if (titleBarStyle === "hiddenInset") {
    options.titleBarStyle = "hiddenInset";
  } else if (titleBarStyle === "overlay") {
    options.titleBarStyle = "hidden";
    options.titleBarOverlay = {
      color: "#00000000",
      symbolColor: "#d0d7de",
      height: 40,
    };
    options.backgroundMaterial = "mica";
  }

  return options;
}

function attachWindowStatePersistence(win: BrowserWindow): void {
  win.on("resize", () => scheduleWindowStateSave(win));
  win.on("move", () => scheduleWindowStateSave(win));
  win.on("close", () => writeWindowState(win));
}

async function createMainWindow(): Promise<void> {
  const bridge = await ensureBridgeStarted();
  const bridgeUrl = bridge.getBridgeUrl();
  const rendererUrl = resolveRendererUrl() ?? bridgeUrl;

  if (!rendererUrl) {
    throw new Error("Bridge started without a reachable URL");
  }

  mainWindow = new BrowserWindow(createWindowOptions());
  attachWindowStatePersistence(mainWindow);
  setupExternalNavigation(mainWindow, rendererUrl);

  if (process.platform === "darwin") {
    mainWindow.setSheetOffset(52);
  }

  mainWindow.once("ready-to-show", () => {
    if (!mainWindow) {
      return;
    }

    if (readWindowState().isMaximized) {
      mainWindow.maximize();
    }
    mainWindow.show();
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  await mainWindow.loadURL(rendererUrl);
}

async function stopBridge(): Promise<void> {
  if (!bridgeController) {
    return;
  }

  const current = bridgeController;
  bridgeController = null;
  await current.stop();
}

nativeTheme.on("updated", () => {
  const nextTheme = getSystemTheme();
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send(DESKTOP_CHANNELS.systemThemeChanged, nextTheme);
  }
});

app.whenReady().then(async () => {
  registerDesktopIpc();
  Menu.setApplicationMenu(createAppMenu());
  await createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createMainWindow();
    }
  });
});

app.on("before-quit", event => {
  if (shuttingDown || !bridgeController) {
    return;
  }

  shuttingDown = true;
  event.preventDefault();
  void stopBridge().finally(() => {
    app.quit();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
