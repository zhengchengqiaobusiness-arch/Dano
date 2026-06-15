export type EmptyStateMode = "text" | "html";

export interface EmptyStateConfig {
  mode: EmptyStateMode;
  content: string;
}

const DEFAULT_PRODUCT_NAME = "Dano";
const DEFAULT_EMPTY_STATE_CONTENT = "给 {产品名称} 发消息";

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

export function getRuntimeEmptyStateConfig(): EmptyStateConfig {
  const productName = getRuntimeProductName();
  const configured = runtimeConfig()?.emptyState;
  const mode = configured?.mode === "html" ? "html" : "text";
  const rawContent =
    typeof configured?.content === "string" && configured.content.trim()
      ? configured.content
      : DEFAULT_EMPTY_STATE_CONTENT;

  return {
    mode,
    content: interpolateProductName(rawContent, productName),
  };
}
