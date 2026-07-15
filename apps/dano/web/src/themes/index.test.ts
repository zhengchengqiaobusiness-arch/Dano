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

  it("uses one white send-button icon token across theme modes", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars["--send-button-icon"]).toBe("#ffffff");
    expect(lightVars["--send-button-icon"]).toBe("#ffffff");
  });

  it("derives hover and dock colors from base theme tokens", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars["--accent-hover"]).toBe(darkVars["--accent"]);
    expect(lightVars["--accent-hover"]).toBe(lightVars["--accent"]);
    expect(darkVars["--panel"]).toBe("#212121");
  });

  it("keeps canvas, controls, code, and accent foregrounds semantically separate", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(lightVars["--bg"]).toBe("#fbfbfb");
    expect(lightVars["--control-bg"]).toBe("#ffffff");
    expect(lightVars["--code-bg"]).toBe("#ffffff");
    expect(lightVars["--on-accent"]).toBe("#ffffff");
    expect(darkVars["--bg"]).toBe("#0d1117");
  });

  it("uses a dark shadow source in the light theme", () => {
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(lightVars["--shadow-raised"]).toBe(
      "0 8px 24px rgba(0, 0, 0, 0.08)",
    );
  });
});
