import { readFileSync } from "node:fs";
import type { RpcModel } from "./types.js";

export type FieldAssistAction = "regenerate" | "polish";
export type FieldAssistFieldType = "input" | "textarea";

export interface FieldAssistRequest {
  requestId: string;
  action: FieldAssistAction;
  fieldType: FieldAssistFieldType;
  title: string;
  placeholder?: string;
  currentValue: string;
  prefill?: string;
}

export interface FieldAssistResponse {
  value: string;
}

export class FieldAssistError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

type FieldAssistMessage = {
  role: "system" | "user";
  content: string;
};

type RuntimeModel = RpcModel & {
  baseUrl?: string;
};

const SENSITIVE_FIELD_PATTERN =
  /password|token|secret|credential|api[_ -]?key|验证码|密钥|身份证号|银行卡号|高敏个人信息/i;

const PROVIDER_API_KEYS: Record<string, string[]> = {
  openai: ["OPENAI_API_KEY"],
  "openai-codex": ["OPENAI_API_KEY"],
  anthropic: ["ANTHROPIC_API_KEY"],
  xiaomi: ["XIAOMI_API_KEY"],
  "xiaomi-token-plan-cn": ["XIAOMI_TOKEN_PLAN_CN_API_KEY", "XIAOMI_API_KEY"],
  "xiaomi-token-plan-ams": ["XIAOMI_TOKEN_PLAN_AMS_API_KEY", "XIAOMI_API_KEY"],
  "xiaomi-token-plan-sgp": ["XIAOMI_TOKEN_PLAN_SGP_API_KEY", "XIAOMI_API_KEY"],
};

const PROVIDER_BASE_URLS: Record<string, string> = {
  openai: "https://api.openai.com/v1",
  "openai-codex": "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com/v1",
};

export function parseFieldAssistRequest(body: unknown): FieldAssistRequest {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new FieldAssistError(400, "INVALID_REQUEST", "Invalid request body");
  }

  const record = body as Record<string, unknown>;
  const action = record.action;
  const fieldType = record.fieldType;
  if (action !== "regenerate" && action !== "polish") {
    throw new FieldAssistError(400, "INVALID_ACTION", "Invalid AI assist action");
  }
  if (fieldType !== "input" && fieldType !== "textarea") {
    throw new FieldAssistError(400, "INVALID_FIELD_TYPE", "Invalid field type");
  }

  const title = readRequiredString(record.title, "title");
  const currentValue =
    typeof record.currentValue === "string" ? record.currentValue : "";
  if (action === "polish" && !currentValue.trim()) {
    throw new FieldAssistError(
      400,
      "EMPTY_POLISH_INPUT",
      "Please enter content before polishing",
    );
  }

  const request: FieldAssistRequest = {
    requestId: readString(record.requestId) ?? "",
    action,
    fieldType,
    title,
    currentValue,
    ...(readString(record.placeholder)
      ? { placeholder: readString(record.placeholder) }
      : {}),
    ...(readString(record.prefill) ? { prefill: readString(record.prefill) } : {}),
  };

  if (isSensitiveFieldAssistRequest(request)) {
    throw new FieldAssistError(
      403,
      "SENSITIVE_FIELD",
      "AI assist is disabled for sensitive fields",
    );
  }

  return request;
}

export function isSensitiveFieldAssistRequest(
  request: Pick<FieldAssistRequest, "title" | "placeholder" | "prefill">,
): boolean {
  return [request.title, request.placeholder, request.prefill]
    .filter((value): value is string => Boolean(value))
    .some(value => SENSITIVE_FIELD_PATTERN.test(value));
}

export function buildFieldAssistMessages(
  request: FieldAssistRequest,
): FieldAssistMessage[] {
  if (request.action === "polish") {
    return [
      {
        role: "system",
        content:
          "你是文本润色助手。只优化表达，不新增事实，不改变金额、时间、数量、人名、部门、审批事项。只输出润色后的正文。",
      },
      {
        role: "user",
        content: request.currentValue,
      },
    ];
  }

  return [
    {
      role: "system",
      content:
        "你是 ask_user_question 字段生成助手。根据字段标题、placeholder、字段类型和已有内容生成一个可直接填入字段的答案。只输出字段值。",
    },
    {
      role: "user",
      content: JSON.stringify({
        title: request.title,
        placeholder: request.placeholder,
        fieldType: request.fieldType,
        currentValue: request.currentValue,
        prefill: request.prefill,
      }),
    },
  ];
}

export function createFieldAssistHandler(options: {
  getCurrentModel: () => RuntimeModel | undefined;
  env?: Record<string, string | undefined>;
  fetch?: typeof fetch;
}) {
  return async (request: FieldAssistRequest): Promise<FieldAssistResponse> => {
    const model = options.getCurrentModel();
    if (!model?.id || !model.provider) {
      throw new FieldAssistError(
        503,
        "AI_ASSIST_FAILED",
        "No model is available for AI assist",
      );
    }

    const value = await requestModelText({
      request,
      model,
      env: options.env ?? process.env,
      fetchImpl: options.fetch ?? fetch,
    });
    return { value };
  };
}

