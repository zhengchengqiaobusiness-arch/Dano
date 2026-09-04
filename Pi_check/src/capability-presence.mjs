/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 只检查 PI 有没有交出能力列表。不补齐、不改写、不推断内容。
 */

export function piSubmittedCapabilities(result) {
  return Boolean(result && Array.isArray(result.capabilities) && result.capabilities.length > 0);
}

export function capabilityCountFromPiResult(result) {
  if (!piSubmittedCapabilities(result)) return 0;
  return result.capabilities.length;
}
