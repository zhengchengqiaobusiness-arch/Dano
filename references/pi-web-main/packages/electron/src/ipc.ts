export type DesktopThemeMode = "dark" | "light";
export type DesktopThemeSource = DesktopThemeMode | "system";
export type DesktopTitleBarStyle = "system" | "hiddenInset" | "overlay";
export type DesktopMenuAction =
  | "new-session"
  | "open-theme-settings"
  | "open-workspace"
  | "refresh-workspaces"
  | "toggle-theme";

export interface DesktopBootstrap {
  platform: NodeJS.Platform;
  systemTheme: DesktopThemeMode;
  titleBarStyle: DesktopTitleBarStyle;
}

export const DESKTOP_CHANNELS = {
  getBootstrap: "pi-web:desktop:get-bootstrap",
  menuAction: "pi-web:desktop:menu-action",
  pickWorkspace: "pi-web:desktop:pick-workspace",
  setNativeTheme: "pi-web:desktop:set-native-theme",
  systemThemeChanged: "pi-web:desktop:system-theme-changed",
} as const;
