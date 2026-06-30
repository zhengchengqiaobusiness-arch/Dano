# feat: ask_user_question 的 input / textarea 支持生产化 AI 辅助

## 背景

当前 Dano 已不是早期 demo 形态。实现 `ask_user_question` 输入框的“重新生成 / AI 润色”时，需要按当前 Dano upstream 代码和 Pi 官方 SDK 文档重新设计，而不是沿用旧版 `packages/bridge` / WebSocket RPC / scratch detached session 的临时方案。

本 issue 替代上一版 `dano_field_assist_actual_code_issue.md`。

当前实现口径：

- Dano 是 `apps/dano` 单应用，不拆独立前端服务 / 后端服务。
- 浏览器端通过现有 HTTP POST command + EventSource/SSE streaming 通道通信。
- LLM provider / API key / Pi runtime 凭据只在服务端使用，不下发浏览器。
- AI 辅助应复用 Pi SDK / Pi runtime 的模型配置，但不得污染当前 Agent session、transcript、session list、extension UI 请求流。
- 不能把这个能力当成“一次普通对话消息”或“一个普通 detached session”。

## 参考依据

### Dano 当前代码 / 文档

- `README.md`
  - Dano 是由 server-side Pi runtime 支撑的浏览器 LLM chat app。
  - 浏览器使用 HTTP POST 发送命令，使用 EventSource/SSE 接收流式响应。
  - 浏览器不接收 LLM credentials；API key 只由服务器从环境变量 / `.env` / Docker secret 文件读取。
  - 当前 API 包括：
    - `POST /api/clients/<clientId>/messages`
    - `GET /api/clients/<clientId>/events`
    - `GET /api/clients`
    - `GET /api/health`
- `apps/dano/types/protocol.ts`
  - 定义 `RpcCommandMap` / `RpcResponseMap`。
  - 已有 `answer_question` 命令用于回答 `ask_user_question`。
  - 定义 `RpcExtensionUIRequest` / `RpcExtensionUIResponse`。
  - 定义 browser/server wire protocol：browser 通过 `{ type: "command", payload: RpcCommand }` 发送命令，server 通过 SSE 发送 response/event/extension UI request。
- `apps/dano/web/src/components/QuestionToolCard.svelte`
  - 当前负责渲染 transcript 中原生 `ask_user_question` 问题卡。
  - 文本回答字段当前由 `AskUserQuestionItem.kind === "text"` 渲染。
  - 当前通过 `answer_question` command 提交用户最终回答。
- `apps/dano/web/src/composables/bridgeStore.svelte.ts`
  - 当前浏览器侧 command / SSE 状态管理入口。
- `apps/dano/src/bridge/bridge-rpc-adapter.ts`
  - 当前服务端 command 分发入口。
  - 当前 `answer_question` 在这里路由到 `askUserQuestionCoordinator.answer(...)`。

### Pi 官方 SDK 文档

Pi SDK 文档中的关键点：

- SDK 提供 `createAgentSession`、`SessionManager`、`AuthStorage`、`ModelRegistry` 等能力。
- `AgentSession` 负责生命周期、消息历史、模型状态、compaction 和事件流。
- `SessionManager.inMemory()` 是 Pi SDK 文档中的原生 in-memory session manager，用于不持久化 session 数据的一次性 session。
- 默认会话持久化会写入 Pi sessions；临时文本辅助不应写入普通 session 文件。
- SDK 默认内置工具包含 `read`、`bash`、`edit`、`write`；本功能必须显式禁用工具。
- `createAgentSession()` 如果不传 `ResourceLoader`，会使用默认 resource discovery；本功能必须避免加载项目 / 全局扩展和工具，防止副作用。
- Pi extension 可注册 tools/events/UI/commands，且可运行任意代码；本功能不应触发 extension side effects。

### 领域语言 / ADR

- 领域术语使用 `Field Assist`，定义见 `CONTEXT.md`。
- 架构决策见 `docs/adr/0004-field-assist-uses-transient-pi-session.md`。

## 产品目标

