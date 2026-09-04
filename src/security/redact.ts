const SENSITIVE_HEADER = /^(authorization|proxy-authorization|cookie|set-cookie)$/i;
const SENSITIVE_KEY = /(password|passwd|pwd|secret|token|api[-_]?key|access[-_]?key|refresh[-_]?token|session|credential)/i;

export function isSecretBearingHeader(name: string) {
  return SENSITIVE_HEADER.test(name) || SENSITIVE_KEY.test(name);
}

export function redactHeaders(headers: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(headers).map(([key, value]) => [
      key,
      isSecretBearingHeader(key) ? "[REDACTED]" : value
    ])
  );
}

export function redactValue(value: unknown, keyHint = ""): unknown {
  if (SENSITIVE_KEY.test(keyHint)) return "[REDACTED]";
  if (Array.isArray(value)) return value.map(item => redactValue(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, child]) => [
        key,
        redactValue(child, key)
      ])
    );
  }
  return value;
}

export function parsePossiblyJson(text: string | null | undefined): unknown {
  if (!text) return undefined;
  try {
    return redactValue(JSON.parse(text));
  } catch {
    if (text.length > 16_384) return `${text.slice(0, 16_384)}…[TRUNCATED]`;
    return text;
  }
}
