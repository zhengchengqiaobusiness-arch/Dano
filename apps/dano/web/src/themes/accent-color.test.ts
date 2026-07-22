import { describe, expect, it } from "vitest";
import {
  ACCENT_COLOR_PRESETS,
  DEFAULT_ACCENT_COLOR_PRESET,
  resolveAccentColorPreset,
  resolveThemeColor,
} from "./accent-color";

describe("Theme Color", () => {
  it("exposes the stable Accent Color Presets", () => {
    expect(DEFAULT_ACCENT_COLOR_PRESET).toBe("default");
    expect(ACCENT_COLOR_PRESETS).toEqual({
      default: "#53b559",
      blue: "#7aa2f7",
      gray: "#e9e9e980",
      yellow: "#d79921",
      pink: "#eb6f92",
      purple: "#cba6f7",
    });
  });

  it.each([
    ["default", "#53b559", "#ffffff"],
    ["blue", "#7aa2f7", "#ffffff"],
    ["gray", "#e9e9e980", "#0d1117"],
    ["yellow", "#d79921", "#ffffff"],
    ["pink", "#eb6f92", "#ffffff"],
    ["purple", "#cba6f7", "#ffffff"],
  ] as const)(
    "resolves the %s preset to its shared accent foreground",
    (preset, accent, onAccent) => {
      expect(resolveAccentColorPreset(preset)).toEqual({ accent, onAccent });
    },
  );

  it("accepts future custom opaque and alpha accent colors", () => {
    expect(resolveThemeColor("#B6B6B6")).toEqual({
      accent: "#b6b6b6",
      onAccent: "#ffffff",
    });
    expect(resolveThemeColor("#FFFFFF80")).toEqual({
      accent: "#ffffff80",
      onAccent: "#0d1117",
    });
  });

  it("composites alpha accents against both actual mode backgrounds", () => {
    expect(resolveThemeColor("#00000080").onAccent).toBe("#ffffff");
    expect(resolveThemeColor("#ffffff80").onAccent).toBe("#0d1117");
  });

  it("uses white at the visual threshold and dark below it", () => {
    expect(resolveThemeColor("#b6b6b6").onAccent).toBe("#ffffff");
    expect(resolveThemeColor("#b8b8b8").onAccent).toBe("#0d1117");
  });

  it.each(["", "53b559", "#fff", "#gggggg", "#123456789"])(
    "falls back to the default for invalid input %j",
    value => {
      expect(resolveThemeColor(value)).toBe(
        resolveAccentColorPreset(DEFAULT_ACCENT_COLOR_PRESET),
      );
    },
  );

  it("reuses one resolution while the accent is unchanged", () => {
    expect(resolveThemeColor("#7AA2F7")).toBe(resolveThemeColor("#7aa2f7"));
    expect(resolveThemeColor("#7aa2f7")).not.toBe(
      resolveThemeColor("#53b559"),
    );
  });
});