为 `ask_user_question` 的文本输入场景增加两个按钮：

```text
[重新生成] [AI 润色]
```

覆盖范围：

- 原生 `ask_user_question` 问题卡里的单行文本字段：`inputType` 缺省或 `inputType === "text"`。
- 原生 `ask_user_question` 问题卡里的多行文本字段：`inputType === "textarea"`。

行为要求：

- “重新生成”请求成功后直接替换当前输入框内容。
- “AI 润色”请求成功后直接替换当前输入框内容。
- 不展示预览。
- 不展示候选卡片。
- 不需要二次确认。
- 不自动提交用户回答。
- 请求失败时保留原内容。
- AI 润色时，当前输入框文本必须作为模型的 `user` prompt 本身。
- 重新生成允许基于字段标题、placeholder、字段类型、当前值和默认值生成业务内容；空内容也允许生成。

## 非目标

本 issue 不做以下内容：

- 不新增独立 `/api/field-assist` endpoint。
- 不新增 WebSocket RPC。
- 不拆前后端服务。
- 不引入 CORS。
- 不把 LLM provider credentials 下发到浏览器。
- 不调用当前 live session 的 `prompt()`。
- 不调用 `answer_question`。
- 不通过用户正式消息流发送“请润色”。
- 不创建普通 detached session。
- 不写入当前 transcript。
- 不写入普通 session list。
- 不触发 tools。
- 不触发 browser / bash / file edit 等 extension side effect。
- 不自动提交 ask_user_question 的最终回答。
- 不改 `ExtensionDialog.svelte`；该组件属于 Pi extension UI 兼容弹窗，不是本 issue 的目标 UI。

## 总体方案

新增一个 production-ready 的 `field_assist` command，走现有 Dano command 通道：

```text
QuestionToolCard.svelte
  ↓
bridgeStore.fieldAssist(...)
  ↓
POST /api/clients/<clientId>/messages
  ↓
{ type: "command", payload: { type: "field_assist", ... } }
  ↓
BridgeRpcAdapter.dispatchCommand("field_assist")
  ↓
FieldAssistService
  ↓
TransientAIClient
  ↓
PiSdkTransientAIClient
  ↓
Pi SDK in-memory no-tools one-shot session
```

核心原则：

```text
复用 Pi SDK / Pi runtime 的模型配置
但不复用当前 Agent session 的 messages / transcript / tools / extension UI / session file
```

## 协议设计

改动文件：

```text
apps/dano/types/protocol.ts
```

新增类型：

```ts
export type FieldAssistAction = "regenerate" | "polish";
export type FieldAssistFieldType = "input" | "textarea";

export interface FieldAssistCommandPayload {
  requestId: string;
  action: FieldAssistAction;
  fieldType: FieldAssistFieldType;
  requestMethod: "input" | "editor"; // textarea 复用 editor 语义，仅作为字段来源元数据
  title: string;
  placeholder?: string;
  currentValue: string;
  prefill?: string;
}

export type FieldAssistWarningCode = "SENSITIVE_FIELD";

export interface FieldAssistWarning {
  code: FieldAssistWarningCode;
  message: string;
}

export interface FieldAssistMetadata {
  action: FieldAssistAction;
  fieldType: FieldAssistFieldType;
  inputLength: number;
  outputLength: number;
  elapsedMs: number;
  model?: RpcModel;
  degraded?: boolean;
  warnings?: FieldAssistWarning[];
}

export interface FieldAssistResult {
  value: string;
  metadata: FieldAssistMetadata;
}
```

扩展 `RpcCommandMap`：

```ts
export interface RpcCommandMap {
  // existing commands...
  field_assist: FieldAssistCommandPayload;
}
```

扩展 `RpcResponseMap`：

```ts
export interface RpcResponseMap {
  // existing responses...
  field_assist: FieldAssistResult;
}
```

浏览器侧仍然使用现有 wire protocol：

```ts
const message = {
  type: "command",
  payload: {
    id: crypto.randomUUID(),
    type: "field_assist",
    requestId,
    action,
    fieldType,
    requestMethod,
    title,
    placeholder,
    currentValue,
    prefill,
  },
};
```

