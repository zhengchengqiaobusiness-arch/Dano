import type { BridgeQuickActionConfig } from "@dano/types/protocol";
import { normalizeLocale, type Locale } from "../i18n/locales";
import { translate } from "../i18n/translate";

export type EmptyStateMode = "text" | "html";

export interface EmptyStateConfig {
  mode: EmptyStateMode;
  content: string;
}

const DEFAULT_PRODUCT_NAME = "Dano";
const DEFAULT_EMPTY_STATE_KEY = "emptyState.message";

function runtimeConfig() {
  return globalThis.window?.__PI_WEB_CONFIG__;
}

function normalizedProductName(value: unknown): string {
  return typeof value === "string" && value.trim()
    ? value.trim()
    : DEFAULT_PRODUCT_NAME;
}

function interpolateProductName(content: string, productName: string): string {
  return content
    .replaceAll("{产品名称}", productName)
    .replaceAll("{productName}", productName);
}

export function getRuntimeProductName(): string {
  return normalizedProductName(runtimeConfig()?.productName);
}

export function getRuntimeLocale(): Locale {
  return normalizeLocale(runtimeConfig()?.locale);
}

export function getRuntimeEmptyStateConfig(): EmptyStateConfig {
  const productName = getRuntimeProductName();
  const configured = runtimeConfig()?.emptyState;
  const mode = configured?.mode === "html" ? "html" : "text";
  const rawContent =
    typeof configured?.content === "string" && configured.content.trim()
      ? configured.content
      : translate(DEFAULT_EMPTY_STATE_KEY, {
          locale: getRuntimeLocale(),
          params: { productName },
        });

  return {
    mode,
    content: interpolateProductName(rawContent, productName),
  };
}

export function getRuntimeQuickActions(): BridgeQuickActionConfig[] {
  const configured = runtimeConfig()?.quickActions;
  if (!Array.isArray(configured)) return [];

  return configured.flatMap(action => {
    const label = action?.label?.trim();
    const prompt = action?.prompt?.trim();
    return label && prompt ? [{ label, prompt }] : [];
  });
}

export function getRuntimeSlashCommandsAndMentionsEnabled(): boolean {
  return runtimeConfig()?.slashCommandsAndMentionsEnabled === true;
}

export function getRuntimeTranscriptProcessSummaryEnabled(): boolean {
  return runtimeConfig()?.transcriptProcessSummaryEnabled === true;
}