async function requestModelText(options: {
  request: FieldAssistRequest;
  model: RuntimeModel;
  env: Record<string, string | undefined>;
  fetchImpl: typeof fetch;
}): Promise<string> {
  const apiKey = readProviderApiKey(options.model.provider, options.env);
  const baseUrl = normalizeBaseUrl(
    options.model.baseUrl ?? PROVIDER_BASE_URLS[options.model.provider],
  );
  if (!apiKey || !baseUrl) {
    throw new FieldAssistError(
      503,
      "AI_ASSIST_FAILED",
      "AI assist model credentials are not configured",
    );
  }

  const messages = buildFieldAssistMessages(options.request);
  const api = options.model.api ?? "";
  const response = api.includes("responses")
    ? await postOpenAIResponses(options.fetchImpl, baseUrl, apiKey, options.model.id, messages)
    : await postOpenAIChatCompletions(
        options.fetchImpl,
        baseUrl,
        apiKey,
        options.model.id,
        messages,
      );

  if (!response.trim()) {
    throw new FieldAssistError(
      502,
      "AI_ASSIST_FAILED",
      "AI assist returned empty content",
    );
  }
  return response.trim();
}

async function postOpenAIResponses(
  fetchImpl: typeof fetch,
  baseUrl: string,
  apiKey: string,
  model: string,
  messages: FieldAssistMessage[],
): Promise<string> {
  const response = await fetchImpl(`${baseUrl}/responses`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      input: messages,
      temperature: 0.3,
      max_output_tokens: 1024,
    }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new FieldAssistError(
      502,
      "AI_ASSIST_FAILED",
      readResponseError(body) ?? "AI assist request failed",
    );
  }

  return readResponseText(body);
}

async function postOpenAIChatCompletions(
  fetchImpl: typeof fetch,
  baseUrl: string,
  apiKey: string,
  model: string,
  messages: FieldAssistMessage[],
): Promise<string> {
  const response = await fetchImpl(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages,
      temperature: 0.3,
      max_tokens: 1024,
    }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new FieldAssistError(
      502,
      "AI_ASSIST_FAILED",
      readResponseError(body) ?? "AI assist request failed",
    );
  }

  return readChatText(body);
}

function readProviderApiKey(
  provider: string,
  env: Record<string, string | undefined>,
): string | undefined {
  const names = [
    ...(PROVIDER_API_KEYS[provider] ?? []),
    `${provider.toUpperCase().replace(/[^A-Z0-9]+/g, "_")}_API_KEY`,
  ];

  for (const name of names) {
    const value = readString(env[name]) ?? readSecretFile(env[`${name}_FILE`]);
    if (value) return value;
  }
  return undefined;
}

function readSecretFile(filePath: string | undefined): string | undefined {
  if (!filePath?.trim()) return undefined;
  try {
    return readString(readFileSync(filePath, "utf8"));
  } catch {
    return undefined;
  }
}

function normalizeBaseUrl(value: string | undefined): string | undefined {
  const trimmed = value?.trim().replace(/\/+$/, "");
  return trimmed || undefined;
}

function readResponseText(body: unknown): string {
  if (!body || typeof body !== "object") return "";
  const record = body as Record<string, unknown>;
  const outputText = readString(record.output_text);
  if (outputText) return outputText;

  const output = Array.isArray(record.output) ? record.output : [];
  return output
    .flatMap(item =>
      item && typeof item === "object" && Array.isArray((item as { content?: unknown }).content)
        ? (item as { content: unknown[] }).content
        : [],
    )
    .map(item =>
      item && typeof item === "object"
        ? readString((item as Record<string, unknown>).text)
        : undefined,
    )
    .filter((value): value is string => Boolean(value))
    .join("");
}

function readChatText(body: unknown): string {
  if (!body || typeof body !== "object") return "";
  const choices = (body as { choices?: unknown }).choices;
  if (!Array.isArray(choices)) return "";
  const first = choices[0];
  if (!first || typeof first !== "object") return "";
  const message = (first as { message?: unknown }).message;
  if (!message || typeof message !== "object") return "";
  return readString((message as Record<string, unknown>).content) ?? "";
}

function readResponseError(body: unknown): string | undefined {
  if (!body || typeof body !== "object") return undefined;
  const error = (body as { error?: unknown }).error;
  if (!error || typeof error !== "object") return undefined;
  return readString((error as Record<string, unknown>).message);
}

function readRequiredString(value: unknown, name: string): string {
  const text = readString(value);
  if (!text) {
    throw new FieldAssistError(400, "INVALID_REQUEST", `${name} is required`);
  }
  return text;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