发送路径仍然是：

```text
POST /api/clients/<clientId>/messages
```

## 后端模块设计

建议新增模块：

```text
apps/dano/src/bridge/field-assist/
  ├── field-assist-types.ts
  ├── field-assist-service.ts
  ├── transient-ai-client.ts
  ├── pi-sdk-transient-ai-client.ts
  ├── prompts.ts
  ├── policy.ts
  ├── normalize.ts
  ├── rate-limit.ts
  └── audit.ts
```

### 1. FieldAssistService

```ts
export class FieldAssistService {
  constructor(private readonly deps: {
    ai: TransientAIClient;
    policy: FieldAssistPolicy;
    audit: FieldAssistAuditSink;
    rateLimit: FieldAssistRateLimiter;
    getCurrentModel: () => RpcModel | undefined;
  }) {}

  async assist(
    input: FieldAssistCommandPayload,
    options: { clientId: string; signal?: AbortSignal },
  ): Promise<FieldAssistResult> {
    const startedAt = Date.now();

    await this.deps.rateLimit.check({
      clientId: options.clientId,
      requestId: input.requestId,
      action: input.action,
    });

    this.deps.policy.assertAllowed(input);

    const messages =
      input.action === "polish"
        ? buildPolishMessages(input)
        : buildRegenerateMessages(input);

    const model = this.deps.getCurrentModel();

    const raw = await this.deps.ai.generateText({
      model,
      messages,
      maxTokens: input.fieldType === "input" ? 160 : 1200,
      temperature: input.action === "polish" ? 0.2 : 0.5,
      timeoutMs: 60_000,
      signal: options.signal,
    });

    const value = normalizeFieldAssistOutput(raw, input.fieldType);

    const result: FieldAssistResult = {
      value,
      metadata: {
        action: input.action,
        fieldType: input.fieldType,
        inputLength: input.currentValue.length,
        outputLength: value.length,
        elapsedMs: Date.now() - startedAt,
        model,
      },
    };

    this.deps.audit.recordSuccess(input, result, options);

    return result;
  }
}
```

### 2. TransientAIClient

```ts
export type TransientAIMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

export type TransientAIRequest = {
  model?: RpcModel;
  messages: TransientAIMessage[];
  temperature?: number;
  maxTokens?: number;
  timeoutMs?: number;
  signal?: AbortSignal;
};

export interface TransientAIClient {
  generateText(request: TransientAIRequest): Promise<string>;
}
```

## Pi SDK transient 实现

新增：

```text
apps/dano/src/bridge/field-assist/pi-sdk-transient-ai-client.ts
```

生产要求：

1. 使用 Pi SDK 创建一次性 in-memory session。
2. 使用 `SessionManager.inMemory()`。
3. 显式禁用所有 tools。
4. 使用受限 / locked resource loader，避免加载项目或全局 extensions。
5. 使用当前 Dano 选择的 model；如果当前无 model，则使用 runtime default model。
6. 设置较低 thinking / off thinking，降低延迟和成本。
7. 设置 timeout / abort。
8. `finally` 中 `dispose()`。
9. 不将临时消息写入当前 Dano session。
10. 不把临时 session 暴露给 session list。

伪代码骨架：

