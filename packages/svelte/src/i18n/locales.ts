export const DEFAULT_LOCALE = "zh-CN" as const;
export const SUPPORTED_LOCALES = ["zh-CN", "en-US"] as const;

export type Locale = (typeof SUPPORTED_LOCALES)[number];

const LOCALE_BY_NORMALIZED_VALUE = new Map<string, Locale>(
  SUPPORTED_LOCALES.map(locale => [normalizeLocaleValue(locale), locale]),
);

function normalizeLocaleValue(value: string): string {
  return value.trim().replaceAll("_", "-").toLowerCase();
}

export function normalizeLocale(value: unknown): Locale {
  if (typeof value !== "string") return DEFAULT_LOCALE;
  return LOCALE_BY_NORMALIZED_VALUE.get(normalizeLocaleValue(value)) ?? DEFAULT_LOCALE;
}
