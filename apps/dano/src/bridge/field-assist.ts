import {
  createAgentSession,
  createExtensionRuntime,
  SessionManager,
  SettingsManager,
  type AgentSession,
  type ResourceLoader,
} from "@earendil-works/pi-coding-agent";
import type {
  FieldAssistCommandPayload,
  FieldAssistFieldType,
  FieldAssistMetadata,
  FieldAssistResult,
  FieldAssistWarning,
  RpcModel,
} from "./types.js";

export type FieldAssistErrorCode =
  | "EMPTY_POLISH_INPUT"
  | "FIELD_ASSIST_DISABLED"
  | "FIELD_ASSIST_NOT_ALLOWED"
  | "REQUEST_TOO_LARGE"
  | "MODEL_UNAVAILABLE"
  | "MODEL_TIMEOUT"
  | "MODEL_ABORTED"
  | "INVALID_MODEL_OUTPUT"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR";

export class FieldAssistError extends Error {
  constructor(
    readonly code: FieldAssistErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
  }
}

type FieldAssistMessage = {
  role: "system" | "user";
  content: string;
};

const LIMITS = {
  inputMaxChars: 2_000,
  textareaMaxChars: 12_000,
  inputOutputMaxChars: 240,
  textareaOutputMaxChars: 3_000,
};

const WARNING_PATTERN =
  /password|passwd|pwd|token|secret|credential|api[ _-]?key|apikey|private key|ssh key|cookie|session|authorization|bearer|验证码|密码|令牌|密钥|秘钥|身份证|银行卡|手机号|邮箱验证码|短信验证码/i;

const SECRET_VALUE_PATTERN =
  /\b(?:sk-[A-Za-z0-9_-]{16,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,})\b|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:token|secret|api[ _-]?key|authorization|bearer)\s*[:=]\s*\S{8,}/i;

const FOLLOW_UP_QUESTION_PATTERN =
  /ask_user_question|(?:请问|请您|麻烦您|需要您)|(?:请|麻烦|需要)(?:补充|提供|告知|填写|输入|说明|确认)|(?:还需要|需要更多|缺少).*(?:信息|内容)/i;

export interface FieldAssistClient {
  generateText(request: {
    model?: RpcModel;
    messages: FieldAssistMessage[];
    timeoutMs?: number;
  }): Promise<string>;
}

export interface FieldAssistService {
  assist(
    input: FieldAssistCommandPayload,
    options?: { clientId?: string },
  ): Promise<FieldAssistResult>;
}

export function createFieldAssistService(options: {
  ai: FieldAssistClient;
  getCurrentModel: () => RpcModel | undefined;
  timeoutMs?: number;
}): FieldAssistService {
  return {
    async assist(input) {
      const startedAt = Date.now();
      assertAllowed(input);

      const model = options.getCurrentModel();
      const raw = await options.ai.generateText({
        model,
        messages:
          input.action === "polish"
            ? buildPolishMessages(input)
            : buildRegenerateMessages(input),
        timeoutMs: options.timeoutMs ?? 60_000,
      });
      const value = normalizeFieldAssistOutput(raw, input.fieldType);
      assertFieldAssistOutput(value);
      const metadata: FieldAssistMetadata = {
        action: input.action,
        fieldType: input.fieldType,
        inputLength: input.currentValue.length,
        outputLength: value.length,
        elapsedMs: Date.now() - startedAt,
        model,
      };
      const warnings = getFieldAssistWarnings(input);
      if (warnings.length) metadata.warnings = warnings;
      return { value, metadata };
    },
  };
}

export function createPiSdkFieldAssistClient(options: {
  cwd: string;
  session: AgentSession;
}): FieldAssistClient {
  return {
    async generateText(request) {
      const model = request.model;
      if (!model) {
        throw new FieldAssistError("MODEL_UNAVAILABLE", "No model is available");
      }

      let session: AgentSession | undefined;
      let timeout: ReturnType<typeof setTimeout> | undefined;
      try {
        const systemPrompt = request.messages
          .filter(message => message.role === "system")
          .map(message => message.content)
          .join("\n\n");
        const userPrompt = request.messages
          .filter(message => message.role === "user")
          .map(message => message.content)
          .join("\n\n");

        const created = await createAgentSession({
          cwd: options.cwd,
          model: model as Parameters<typeof createAgentSession>[0] extends {
            model?: infer M;
          }
            ? M
            : never,
          thinkingLevel: "off",
          modelRegistry: options.session.modelRegistry,
          settingsManager: SettingsManager.inMemory({
            compaction: { enabled: false },
            retry: { enabled: false },
          }),
          resourceLoader: createLockedResourceLoader(systemPrompt),
          noTools: "all",
          sessionManager: SessionManager.inMemory(options.cwd),
        });
        session = created.session;

        let text = "";
        const unsubscribe = session.subscribe(event => {
          if (
            event.type === "message_update" &&
            event.assistantMessageEvent.type === "text_delta"
          ) {
            text += event.assistantMessageEvent.delta;
          }
        });

        const timeoutMs = request.timeoutMs ?? 60_000;
        const timedPrompt = new Promise<never>((_, reject) => {
          timeout = setTimeout(() => {
            void session?.abort();
            reject(new FieldAssistError("MODEL_TIMEOUT", "AI assist timed out"));
          }, timeoutMs);
        });

        try {
          await Promise.race([session.prompt(userPrompt, { expandPromptTemplates: false }), timedPrompt]);
        } finally {
          unsubscribe();
        }

        if (!text.trim()) {
          throw new FieldAssistError(
            "INVALID_MODEL_OUTPUT",
            "AI assist returned empty content",
          );
        }
        return text.trim();
      } finally {
        if (timeout) clearTimeout(timeout);
        session?.dispose();
      }
    },
  };
}