```ts
export class PiSdkTransientAIClient implements TransientAIClient {
  constructor(private readonly deps: {
    authStorage: AuthStorage;
    modelRegistry: ModelRegistry;
    getDefaultModel: () => RpcModel | undefined;
    createLockedResourceLoader: (systemPrompt: string) => unknown;
  }) {}

  async generateText(request: TransientAIRequest): Promise<string> {
    const model = request.model ?? this.deps.getDefaultModel();
    if (!model) {
      throw new FieldAssistError("MODEL_UNAVAILABLE");
    }

    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(new Error("MODEL_TIMEOUT")),
      request.timeoutMs ?? 60_000,
    );

    const externalAbort = () => controller.abort(request.signal?.reason);
    request.signal?.addEventListener("abort", externalAbort, { once: true });

    let session: AgentSession | undefined;

    try {
      const systemPrompt = request.messages
        .filter(message => message.role === "system")
        .map(message => message.content)
        .join("\n\n");

      const userPrompt = request.messages
        .filter(message => message.role === "user")
        .map(message => message.content)
        .join("\n\n");

      session = await createAgentSession({
        sessionManager: SessionManager.inMemory(),
        authStorage: this.deps.authStorage,
        modelRegistry: this.deps.modelRegistry,
        model,
        thinkingLevel: "off",
        noTools: "all",
        resourceLoader: this.deps.createLockedResourceLoader(systemPrompt),
      } satisfies CreateAgentSessionOptions); // API names must be verified from local typings.

      let text = "";
      const unsubscribe = subscribeToTextDeltas(session, chunk => {
        text += chunk;
      }); // API/event names must be verified from local typings.

      try {
        await promptOneShot(session, userPrompt, controller.signal);
        // prompt argument shape must be verified from local typings.
      } finally {
        unsubscribe?.();
      }

      if (!text.trim()) {
        throw new FieldAssistError("INVALID_MODEL_OUTPUT");
      }

      return text.trim();
    } catch (error) {
      if (controller.signal.aborted) {
        throw new FieldAssistError("MODEL_TIMEOUT", { cause: error });
      }
      throw error;
    } finally {
      clearTimeout(timeout);
      request.signal?.removeEventListener("abort", externalAbort);
      await session?.dispose?.();
    }
  }
}
```

> 上面的代码只表达控制流和约束，不是可复制实现。`CreateAgentSessionOptions`、tool 禁用选项、`ResourceLoader` 构造方式、事件订阅 API、文本 delta 事件名、`prompt` 参数形状都必须以当前安装的 Pi SDK typings 为准；实现时先写/更新 mock-spy 测试锁定这些 API，再写生产代码。

### 禁止使用的路径

不要这样实现：

```ts
// 禁止：污染当前会话
liveSession.prompt("请润色...");

// 禁止：走正式 ask_user_question 回答路径
askUserQuestionCoordinator.answer(...);

// 禁止：伪装成用户正式消息
sendUserMessage(...);

// 禁止：普通 detached session，容易进入 session 管理 / 默认工具 / extension 流
detachedSessionRegistry.create(...);

// 禁止：默认工具打开
createAgentSession({ ... }); // 未设置 noTools: "all"

// 禁止：默认 ResourceLoader 自动加载项目扩展
createAgentSession({ resourceLoader: undefined });
```

如果当前 Dano 的 `createDetachedAgentSession()` helper 默认会创建可见 session、默认加载 tools、默认加载 extensions 或写 session 文件，则不得复用它实现 field assist。

## Prompt 规则

### AI 润色

AI 润色时，当前输入框文本必须作为模型的 `user` prompt 本身。

```ts
export function buildPolishMessages(
  input: FieldAssistCommandPayload,
): TransientAIMessage[] {
  return [
    {
      role: "system",
      content: [
        "你是文本润色助手。",
        "只优化表达，不新增事实。",
        "不改变金额、时间、数量、人名、部门、审批事项、编号、专有名词。",
        "保持原文语种。",
        "只输出润色后的正文。",
        "不要解释，不要加标题，不要用 Markdown 包裹。",
      ].join("\n"),
    },
    {
      role: "user",
      content: input.currentValue,
    },
  ];
}
```

不要这样：

```ts
// 禁止：user prompt 被包装
content: `请润色以下内容：${input.currentValue}`;

// 禁止：user prompt 变成 JSON
content: JSON.stringify({ currentValue: input.currentValue });
```

### 重新生成

重新生成可以使用结构化上下文：

```ts
export function buildRegenerateMessages(
  input: FieldAssistCommandPayload,
): TransientAIMessage[] {
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
```

重新生成允许在 `currentValue` 为空时仅基于字段标题、placeholder、字段类型和 prefill 生成可直接填入的业务内容。

