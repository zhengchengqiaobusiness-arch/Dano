import { mount } from "svelte";
import AppHeader from "../layout/AppHeader.svelte";
import { resolveAppThemeVars } from "../themes";
import { PI_BASE46_LIGHT_THEME } from "../themes/light";

for (const [name, value] of Object.entries(
  resolveAppThemeVars(PI_BASE46_LIGHT_THEME),
)) {
  document.documentElement.style.setProperty(name, value);
}

document.body.style.margin = "0";
document.body.style.background = "var(--bg)";
document.body.style.color = "var(--text)";
document.body.style.fontFamily = "system-ui, sans-serif";
document.getElementById("app")!.style.minHeight = "100vh";

const conversation = new URLSearchParams(window.location.search).get("conversation");

mount(AppHeader, {
  target: document.getElementById("app")!,
  props: {
    connectionStatus: "connected",
    showNewSession: conversation === "chat",
    currentUser: { username: "浏览器验收用户" },
  },
});
