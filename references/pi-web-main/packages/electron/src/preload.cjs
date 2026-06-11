const { contextBridge, ipcRenderer } = require("electron");

const DESKTOP_CHANNELS = {
  getBootstrap: "pi-web:desktop:get-bootstrap",
  menuAction: "pi-web:desktop:menu-action",
  pickWorkspace: "pi-web:desktop:pick-workspace",
  setNativeTheme: "pi-web:desktop:set-native-theme",
  systemThemeChanged: "pi-web:desktop:system-theme-changed",
};

const bootstrap = ipcRenderer.sendSync(DESKTOP_CHANNELS.getBootstrap);

function subscribeToChannel(channel, callback) {
  const listener = (_event, payload) => {
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
  pickWorkspace() {
    return ipcRenderer.invoke(DESKTOP_CHANNELS.pickWorkspace);
  },
  setNativeTheme(source) {
    return ipcRenderer.invoke(DESKTOP_CHANNELS.setNativeTheme, source);
  },
  onMenuAction(callback) {
    return subscribeToChannel(DESKTOP_CHANNELS.menuAction, callback);
  },
  onSystemThemeChange(callback) {
    return subscribeToChannel(DESKTOP_CHANNELS.systemThemeChanged, callback);
  },
});
