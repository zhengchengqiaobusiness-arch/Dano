export type ThemeMode = "dark" | "light";

export type ThemePreference = {
  mode: ThemeMode;
  darkThemeId: string;
  lightThemeId: string;
};

export type Base46Palette = Record<string, string>;
export type Base16Palette = Record<
  | "base00"
  | "base01"
  | "base02"
  | "base03"
  | "base04"
  | "base05"
  | "base06"
  | "base07"
  | "base08"
  | "base09"
  | "base0A"
  | "base0B"
  | "base0C"
  | "base0D"
  | "base0E"
  | "base0F",
  string
>;

export type Base46Theme = {
  id: string;
  label: string;
  mode: ThemeMode;
  base30: Base46Palette;
  base16: Base16Palette;
};

export type ThemePair = {
  dark: Base46Theme;
  light: Base46Theme;
};
