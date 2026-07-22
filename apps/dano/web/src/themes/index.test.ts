import { describe, expect, it } from "vitest";
import { ACCENT_COLOR_PRESETS } from "./accent-color";
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

  it("uses one default Theme Color across theme modes", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars["--accent"]).toBe("#53b559");
    expect(lightVars["--accent"]).toBe("#53b559");
    expect(darkVars["--on-accent"]).toBe("#ffffff");
    expect(lightVars["--on-accent"]).toBe("#ffffff");
  });

  it("uses one white send-button icon token across theme modes", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars["--send-button-icon"]).toBe("#ffffff");
    expect(lightVars["--send-button-icon"]).toBe("#ffffff");
  });

  it("derives accent hover from Theme Color while keeping panel semantics", () => {
    const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME);

    expect(darkVars["--accent-hover"]).toBe(darkVars["--accent"]);
    expect(lightVars["--accent-hover"]).toBe(lightVars["--accent"]);
    expect(darkVars["--panel"]).toBe("#212121");
  });

  it("keeps non-accent Pi Base46 semantics unchanged", () => {
    const defaultVars = resolveAppThemeVars(PI_BASE46_DARK_THEME);
    const customVars = resolveAppThemeVars(
      PI_BASE46_DARK_THEME,
      ACCENT_COLOR_PRESETS.purple,
    );

    for (const variable of [
      "--bg",
      "--panel",
      "--control-bg",
      "--code-bg",
      "--text",
      "--success",
      "--warning",
      "--danger",
    ]) {
      expect(customVars[variable]).toBe(defaultVars[variable]);
    }
  });

  it.each(Object.entries(ACCENT_COLOR_PRESETS))(
    "derives every observable accent variable from the %s Theme Color",
    (_preset, accent) => {
      const darkVars = resolveAppThemeVars(PI_BASE46_DARK_THEME, accent);
      const lightVars = resolveAppThemeVars(PI_BASE46_LIGHT_THEME, accent);

      expect(darkVars["--accent"]).toBe(accent);
      expect(lightVars["--accent"]).toBe(accent);
      expect(darkVars["--accent-hover"]).toBe(accent);
      expect(lightVars["--accent-hover"]).toBe(accent);
      expect(darkVars["--on-accent"]).toBe(lightVars["--on-accent"]);

      for (const variable of [
        "--surface-active",
        "--focus-ring",
        "--selection-bg",
      ]) {
        expect(darkVars[variable]).toMatch(/^rgba\(/);
        expect(lightVars[variable]).toMatch(/^rgba\(/);
        expect(darkVars[variable]).not.toContain(
          PI_BASE46_DARK_THEME.base16.base0D,
        );
        expect(lightVars[variable]).not.toContain(
          PI_BASE46_LIGHT_THEME.base16.base0D,
        );
      }
    },
  );

  it("preserves an alpha Theme Color in derived state opacity", () => {
    const vars = resolveAppThemeVars(
      PI_BASE46_LIGHT_THEME,
      ACCENT_COLOR_PRESETS.gray,
    );

    expect(vars["--accent"]).toBe("#e9e9e980");
    expect(vars["--surface-active"]).toBe("rgba(233, 233, 233, 0.0703)");
    expect(vars["--focus-ring"]).toBe("rgba(233, 233, 233, 0.1405)");
    expect(vars["--selection-bg"]).toBe("rgba(233, 233, 233, 0.0803)");
    expect(vars["--on-accent"]).toBe("#0d1117");
  });

  it("changes every derived state when the Theme Color changes", () => {
    const blueVars = resolveAppThemeVars(
      PI_BASE46_DARK_THEME,
      ACCENT_COLOR_PRESETS.blue,
    );
    const pinkVars = resolveAppThemeVars(
      PI_BASE46_DARK_THEME,
      ACCENT_COLOR_PRESETS.pink,
    );

    for (const variable of [
      "--accent",
      "--accent-hover",
      "--surface-active",
      "--focus-ring",
      "--selection-bg",
    ]) {
      expect(blueVars[variable]).not.toBe(pinkVars[variable]);
    }
  });

  it("derives all observable variables from a future custom alpha color", () => {
    const vars = resolveAppThemeVars(PI_BASE46_DARK_THEME, "#12345680");

    expect(vars["--accent"]).toBe("#12345680");
    expect(vars["--accent-hover"]).toBe("#12345680");
    expect(vars["--surface-active"]).toBe("rgba(18, 52, 86, 0.0753)");
    expect(vars["--focus-ring"]).toBe("rgba(18, 52, 86, 0.1757)");
    expect(vars["--selection-bg"]).toBe("rgba(18, 52, 86, 0.1104)");
    expect(vars["--on-accent"]).toBe("#ffffff");
  });

  it("falls back to the default Theme Color for invalid input", () => {
    const vars = resolveAppThemeVars(PI_BASE46_DARK_THEME, "not-a-color");

    expect(vars["--accent"]).toBe(ACCENT_COLOR_PRESETS.default);
    expect(vars["--on-accent"]).toBe("#ffffff");
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