export function buildPolishMessages(
  input: FieldAssistCommandPayload,
): FieldAssistMessage[] {
  return [
    {
      role: "system",
      content: [
        "你是文本润色助手。",
        "只优化表达，不新增事实。",
        "不改变金额、时间、数量、人名、部门、审批事项、编号、专有名词。",
        "如果信息不足，保留原意做最小润色，不要追问用户，不要请求补充信息。",
        "保持原文语种。",
        "只输出润色后的正文。",
        "不要解释，不要加标题，不要用 Markdown 包裹。",
      ].join("\n"),
    },
    { role: "user", content: input.currentValue },
  ];
}

export function buildRegenerateMessages(
  input: FieldAssistCommandPayload,
): FieldAssistMessage[] {
  return [
    {
      role: "system",
      content: [
        "你是 ask_user_question 字段生成助手。",
        "根据字段标题、placeholder、字段类型和已有内容生成一个可直接填入字段的答案。",
        "只输出字段值。",
        "不要解释，不要加标题，不要用 Markdown 包裹。",
        input.fieldType === "input"
          ? "输出应简短，适合单行输入框。"
          : "输出可以是自然段，适合多行文本框。",
      ].join("\n"),
    },
    {
      role: "user",
      content: JSON.stringify({
        title: input.title,
        placeholder: input.placeholder,
        fieldType: input.fieldType,
        currentValue: input.currentValue,
        prefill: input.prefill,
      }),
    },
  ];
}

export function assertAllowed(input: FieldAssistCommandPayload): void {
  if (input.action === "polish" && !input.currentValue.trim()) {
    throw new FieldAssistError(
      "EMPTY_POLISH_INPUT",
      "Please enter content before polishing",
    );
  }
  const max =
    input.fieldType === "input" ? LIMITS.inputMaxChars : LIMITS.textareaMaxChars;
  if (input.currentValue.length > max) {
    throw new FieldAssistError("REQUEST_TOO_LARGE", "Field content is too long");
  }
  if (SECRET_VALUE_PATTERN.test(input.currentValue)) {
    throw new FieldAssistError(
      "FIELD_ASSIST_NOT_ALLOWED",
      "Field contains obvious secret credentials",
    );
  }
}

export function getFieldAssistWarnings(input: {
  title: string;
  placeholder?: string;
  prefill?: string;
}): FieldAssistWarning[] {
  const hit = [input.title, input.placeholder, input.prefill]
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

export function normalizeFieldAssistOutput(
  value: string,
  fieldType: FieldAssistFieldType,
): string {
  const normalized =
    fieldType === "input"
      ? value.replace(/\s+/g, " ").trim()
      : value
          .replace(/\r\n/g, "\n")
          .split("\n")
          .map(line => line.trimEnd())
          .join("\n")
          .trim();
  const max =
    fieldType === "input"
      ? LIMITS.inputOutputMaxChars
      : LIMITS.textareaOutputMaxChars;
  return normalized.length > max ? normalized.slice(0, max).trimEnd() : normalized;
}

export function assertFieldAssistOutput(value: string): void {
  if (FOLLOW_UP_QUESTION_PATTERN.test(value)) {
    throw new FieldAssistError(
      "INVALID_MODEL_OUTPUT",
      "AI 辅助返回了追问内容，请重试",
    );
  }
}

function createLockedResourceLoader(systemPrompt: string): ResourceLoader {
  return {
    getExtensions: () => ({
      extensions: [],
      errors: [],
      runtime: createExtensionRuntime(),
    }),
    getSkills: () => ({ skills: [], diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => systemPrompt,
    getAppendSystemPrompt: () => [],
    extendResources: () => {},
    reload: async () => {},
  };
}
