import type {
  FieldAssistCommandPayload,
  FieldAssistResult,
  FieldAssistWarning,
  RpcResponse,
} from "@dano/types/protocol";

const WARNING_PATTERN =
  /password|passwd|pwd|token|secret|credential|api[ _-]?key|apikey|private key|ssh key|cookie|session|authorization|bearer|验证码|密码|令牌|密钥|秘钥|身份证|银行卡|手机号|邮箱验证码|短信验证码/i;

export function getFieldAssistWarning(context: {
  title: string;
  placeholder?: string;
  prefill?: string;
}): string {
  return readFieldAssistWarnings(context)[0]?.message ?? "";
}

export function readFieldAssistWarnings(context: {
  title: string;
  placeholder?: string;
  prefill?: string;
}): FieldAssistWarning[] {
  const hit = [context.title, context.placeholder, context.prefill]
    .filter((value): value is string => Boolean(value))
    .some(value => WARNING_PATTERN.test(value));
  return hit
    ? [
        {
          code: "SENSITIVE_FIELD",
          message: "该字段可能包含敏感信息，请确认内容适合发送给 AI 辅助。",
        },
      ]
    : [];
}

export function toFieldAssistErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return "AI 辅助请求失败";
}

export function nextFieldAssistRunId(
  runs: Record<string, number>,
  fieldId: string,
): number {
  return (runs[fieldId] ?? 0) + 1;
}

export function isCurrentFieldAssistRun(
  runs: Record<string, number>,
  fieldId: string,
  runId: number,
): boolean {
  return runs[fieldId] === runId;
}

export function invalidateFieldAssistRuns(
  runs: Record<string, number>,
): Record<string, number> {
  return Object.fromEntries(
    Object.entries(runs).map(([fieldId, runId]) => [fieldId, runId + 1]),
  );
}

export function readFieldAssistResponse(response: RpcResponse): FieldAssistResult {
  if (!response.success) {
    throw new Error(response.error || "AI 辅助请求失败");
  }
  if (
    !response.data ||
    typeof response.data !== "object" ||
    typeof (response.data as Partial<FieldAssistResult>).value !== "string"
  ) {
    throw new Error("AI 辅助返回格式错误");
  }
  return response.data as FieldAssistResult;
}

export type { FieldAssistCommandPayload, FieldAssistResult };
