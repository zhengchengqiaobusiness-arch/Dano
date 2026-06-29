export type FieldAssistAction = "regenerate" | "polish";
export type FieldAssistFieldType = "input" | "textarea";

export interface FieldAssistPayload {
  requestId: string;
  action: FieldAssistAction;
  fieldType: FieldAssistFieldType;
  title: string;
  placeholder?: string;
  currentValue: string;
  prefill?: string;
}

const SENSITIVE_FIELD_PATTERN =
  /password|token|secret|credential|api[_ -]?key|验证码|密钥|身份证号|银行卡号|高敏个人信息/i;

export function canShowFieldAssist(context: {
  title: string;
  placeholder?: string;
  prefill?: string;
}): boolean {
  return ![context.title, context.placeholder, context.prefill]
    .filter((value): value is string => Boolean(value))
    .some(value => SENSITIVE_FIELD_PATTERN.test(value));
}

export async function runFieldAssist(
  payload: FieldAssistPayload,
  fetchImpl: typeof fetch = fetch,
): Promise<string> {
  const response = await fetchImpl("/api/field-assist", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const result = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(readError(result) ?? "AI 辅助请求失败");
  }
  if (!result || typeof result !== "object" || typeof result.value !== "string") {
    throw new Error("AI 辅助返回格式错误");
  }

  return result.value;
}

function readError(value: unknown): string | undefined {
  return value && typeof value === "object"
    ? readString((value as Record<string, unknown>).error)
    : undefined;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
