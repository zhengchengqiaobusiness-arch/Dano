import { DEFAULT_LOCALE, normalizeLocale, type Locale } from "./locales";
import { messages } from "./messages";

export type TranslationParams = Record<
  string,
  string | number | boolean | null | undefined
>;

export interface TranslateOptions {
  locale?: Locale | string | null;
  params?: TranslationParams;
}

function interpolate(message: string, params?: TranslationParams): string {
  if (!params) return message;

  return message.replace(/\{([^{}]+)\}/g, (placeholder, key: string) => {
    const value = params[key];
    return value === undefined || value === null ? placeholder : String(value);
  });
}

export function translate(key: string, options: TranslateOptions = {}): string {
  const locale = normalizeLocale(options.locale);
  const localeMessages = messages[locale];
  const defaultMessages = messages[DEFAULT_LOCALE];
  const message = localeMessages[key] ?? defaultMessages[key];

  return message === undefined ? key : interpolate(message, options.params);
}
