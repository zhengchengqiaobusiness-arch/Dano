/// <reference types="svelte" />
/// <reference types="vite/client" />

declare global {
  const __PI_WEB_DEV_DEBUG__: boolean;

  type PiDesktopPlatform = "darwin" | "linux" | "win32";
  type PiDesktopThemeMode = "dark" | "light";
  type PiDesktopThemeSource = PiDesktopThemeMode | "system";
  type PiDesktopTitleBarStyle = "system" | "hiddenInset" | "overlay";
  type PiDesktopMenuAction =
    | "new-session"
    | "open-theme-settings"
    | "open-workspace"
    | "refresh-workspaces"
    | "toggle-theme";

  interface Window {
    __PI_WEB_CONFIG__?: {
      debugModeAvailable?: boolean;
    };
    piDesktop?: {
      isDesktop: true;
      platform: PiDesktopPlatform;
      systemTheme: PiDesktopThemeMode;
      titleBarStyle: PiDesktopTitleBarStyle;
      pickWorkspace(): Promise<string | null>;
      setNativeTheme(source: PiDesktopThemeSource): Promise<void>;
      onMenuAction(callback: (action: PiDesktopMenuAction) => void): () => void;
      onSystemThemeChange(
        callback: (mode: PiDesktopThemeMode) => void,
      ): () => void;
    };
  }
}

export {};