## 安全策略

### 1. 敏感字段提示

默认不因敏感字段直接禁用 AI 辅助；表单验证时显示警告，提示该字段可能包含敏感信息。密钥、验证码、token 等明显秘密值仍应由服务端策略拒绝，避免把 secrets 送入模型。

敏感字段提示是协议返回和前端表单状态，不是失败态：

- 前端在发起请求前用同一套关键词规则计算 warning，并在表单内显示。
- 服务端再次计算 warning，并在成功响应的 `metadata.warnings` 返回。
- warning 不进入 audit 原文日志，只允许记录 warning code。
- 只有明显凭据值命中时才返回 `FIELD_ASSIST_NOT_ALLOWED`。

匹配来源：

- `title`
- `placeholder`
- `prefill`
- 必要时检查 `currentValue` 是否包含明显密钥 / token / 验证码模式

警告关键词包括但不限于：

```text
password
passwd
pwd
token
secret
credential
api key
apikey
private key
ssh key
cookie
session
authorization
bearer
验证码
密码
令牌
密钥
秘钥
API Key
身份证
银行卡
手机号
邮箱验证码
短信验证码
```

仅当命中明显秘密值模式时返回错误码：

```text
FIELD_ASSIST_NOT_ALLOWED
```

### 2. 输入长度限制

建议默认：

```ts
const LIMITS = {
  inputMaxChars: 2_000,
  textareaMaxChars: 12_000,
  inputOutputMaxChars: 240,
  textareaOutputMaxChars: 3_000,
};
```

超限错误码：

```text
REQUEST_TOO_LARGE
```

### 3. 并发 / 频控

建议默认：

```text
同一个 clientId：最多 10 次 / 分钟
同一个 requestId：最多 1 个 in-flight field_assist
服务端全局：最多 4 个并发 field_assist
```

错误码：

```text
RATE_LIMITED
```

### 4. 日志与审计

不得记录：

- `currentValue`
- 生成后的 `value`
- 完整 prompt
- 完整 model response

只记录结构化元数据：

```ts
export interface FieldAssistAuditEvent {
  event: "field_assist";
  requestIdHash: string;
  clientIdHash: string;
  action: FieldAssistAction;
  fieldType: FieldAssistFieldType;
  warningCodes?: FieldAssistWarningCode[];
  inputLength: number;
  outputLength?: number;
  modelProvider?: string;
  modelId?: string;
  success: boolean;
  elapsedMs: number;
  errorCode?: FieldAssistErrorCode;
}
```

## 错误码

```ts
export type FieldAssistErrorCode =
  | "EMPTY_POLISH_INPUT"
  | "FIELD_ASSIST_DISABLED"
  | "FIELD_ASSIST_NOT_ALLOWED"
  | "REQUEST_TOO_LARGE"
  | "MODEL_UNAVAILABLE"
  | "MODEL_TIMEOUT"
  | "MODEL_ABORTED"
  | "MODEL_REFUSED"
  | "INVALID_MODEL_OUTPUT"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR";
```

前端展示建议：

```ts
const FIELD_ASSIST_ERROR_MESSAGES: Record<FieldAssistErrorCode, string> = {
  EMPTY_POLISH_INPUT: "请先输入需要润色的内容",
  FIELD_ASSIST_DISABLED: "当前环境未启用 AI 辅助",
  FIELD_ASSIST_NOT_ALLOWED: "该字段包含明显敏感凭据，已禁用 AI 辅助",
  REQUEST_TOO_LARGE: "当前内容过长，无法进行 AI 辅助",
  MODEL_UNAVAILABLE: "当前没有可用模型",
  MODEL_TIMEOUT: "AI 辅助超时，请稍后重试",
  MODEL_ABORTED: "AI 辅助已取消",
  MODEL_REFUSED: "模型拒绝处理该内容",
  INVALID_MODEL_OUTPUT: "AI 辅助返回内容为空或格式错误",
  RATE_LIMITED: "AI 辅助请求过于频繁，请稍后再试",
  INTERNAL_ERROR: "AI 辅助请求失败",
};
```

