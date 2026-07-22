import { PI_BASE46_DARK_THEME } from "./dark/pi-base46-dark";
import { PI_BASE46_LIGHT_THEME } from "./light/pi-base46-light";
import {
  DEFAULT_ACCENT_COLOR_PRESET,
  type AccentColorPreset,
} from "@dano/types/protocol";

export const ACCENT_COLOR_PRESETS = Object.freeze({
  default: "#53b559",
  blue: "#7aa2f7",
  gray: "#e9e9e980",
  yellow: "#d79921",
  pink: "#eb6f92",
  purple: "#cba6f7",
} as const);

export { DEFAULT_ACCENT_COLOR_PRESET };
export type { AccentColorPreset };

export type ResolvedThemeColor = Readonly<{
  accent: string;
  onAccent: "#ffffff" | "#0d1117";
}>;

type RgbaColor = Readonly<{
  red: number;
  green: number;
  blue: number;
  alpha: number;
}>;

const WHITE = "#ffffff";
const DARK = "#0d1117";
const MINIMUM_WHITE_CONTRAST = 2;
const THEME_BACKGROUNDS = [
  PI_BASE46_LIGHT_THEME.base30.black,
  PI_BASE46_DARK_THEME.base30.black,
] as const;
const resolvedThemeColors = new Map<string, ResolvedThemeColor>();

function parseThemeColor(value: string): RgbaColor | null {
  const normalized = value.trim().toLowerCase();
  if (!/^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/.test(normalized)) return null;

  return {
    red: Number.parseInt(normalized.slice(1, 3), 16),
    green: Number.parseInt(normalized.slice(3, 5), 16),
    blue: Number.parseInt(normalized.slice(5, 7), 16),
    alpha:
      normalized.length === 9
        ? Number.parseInt(normalized.slice(7, 9), 16) / 255
        : 1,
  };
}

function composite(foreground: RgbaColor, background: RgbaColor): RgbaColor {
  return {
    red: foreground.red * foreground.alpha + background.red * (1 - foreground.alpha),
    green:
      foreground.green * foreground.alpha +
      background.green * (1 - foreground.alpha),
    blue:
      foreground.blue * foreground.alpha +
      background.blue * (1 - foreground.alpha),
    alpha: 1,
  };
}

function linearChannel(channel: number): number {
  const normalized = channel / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : ((normalized + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(color: RgbaColor): number {
  return (
    linearChannel(color.red) * 0.2126 +
    linearChannel(color.green) * 0.7152 +
    linearChannel(color.blue) * 0.0722
  );
}

function whiteContrast(color: RgbaColor): number {
  return 1.05 / (relativeLuminance(color) + 0.05);
}

function resolveAccentForeground(accent: RgbaColor): "#ffffff" | "#0d1117" {
  const minimumWhiteContrast = Math.min(
    ...THEME_BACKGROUNDS.map(background => {
      const parsedBackground = parseThemeColor(background);
      if (!parsedBackground) {
        throw new Error(`Invalid Base46 background color: ${background}`);
      }
      return whiteContrast(composite(accent, parsedBackground));
    }),
  );

  return minimumWhiteContrast >= MINIMUM_WHITE_CONTRAST ? WHITE : DARK;
}

function formatAlpha(alpha: number): string {
  return String(Math.round(alpha * 10_000) / 10_000);
}

export function withThemeColorOpacity(value: string, opacity: number): string {
  const parsed = parseThemeColor(value);
  if (!parsed) return value;

  return `rgba(${parsed.red}, ${parsed.green}, ${parsed.blue}, ${formatAlpha(parsed.alpha * opacity)})`;
}

export function resolveThemeColor(value: string): ResolvedThemeColor {
  const normalized = value.trim().toLowerCase();
  const parsed = parseThemeColor(normalized);
  if (!parsed) {
    return resolveAccentColorPreset(DEFAULT_ACCENT_COLOR_PRESET);
  }

  const cached = resolvedThemeColors.get(normalized);
  if (cached) return cached;

  const resolved = Object.freeze({
    accent: normalized,
    onAccent: resolveAccentForeground(parsed),
  });
  resolvedThemeColors.set(normalized, resolved);
  return resolved;
}

export function resolveAccentColorPreset(
  preset: AccentColorPreset,
): ResolvedThemeColor {
  return resolveThemeColor(ACCENT_COLOR_PRESETS[preset]);
}
