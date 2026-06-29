import { describe, expect, it } from "vitest";
import { PI_BASE46_DARK_THEME } from "./dark";
import { resolveAppThemeVars } from "./index";
import { PI_BASE46_LIGHT_THEME } from "./light";

describe("resolveAppThemeVars", () => {
  it("does not emit a separate send button background token", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars).not.toHaveProperty("--send-button-bg");
    expect(lightVars).not.toHaveProperty("--send-button-bg");
  });

  it("does not emit a separate user message background token", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars).not.toHaveProperty("--user-message-bg");
    expect(lightVars).not.toHaveProperty("--user-message-bg");
  });

  it("does not emit a separate composer dock background token", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars).not.toHaveProperty("--composer-dock-bg");
    expect(lightVars).not.toHaveProperty("--composer-dock-bg");
  });

  it("keeps default Pi accents on the send-button colors", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars["--accent"]).toBe("#48a04c");
    expect(lightVars["--accent"]).toBe("#53b559");
  });

  it("derives hover and dock colors from base theme tokens", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars["--accent-hover"]).toBe(darkVars["--accent"]);
    expect(lightVars["--accent-hover"]).toBe(lightVars["--accent"]);
    expect(darkVars["--panel"]).toBe("#212121");
  });
});