## 前端实现

改动文件：

```text
apps/dano/web/src/components/QuestionToolCard.svelte
apps/dano/web/src/components/ChatTranscript.svelte
apps/dano/web/src/layout/AppMainContent.svelte
apps/dano/web/src/composables/bridgeStore.svelte.ts
apps/dano/web/src/utils/fieldAssist.ts
apps/dano/web/src/utils/askUserQuestion.ts
```

`apps/dano/web/src/utils/fieldAssist.ts` 只放前端纯函数：

- `getFieldAssistWarning(...)`
- `toFieldAssistErrorMessage(...)`
- warning / error 文案映射

后端敏感字段判断放在 `apps/dano/src/bridge/field-assist/policy.ts`。前后端用相同 fixture 覆盖关键词口径，避免 UI 提示和服务端响应漂移。

### QuestionToolCard.svelte

`QuestionToolCard` 接收 `onFieldAssist`，只对 `AskUserQuestionItem.kind === "text"` 显示按钮。

字段类型映射：

```ts
const fieldType =
  item.inputType === "textarea" ? "textarea" : "input";
```

按钮规则：

```text
重新生成：
- text / textarea 均显示
- 空内容也可点击
- AI 请求中禁用

AI 润色：
- text / textarea 均显示
- 当前内容 trim 后为空时禁用
- AI 请求中禁用

提交按钮：
- AI 请求中建议禁用，避免提交旧值或中间态
```

成功行为：

- 只替换当前问题卡字段的 `textAnswer[item.id]`。
- 不调用 `answer_question`。
- 不自动提交整个问题卡。
- 如果问题卡切换或新请求先完成，旧响应不得覆盖新字段。

失败行为：

- 保留原 `textAnswer[item.id]`。
- 在当前字段下显示错误。

`inputType: "textarea"`：

- `ask_user_question` protocol 和 parser 接受 `textarea`。
- `QuestionToolCard` 渲染 `<textarea>`。
- `field_assist` payload 使用 `fieldType: "textarea"`。

`ExtensionDialog.svelte`：

- 不接入本功能。
- 不传 `onFieldAssist`。
- 不展示 Field Assist 按钮。

### bridgeStore.svelte.ts

新增 typed helper：

```ts
async function fieldAssist(
  payload: FieldAssistCommandPayload,
): Promise<FieldAssistResult> {
  return sendCommand("field_assist", payload, {
    timeoutMs: 65_000,
  });
}
```

该 helper 必须复用现有 command 发送通道，不新增浏览器直连 provider 调用。

## 服务端接入

改动文件：

```text
apps/dano/src/bridge/bridge-rpc-adapter.ts
```

在 `dispatchCommand` 中新增：

```ts
case "field_assist": {
  const result = await this.context.fieldAssist.assist(command, {
    clientId: source.clientId,
    signal: source.signal,
  });

  return {
    id: command.id,
    type: "response",
    command: "field_assist",
    success: true,
    data: result,
  };
}
```

错误响应沿用现有 RPC error response 结构，`error.code` 使用 `FieldAssistErrorCode`。

## 配置

新增可选配置：

```ts
export interface FieldAssistConfig {
  enabled: boolean;
  timeoutMs: number;
  rateLimitPerClientPerMinute: number;
  maxConcurrentGlobal: number;
  inputMaxChars: number;
  textareaMaxChars: number;
  inputOutputMaxChars: number;
  textareaOutputMaxChars: number;
  thinkingLevel: "off" | "low";
}
```

默认：

```ts
export const DEFAULT_FIELD_ASSIST_CONFIG: FieldAssistConfig = {
  enabled: true,
  timeoutMs: 60_000,
  rateLimitPerClientPerMinute: 10,
  maxConcurrentGlobal: 4,
  inputMaxChars: 2_000,
  textareaMaxChars: 12_000,
  inputOutputMaxChars: 240,
  textareaOutputMaxChars: 3_000,
  thinkingLevel: "off",
};
```

