import { getRuntimeLocale } from "../utils/runtimeConfig";
import { translate, type TranslationParams } from "./translate";

export {
  DEFAULT_LOCALE,
  SUPPORTED_LOCALES,
  normalizeLocale,
  type Locale,
} from "./locales";
export { translate, type TranslationParams } from "./translate";

export function t(key: string, params?: TranslationParams): string {
  return translate(key, { locale: getRuntimeLocale(), params });
}
