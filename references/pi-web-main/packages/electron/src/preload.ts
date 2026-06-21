import { contextBridge, ipcRenderer } from "electron";
import {
  DESKTOP_CHANNELS,
  type DesktopBootstrap,
  type DesktopMenuAction,
  type DesktopThemeMode,
  type DesktopThemeSource,
} from "./ipc.js";

const bootstrap = ipcRenderer.sendSync(
  DESKTOP_CHANNELS.getBootstrap,
) as DesktopBootstrap;

function subscribeToChannel<T>(
  channel: string,
  callback: (payload: T) => void,
): () => void {
  const listener = (_event: unknown, payload: T) => {
    callback(payload);
  };

  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.off(channel, listener);
  };
}

contextBridge.exposeInMainWorld("piDesktop", {
  isDesktop: true,
  platform: bootstrap.platform,
  systemTheme: bootstrap.systemTheme,
  titleBarStyle: bootstrap.titleBarStyle,
  pickWorkspace(): Promise<string | null> {
    return ipcRenderer.invoke(DESKTOP_CHANNELS.pickWorkspace) as Promise<
      string | null
    >;
  },
  setNativeTheme(source: DesktopThemeSource): Promise<void> {
    return ipcRenderer.invoke(
      DESKTOP_CHANNELS.setNativeTheme,
      source,
    ) as Promise<void>;
  },
  onMenuAction(callback: (action: DesktopMenuAction) => void): () => void {
    return subscribeToChannel<DesktopMenuAction>(
      DESKTOP_CHANNELS.menuAction,
      callback,
    );
  },
  onSystemThemeChange(callback: (mode: DesktopThemeMode) => void): () => void {
    return subscribeToChannel<DesktopThemeMode>(
      DESKTOP_CHANNELS.systemThemeChanged,
      callback,
    );
  },
});