环境变量可覆盖：

```text
DANO_FIELD_ASSIST_ENABLED=true
DANO_FIELD_ASSIST_TIMEOUT_MS=60000
DANO_FIELD_ASSIST_RATE_LIMIT_PER_CLIENT_PER_MINUTE=10
DANO_FIELD_ASSIST_MAX_CONCURRENT_GLOBAL=4
```

## 测试计划

### 1. 协议测试

- [ ] `RpcCommandMap` 包含 `field_assist`。
- [ ] `RpcResponseMap` 包含 `field_assist`。
- [ ] `ClientMessage` 能承载 `field_assist` command。
- [ ] `ServerMessage` 能返回 `field_assist` response。
- [ ] TypeScript 编译通过。

### 2. 后端单元测试

- [ ] `polish` 空内容返回 `EMPTY_POLISH_INPUT`。
- [ ] 敏感 title / placeholder 不阻止请求，成功响应包含 `metadata.warnings: [{ code: "SENSITIVE_FIELD", ... }]`。
- [ ] 明显密钥 / token / 验证码值返回 `FIELD_ASSIST_NOT_ALLOWED`。
- [ ] 超长输入返回 `REQUEST_TOO_LARGE`。
- [ ] `polish` prompt 中 `user.content === currentValue`，不能带前缀、JSON 包装或额外说明。
- [ ] `regenerate` 使用结构化上下文。
- [ ] `input` 输出被 normalize 为单行并限制长度。
- [ ] `textarea` 输出保留自然段并限制长度。
- [ ] model timeout 返回 `MODEL_TIMEOUT`。
- [ ] rate limit 返回 `RATE_LIMITED`。
- [ ] audit event 不包含原文和模型输出。
- [ ] audit event 可包含 warning code，但不包含 warning 命中的原文。

### 3. Pi transient 测试

使用 mock Pi SDK / spy：

- [ ] 实现前先用当前安装的 `@earendil-works/pi-coding-agent` typings 锁定 `createAgentSession`、`SessionManager.inMemory`、tool 禁用、ResourceLoader 和事件订阅的真实 API 名称。
- [ ] 使用 `SessionManager.inMemory()`。
- [ ] 传入 `noTools: "all"`。
- [ ] 传入 locked / minimal resource loader。
- [ ] 不调用当前 live session 的 `prompt()`。
- [ ] 不调用 `askUserQuestionCoordinator.answer()`。
- [ ] 不调用 detached session registry。
- [ ] 不发送 transcript event。
- [ ] 完成后调用 `dispose()`。
- [ ] abort / timeout 后调用 `dispose()`。

### 4. 前端组件测试

- [ ] `input` 类型展示“重新生成”和“AI 润色”。
- [ ] `textarea` 类型展示“重新生成”和“AI 润色”。
- [ ] 空 `input` 可以点击“重新生成”。
- [ ] 空 `textarea` 可以点击“重新生成”。
- [ ] 空内容时“AI 润色”禁用或提示。
- [ ] 点击“重新生成”成功后直接替换当前内容。
- [ ] 点击“AI 润色”成功后直接替换当前内容。
- [ ] 请求失败时保留原内容。
- [ ] 请求中禁用两个 AI 辅助按钮。
- [ ] 请求中禁用 submit。
- [ ] 敏感字段请求前显示 warning，但仍允许用户点击“重新生成”和“AI 润色”。
- [ ] 服务端返回 `metadata.warnings` 时表单显示 warning。
- [ ] 明显凭据错误返回时保留原内容并展示错误。
- [ ] AI 请求不会自动提交 ask_user_question。
- [ ] request 变化后旧响应不会覆盖新 request 的内容。

### 5. 集成测试 / smoke

- [ ] 创建 Dano client。
- [ ] 打开 SSE。
- [ ] 触发一个 `ask_user_question` input 请求。
- [ ] 通过 `POST /api/clients/<clientId>/messages` 发送 `field_assist`。
- [ ] 收到 `field_assist` response。
- [ ] 当前 session transcript 中没有新增“请润色”用户消息。
- [ ] session list 中没有出现 field assist 临时 session。
- [ ] 再发送 `answer_question`，流程正常继续。

