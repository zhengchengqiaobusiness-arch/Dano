import type { RpcThinkingLevel } from "@pi-web/bridge/types";

export const DEFAULT_THINKING_LEVEL: RpcThinkingLevel = "off";

export const THINKING_LEVEL_OPTIONS: readonly {
  value: RpcThinkingLevel;
  label: string;
}[] = [
  { value: "off", label: "Off" },
  { value: "minimal", label: "Minimal" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "XHigh" },
];

function thinkingLevelIndex(
  currentLevel: RpcThinkingLevel | null | undefined,
): number {
  const normalizedLevel = currentLevel ?? DEFAULT_THINKING_LEVEL;
  const currentIndex = THINKING_LEVEL_OPTIONS.findIndex(
    option => option.value === normalizedLevel,
  );
  return currentIndex >= 0 ? currentIndex : 0;
}

export function getNextThinkingLevel(
  currentLevel: RpcThinkingLevel | null | undefined,
): RpcThinkingLevel {
  const currentIndex = thinkingLevelIndex(currentLevel);
  return THINKING_LEVEL_OPTIONS[
    (currentIndex + 1) % THINKING_LEVEL_OPTIONS.length
  ]!.value;
}

export function getPreviousThinkingLevel(
  currentLevel: RpcThinkingLevel | null | undefined,
): RpcThinkingLevel {
  const currentIndex = thinkingLevelIndex(currentLevel);
  return THINKING_LEVEL_OPTIONS[
    (currentIndex - 1 + THINKING_LEVEL_OPTIONS.length) %
      THINKING_LEVEL_OPTIONS.length
  ]!.value;
}