### 6. 验证命令

完成实现后至少运行：

```bash
pnpm run check
pnpm run test
pnpm run build
```

如已有 deploy smoke：

```bash
pnpm --filter @dano/app run deploy:smoke
```

## 分阶段落地

### Phase 1：协议和 mock 服务

- 增加 `field_assist` protocol 类型。
- 增加 `bridgeStore.fieldAssist()`。
- `QuestionToolCard.svelte` 给 text / textarea 字段加按钮和直接替换逻辑。
- 服务端用 mock `FieldAssistService` 返回固定文本。
- 完成前端和协议测试。

### Phase 2：Pi SDK transient backend

- 实现 `TransientAIClient`。
- 实现 `PiSdkTransientAIClient`。
- 先用本地 typings 确认并锁定 Pi SDK API 名称，再按确认后的 API 写实现；不把示意代码中的事件名 / ResourceLoader 构造方式当成事实。
- 使用 in-memory session。
- 禁用 tools。
- 使用 locked resource loader。
- 接入当前 model / default model。
- 完成 timeout / abort / dispose。

### Phase 3：生产治理

- 敏感字段策略。
- 输入 / 输出长度限制。
- rate limit。
- global concurrency。
- audit metadata。
- structured error code。

### Phase 4：集成验收

- 完成 HTTP/SSE command 集成测试。
- 验证不污染 transcript / session list。
- 验证 ask_user_question 原有提交路径不变。
- 更新 README 或 docs。

## 验收标准

### 产品行为

- [ ] `ask_user_question` 的 `input` 展示“重新生成”和“AI 润色”。
- [ ] `ask_user_question` 的 `textarea` 展示“重新生成”和“AI 润色”。
- [ ] “重新生成”成功后直接替换当前字段内容。
- [ ] “AI 润色”成功后直接替换当前字段内容。
- [ ] “AI 润色”使用当前字段文本作为模型 `user` prompt 本身。
- [ ] “重新生成”可在空字段中基于字段上下文生成业务内容。
- [ ] 不展示预览 / 候选 / 使用建议按钮。
- [ ] 不自动提交用户回答。
- [ ] 请求失败时原内容不变。

### 架构行为

- [ ] 通过现有 `POST /api/clients/<clientId>/messages` command 通道发送。
- [ ] 不新增 WebSocket RPC。
- [ ] 不新增独立 `/api/field-assist` endpoint。
- [ ] 服务端复用 Pi SDK / Pi runtime 模型配置。
- [ ] 服务端使用 Pi SDK 原生 `SessionManager.inMemory()`。
- [ ] 不把 provider credentials 下发浏览器。
- [ ] 不复用当前 live AgentSession。
- [ ] 不调用 `answer_question`。
- [ ] 不创建普通 detached session。
- [ ] 不写入当前 transcript。
- [ ] 不出现在 session list。
- [ ] 不触发 tools。
- [ ] 不触发 extensions side effect。

### 安全与生产化

- [ ] 敏感字段表单验证显示警告；明显秘密值由服务端拒绝。
- [ ] 有输入长度限制。
- [ ] 有输出长度限制。
- [ ] 有 timeout。
- [ ] 有 abort cleanup。
- [ ] 有 rate limit。
- [ ] 有 global concurrency limit。
- [ ] 日志不记录原文。
- [ ] 日志不记录模型输出。
- [ ] audit 只记录结构化元数据。
- [ ] 所有错误有稳定错误码。

## 实现备注

这个功能本质是：

```text
临时字段文本处理器
```

不是：

```text
一次用户正式对话
一个新 Agent 任务
一个 detached workflow
一个 ask_user_question 回答
```

因此最终实现必须落在：

```text
FieldAssistService + TransientAIClient + Pi SDK in-memory no-tools one-shot call
```

而不是：

```text
live session prompt
answer_question
普通 detached session
browser/provider direct call
```
