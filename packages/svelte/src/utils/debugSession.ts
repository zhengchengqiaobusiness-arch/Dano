import type {
  RpcImageContent,
  RpcModel,
  RpcSessionState,
  RpcThinkingLevel,
  RpcTranscriptContent,
  RpcTranscriptContentBlock,
  RpcTranscriptMessage,
  RpcWorkspaceSummary,
} from "@pi-web/bridge/types";
import type { SessionEntry } from "../composables/bridgeStore.svelte";
import type { RpcModelInfo } from "./models";

export const DEBUG_WORKSPACE_ID = "debug-workspace";
export const DEBUG_WORKSPACE_NAME = "Debug";
export const DEBUG_WORKSPACE_PATH = "debug://workspace";

export interface DebugSession {
  id: string;
  path: string;
  name: string;
  transcript: RpcTranscriptMessage[];
  sessionState: RpcSessionState;
  updatedAt: string;
  tokensPerSecond: number;
  backingWorkspacePath?: string;
  backingWorkspaceName?: string;
}

export interface DebugStreamChunk {
  delayMs: number;
  message: RpcTranscriptMessage;
}

export interface DebugStreamPlan {
  chunks: DebugStreamChunk[];
}

export interface DebugPromptResult {
  session: DebugSession;
  stream?: DebugStreamPlan;
}

const DEFAULT_DEBUG_TPS = 24;
const MIN_DEBUG_TPS = 1;
const MAX_DEBUG_TPS = 240;

let debugIdCounter = 0;
let debugSessionCounter = 0;

function nowIso(): string {
  return new Date().toISOString();
}

function nextDebugId(prefix: string): string {
  debugIdCounter += 1;
  return `debug-${prefix}-${debugIdCounter}`;
}

function normalizeModel(model?: RpcModel | null): RpcModel | undefined {
  if (!model?.id || !model.provider) return undefined;
  return {
    id: model.id,
    provider: model.provider,
    name: model.name,
    api: model.api,
    reasoning: model.reasoning,
    contextWindow: model.contextWindow,
    maxTokens: model.maxTokens,
  };
}

function contentFromTextAndImages(
  text: string,
  images: readonly RpcImageContent[] = [],
): RpcTranscriptContent | undefined {
  const trimmed = text.trim();
  const blocks: RpcTranscriptContentBlock[] = images.map(image => ({
    type: "image" as const,
    data: image.data,
    mimeType: image.mimeType,
    text: "Debug image",
  }));

  if (trimmed) {
    if (blocks.length === 0) return trimmed;
    return [{ type: "text", text: trimmed }, ...blocks];
  }

  if (blocks.length > 0) return blocks;
  return undefined;
}

function materializeMessage(
  message: Omit<RpcTranscriptMessage, "id" | "transcriptKey" | "timestamp"> & {
    id?: string;
    transcriptKey?: string;
    timestamp?: string;
  },
): RpcTranscriptMessage {
  const id = message.id?.trim() || nextDebugId("msg");
  return {
    ...message,
    id,
    transcriptKey: message.transcriptKey?.trim() || id,
    timestamp: message.timestamp?.trim() || nowIso(),
  };
}

function assistantMessage(
  text: string,
  images: readonly RpcImageContent[] = [],
): RpcTranscriptMessage {
  return materializeMessage({
    role: "assistant",
    content: contentFromTextAndImages(text, images),
  });
}

function userMessage(
  text: string,
  images: readonly RpcImageContent[] = [],
): RpcTranscriptMessage {
  return materializeMessage({
    role: "user",
    content: contentFromTextAndImages(text, images),
  });
}

function systemMessage(block: RpcTranscriptContentBlock): RpcTranscriptMessage {
  return materializeMessage({ role: "system", content: [block] });
}

function errorMessage(message: string): RpcTranscriptMessage {
  return materializeMessage({
    role: "assistant",
    stopReason: "error",
    errorMessage: message,
  });
}

function introMessage(
  sessionName: string,
  backingWorkspacePath?: string,
  tokensPerSecond: number = DEFAULT_DEBUG_TPS,
): RpcTranscriptMessage {
  const workspaceLine = backingWorkspacePath
    ? `- Bound workspace: \`${backingWorkspacePath}\``
    : "- No workspace bound for file reads or workspace entry lookup.";

  return assistantMessage(
    [
      `# ${sessionName}`,
      "This session stays in memory only and never sends a real LLM request.",
      "",
      "- Plain submit appends an assistant Markdown message.",
      "- `/assistant <markdown>` appends assistant content.",
      "- `/user <text>` appends a user message.",
      "- `/fixture markdown|tool-read|tool-bash|tool-edit|tool-write|mixed|error` inserts samples.",
      "- `/tps <number>` sets the local debug streaming speed.",
      `- Current debug stream speed: ${clampDebugTps(tokensPerSecond)} TPS.`,
      "- `/json <payload>` appends transcript message JSON or raw content block JSON.",
      "- `/name <title>` renames the session.",
      "- `/clear` resets the transcript to this help message.",
      workspaceLine,
    ].join("\n"),
  );
}

function syncSession(
  session: DebugSession,
  overrides: Partial<DebugSession> = {},
): DebugSession {
  const name = overrides.name?.trim() || session.name;
  const transcript = overrides.transcript ?? session.transcript;
  const sessionState = overrides.sessionState ?? session.sessionState;
  const tokensPerSecond = clampDebugTps(
    typeof overrides.tokensPerSecond === "number"
      ? overrides.tokensPerSecond
      : session.tokensPerSecond,
  );

  return {
    ...session,
    ...overrides,
    name,
    transcript,
    updatedAt: nowIso(),
    tokensPerSecond,
    sessionState: {
      ...sessionState,
      sessionId: session.id,
      sessionName: name,
      sessionFile: session.path,
      workspacePath: session.backingWorkspacePath,
      isStreaming: sessionState.isStreaming === true,
      isCompacting: sessionState.isCompacting === true,
      messageCount: transcript.length,
      pendingMessageCount: 0,
      autoCompactionEnabled: sessionState.autoCompactionEnabled ?? false,
    },
  };
}

export function setDebugSessionStreaming(
  session: DebugSession,
  isStreaming: boolean,
): DebugSession {
  return syncSession(session, {
    sessionState: {
      ...session.sessionState,
      isStreaming,
    },
  });
}

function resetTranscript(session: DebugSession): DebugSession {
  return setDebugSessionStreaming(
    syncSession(session, {
      transcript: [
        introMessage(
          session.name,
          session.backingWorkspacePath,
          session.tokensPerSecond,
        ),
      ],
    }),
    false,
  );
}

function appendMessages(
  session: DebugSession,
  messages: readonly RpcTranscriptMessage[],
): DebugSession {
  if (messages.length === 0) return session;
  return syncSession(session, {
    transcript: [...session.transcript, ...messages],
  });
}

export function replaceDebugSessionMessage(
  session: DebugSession,
  message: RpcTranscriptMessage,
): DebugSession {
  const messageId = message.id?.trim();
  if (!messageId) return session;
  const transcript = session.transcript.map(entry =>
    entry.id === messageId ? { ...message } : entry,
  );
  return syncSession(session, { transcript });
}

function clampDebugTps(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_DEBUG_TPS;
  return Math.min(MAX_DEBUG_TPS, Math.max(MIN_DEBUG_TPS, Math.round(value)));
}

function parseDebugTps(value: string): number {
  const parsed = Number.parseFloat(value.trim());
  if (!Number.isFinite(parsed)) {
    throw new Error("TPS must be a finite number.");
  }
  return clampDebugTps(parsed);
}

function delayForTokens(tokenCount: number, tps: number): number {
  const normalizedTps = clampDebugTps(tps);
  const tokens = Math.max(1, Math.round(tokenCount));
  return Math.max(40, Math.round((tokens / normalizedTps) * 1000));
}

function countApproxTokens(text: string): number {
  const matches = text.match(/```|`|[A-Za-z_][A-Za-z0-9_]*|\d+|[^\s]/g);
  return matches?.length ?? 1;
}

function trimCommandArgument(
  value: string | undefined,
  fallback: string,
): string {
  const trimmed = value?.trim();
  if (!trimmed) throw new Error(fallback);
  return trimmed;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isContentBlockLike(
  value: unknown,
): value is RpcTranscriptContentBlock {
  return isRecord(value) && typeof value.type === "string";
}

function isMessageLike(value: unknown): value is Partial<RpcTranscriptMessage> {
  return isRecord(value) && typeof value.role === "string";
}

function normalizeMessageLike(
  value: Partial<RpcTranscriptMessage>,
): RpcTranscriptMessage {
  if (!value.role?.trim()) {
    throw new Error("Debug JSON message is missing a role.");
  }

  const content = value.content;
  const text = typeof value.text === "string" ? value.text : undefined;
  if (
    content !== undefined &&
    typeof content !== "string" &&
    !Array.isArray(content)
  ) {
    throw new Error("Debug JSON message content must be a string or an array.");
  }
  if (
    content === undefined &&
    text === undefined &&
    value.errorMessage === undefined
  ) {
    throw new Error(
      "Debug JSON message must provide content, text, or errorMessage.",
    );
  }

  return materializeMessage({
    role: value.role,
    content,
    text,
    stopReason: value.stopReason,
    errorMessage: value.errorMessage,
    toolCallId: value.toolCallId,
    toolName: value.toolName,
    isError: value.isError,
    details: value.details,
    id: value.id,
    transcriptKey: value.transcriptKey,
    timestamp: value.timestamp,
  });
}

function normalizeJsonMessages(payload: unknown): RpcTranscriptMessage[] {
  if (typeof payload === "string") {
    return [assistantMessage(payload)];
  }

  if (Array.isArray(payload)) {
    if (payload.every(isMessageLike)) {
      return payload.map(message => normalizeMessageLike(message));
    }
    if (
      payload.every(
        item => typeof item === "string" || isContentBlockLike(item),
      )
    ) {
      return [
        materializeMessage({
          role: "assistant",
          content: payload as RpcTranscriptContent,
        }),
      ];
    }
    throw new Error(
      "Debug JSON array must contain transcript messages or content blocks.",
    );
  }

  if (isMessageLike(payload)) {
    return [normalizeMessageLike(payload)];
  }

  if (isContentBlockLike(payload)) {
    return [materializeMessage({ role: "assistant", content: [payload] })];
  }

  if (isRecord(payload) && Array.isArray(payload.messages)) {
    return normalizeJsonMessages(payload.messages);
  }

  if (isRecord(payload) && isMessageLike(payload.message)) {
    return [normalizeMessageLike(payload.message)];
  }

  throw new Error(
    "Debug JSON payload must be a transcript message, a messages array, or raw content blocks.",
  );
}

const MARKDOWN_FIXTURE_LINES = [
  "## Markdown Fixture",
  "",
  "Use this to verify headings, emphasis, lists, tables, code fences, inline file refs, and Mermaid.",
  "",
  "- Bold: **important**",
  "- Italic: *subtle*",
  "- Inline code: `packages/svelte/src/components/ChatTranscript.svelte:564`",
  "",
  "| Column | Value |",
  "| --- | --- |",
  "| Mode | Debug |",
  "| Scope | In-memory session |",
  "",
  "> Blockquotes and file references should keep the renderer stable.",
  "",
  "```ts",
  "export function renderPreview(name: string) {",
  "  return `hello ${name}`;",
  "}",
  "```",
  "",
  "```mermaid",
  "flowchart LR",
  "  User --> ChatTranscript",
  "  ChatTranscript --> MarkdownRenderer",
  "```",
];

const READ_FIXTURE_PATH =
  "packages/svelte/src/components/ChatTranscript.svelte";
const READ_FIXTURE_ARGS = {
  path: READ_FIXTURE_PATH,
  offset: 552,
  limit: 40,
};
const READ_FIXTURE_RESULT = [
  '{#if block.kind === "tool"}',
  '  <div class="tool-inline-block" data-tree-entry-id={block.resultSourceMessageId}>',
  '    <div class="tool-inline" data-status={toolBlockDescriptor(block).status}>',
  "      <button",
  '        type="button"',
  '        class="tool-inline-toggle"',
  "        onclick={() => blockState.toggleToolBlock(messageStableKey(item.message, item.messageIndex), bIdx)}",
  "        aria-expanded={blockState.isToolBlockExpanded(messageStableKey(item.message, item.messageIndex), bIdx)}",
  "      >",
  '        <span class="tool-inline-summary">',
  '          <span class="tool-inline-name">{toolBlockDescriptor(block).name}</span>',
  "        </span>",
  "      </button>",
  "    </div>",
  "  </div>",
  "{/if}",
].join("\n");

const BASH_FIXTURE_ARGS = {
  command: [
    "pnpm -C packages/svelte check",
    "pnpm run build:web",
    'rg -n "MarkdownRenderer" packages/svelte/src/components/ChatTranscript.svelte',
  ].join("\n"),
  timeout: 180,
};
const BASH_FIXTURE_PARTIAL_OUTPUT = [
  "> @pi-web/svelte check",
  "Loading svelte-check...",
  "svelte-check found 0 errors and 0 warnings",
  "",
  "> @woxqaq/pi-web build:web",
  "vite v8 building client environment for production...",
  "transforming modules...",
].join("\n");
const BASH_FIXTURE_FINAL_OUTPUT = [
  BASH_FIXTURE_PARTIAL_OUTPUT,
  "rendering chunks...",
  "computing gzip size...",
  "../../web-dist/assets/index.js 536.07 kB | gzip: 184.87 kB",
  "../../web-dist/assets/vendor-mermaid.js 1557.84 kB | gzip: 489.70 kB",
  "packages/svelte/src/components/ChatTranscript.svelte:564:                <MarkdownRenderer",
  "Done in 1.03s.",
  "Command exited with code 0",
].join("\n");

const EDIT_FIXTURE_ONE_ARGS = {
  path: "packages/svelte/src/App.svelte",
  edits: [
    {
      oldText: "let debugSessionsEnabled = $derived(debugModeAvailable);",
      newText: [
        "let debugSessionsEnabled = $derived(",
        "  debugModeAvailable && (showDebugFixtures || debugSessions.length > 0),",
        ");",
      ].join("\n"),
    },
    {
      oldText: "const debugWorkspaceSummary = createDebugWorkspaceSummary();",
      newText: [
        "const debugWorkspaceSummary = createDebugWorkspaceSummary({",
        '  accent: "violet",',
        '  labelSuffix: " local-only",',
        "});",
      ].join("\n"),
    },
    {
      oldText: "scheduleDebugStream(activeDebugSessionPath, stream);",
      newText: [
        "scheduleDebugStream(activeDebugSessionPath, stream, {",
        "  announce: true,",
        "  preserveExpandedBlocks: true,",
        "});",
      ].join("\n"),
    },
    {
      oldText: "pendingRevision = null;",
      newText: [
        "pendingRevision = null;",
        "editQueuedPayload = null;",
        "debugLastAppliedFixture = payload.message;",
      ].join("\n"),
    },
  ],
};
const EDIT_FIXTURE_ONE_DIFF = [
  "--- a/packages/svelte/src/App.svelte",
  "+++ b/packages/svelte/src/App.svelte",
  "@@ -78,7 +78,10 @@",
  "-const debugWorkspaceSummary = createDebugWorkspaceSummary();",
  "+const debugWorkspaceSummary = createDebugWorkspaceSummary({",
  '+  accent: "violet",',
  '+  labelSuffix: " local-only",',
  "+});",
  "@@ -258,7 +261,9 @@",
  "-let debugSessionsEnabled = $derived(debugModeAvailable);",
  "+let debugSessionsEnabled = $derived(",
  "+  debugModeAvailable && (showDebugFixtures || debugSessions.length > 0),",
  "+);",
  "@@ -894,7 +899,12 @@",
  "-pendingRevision = null;",
  "+pendingRevision = null;",
  "+editQueuedPayload = null;",
  "+debugLastAppliedFixture = payload.message;",
  "@@ -903,7 +913,11 @@",
  "-scheduleDebugStream(activeDebugSessionPath, stream);",
  "+scheduleDebugStream(activeDebugSessionPath, stream, {",
  "+  announce: true,",
  "+  preserveExpandedBlocks: true,",
  "+});",
].join("\n");

const EDIT_FIXTURE_TWO_ARGS = {
  path: "packages/svelte/src/components/MarkdownRenderer.svelte",
  edits: [
    {
      oldText:
        "function replaceWithMermaidSource(block: HTMLElement, source: string, statusText: string) {",
      newText: [
        "function replaceWithMermaidSource(",
        "  block: HTMLElement,",
        "  source: string,",
        "  statusText: string,",
        ") {",
      ].join("\n"),
    },
    {
      oldText:
        "if (currentStatus === statusText && currentSource === source) return;",
      newText: [
        "if (",
        "  currentStatus === statusText &&",
        "  currentSource === source &&",
        '  block.dataset.mermaidSourceStable === "true"',
        ") return;",
      ].join("\n"),
    },
    {
      oldText: "block.replaceChildren(status, pre);",
      newText: [
        'block.dataset.mermaidSourceStable = "true";',
        "block.replaceChildren(status, pre);",
        'queueMicrotask(() => block.removeAttribute("data-mermaid-source-stable"));',
      ].join("\n"),
    },
  ],
};
const EDIT_FIXTURE_TWO_DIFF = [
  "--- a/packages/svelte/src/components/MarkdownRenderer.svelte",
  "+++ b/packages/svelte/src/components/MarkdownRenderer.svelte",
  "@@ -193,7 +193,11 @@",
  "-function replaceWithMermaidSource(block: HTMLElement, source: string, statusText: string) {",
  "+function replaceWithMermaidSource(",
  "+  block: HTMLElement,",
  "+  source: string,",
  "+  statusText: string,",
  "+) {",
  "@@ -196,7 +200,11 @@",
  "-if (currentStatus === statusText && currentSource === source) return;",
  "+if (",
  "+  currentStatus === statusText &&",
  "+  currentSource === source &&",
  '+  block.dataset.mermaidSourceStable === "true"',
  "+) return;",
  "@@ -212,4 +220,6 @@",
  "-block.replaceChildren(status, pre);",
  '+block.dataset.mermaidSourceStable = "true";',
  "+block.replaceChildren(status, pre);",
  '+queueMicrotask(() => block.removeAttribute("data-mermaid-source-stable"));',
].join("\n");

const WRITE_FIXTURE_PATH = "packages/svelte/src/utils/debugPreviewRegistry.ts";
const WRITE_FIXTURE_CONTENT = [
  "export interface DebugPreviewEntry {",
  "  id: string;",
  "  label: string;",
  '  kind: "markdown" | "tool" | "mixed";',
  "  updatedAt: string;",
  "}",
  "",
  "export function createDebugPreviewEntry(",
  "  id: string,",
  "  label: string,",
  "  kind: DebugPreviewEntry['kind'],",
  "): DebugPreviewEntry {",
  "  return {",
  "    id,",
  "    label,",
  "    kind,",
  "    updatedAt: new Date().toISOString(),",
  "  };",
  "}",
].join("\n");

const ERROR_FIXTURE_MESSAGE =
  "Synthetic debug failure: parser exited before the final tool result could be normalized.";

function buildTextStream(
  session: DebugSession,
  lines: readonly string[],
  linesPerChunk: number,
): DebugPromptResult {
  const messageId = nextDebugId("msg");
  const snapshots = buildProgressiveTextMessages(
    lines,
    messageId,
    linesPerChunk,
  );
  const [initialMessage, ...remainingMessages] = snapshots;
  const tps = session.tokensPerSecond;
  let previousText =
    typeof initialMessage.content === "string" ? initialMessage.content : "";
  return {
    session: setDebugSessionStreaming(
      appendMessages(session, [initialMessage]),
      true,
    ),
    stream: {
      chunks: remainingMessages.map(message => {
        const nextText =
          typeof message.content === "string" ? message.content : "";
        const appendedText = nextText.slice(previousText.length);
        previousText = nextText;
        return {
          delayMs: delayForTokens(countApproxTokens(appendedText), tps),
          message,
        };
      }),
    },
  };
}

function buildSnapshotStream(
  session: DebugSession,
  initialMessages: readonly RpcTranscriptMessage[],
  snapshots: readonly RpcTranscriptMessage[],
  stageTexts: readonly string[],
): DebugPromptResult {
  const [initialSnapshot, ...remainingSnapshots] = snapshots;
  return {
    session: setDebugSessionStreaming(
      appendMessages(session, [...initialMessages, initialSnapshot]),
      true,
    ),
    stream: {
      chunks: remainingSnapshots.map((message, index) => ({
        delayMs: delayForTokens(
          countApproxTokens(
            stageTexts[index] ??
              JSON.stringify(message.content ?? message.errorMessage ?? ""),
          ),
          session.tokensPerSecond,
        ),
        message,
      })),
    },
  };
}

function progressiveMarkdownFixture(session: DebugSession): DebugPromptResult {
  return buildTextStream(session, MARKDOWN_FIXTURE_LINES, 4);
}

function progressiveReadFixture(session: DebugSession): DebugPromptResult {
  const assistantId = nextDebugId("msg");
  const toolCallId = nextDebugId("tool");
  const intro = [
    "### Read Fixture",
    "",
    `Inspecting \`${READ_FIXTURE_PATH}\` to verify code rendering, file references, and expanded tool details.`,
  ].join("\n");
  const outro = [
    "### Read Summary",
    "",
    "- Read blocks should open as code panels.",
    "- File references like `packages/svelte/src/components/ChatTranscript.svelte:564` should stay clickable.",
  ].join("\n");

  const snapshots = [
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [{ type: "text", text: intro }],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "read",
          arguments: READ_FIXTURE_ARGS,
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "read",
          arguments: READ_FIXTURE_ARGS,
        },
        {
          type: "toolResult",
          content: [{ type: "text", text: READ_FIXTURE_RESULT }],
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "read",
          arguments: READ_FIXTURE_ARGS,
        },
        {
          type: "toolResult",
          content: [{ type: "text", text: READ_FIXTURE_RESULT }],
        },
        { type: "text", text: outro },
      ],
    }),
  ];

  return buildSnapshotStream(session, [], snapshots, [
    `${intro}\nread ${JSON.stringify(READ_FIXTURE_ARGS)}`,
    READ_FIXTURE_RESULT,
    outro,
  ]);
}

function progressiveBashFixture(session: DebugSession): DebugPromptResult {
  const assistantId = nextDebugId("msg");
  const toolCallId = nextDebugId("tool");
  const intro = [
    "### Bash Fixture",
    "",
    "Running a synthetic build command to stress the bash tool output panel and summary metadata.",
  ].join("\n");
  const outro = [
    "### Bash Summary",
    "",
    "- Multi-line commands should keep the `$ ...` preview.",
    "- Long output should remain scrollable inside the tool block.",
  ].join("\n");

  const snapshots = [
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [{ type: "text", text: intro }],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "bash",
          arguments: BASH_FIXTURE_ARGS,
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "bash",
          arguments: BASH_FIXTURE_ARGS,
        },
        {
          type: "toolResult",
          content: [{ type: "text", text: BASH_FIXTURE_PARTIAL_OUTPUT }],
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "bash",
          arguments: BASH_FIXTURE_ARGS,
        },
        {
          type: "toolResult",
          content: [{ type: "text", text: BASH_FIXTURE_FINAL_OUTPUT }],
        },
        { type: "text", text: outro },
      ],
    }),
  ];

  return buildSnapshotStream(session, [], snapshots, [
    `${intro}\n${BASH_FIXTURE_ARGS.command}`,
    BASH_FIXTURE_PARTIAL_OUTPUT,
    `${BASH_FIXTURE_FINAL_OUTPUT}\n${outro}`,
  ]);
}

function progressiveEditFixture(session: DebugSession): DebugPromptResult {
  const assistantId = nextDebugId("msg");
  const toolCallOneId = nextDebugId("tool");
  const toolCallTwoId = nextDebugId("tool");
  const intro = [
    "### Edit Fixture",
    "",
    "Applying two synthetic edit passes so the transcript shows multiple diff-heavy tool blocks in a single assistant response.",
  ].join("\n");
  const transition = [
    "### Second Edit Pass",
    "",
    "Follow-up edits tighten the Mermaid placeholder guard and add a stability marker reset.",
  ].join("\n");
  const partialDiffOne = EDIT_FIXTURE_ONE_DIFF.split("\n")
    .slice(0, 12)
    .join("\n");
  const partialDiffTwo = EDIT_FIXTURE_TWO_DIFF.split("\n")
    .slice(0, 10)
    .join("\n");
  const outro = [
    "### Edit Summary",
    "",
    "- Two separate edit blocks should stack cleanly in one assistant message.",
    "- Each block should show diff stats, expanded diff content, and its own path/edits payload.",
    "- The second block should feel substantial enough to test scrolling inside the diff viewer.",
  ].join("\n");

  const snapshots = [
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [{ type: "text", text: intro }],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallOneId,
          name: "edit",
          arguments: EDIT_FIXTURE_ONE_ARGS,
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallOneId,
          name: "edit",
          arguments: EDIT_FIXTURE_ONE_ARGS,
        },
        {
          type: "toolResult",
          content: [
            {
              type: "text",
              text: "Applied 4 targeted replacements in App.svelte.",
            },
          ],
          details: { diff: partialDiffOne },
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallOneId,
          name: "edit",
          arguments: EDIT_FIXTURE_ONE_ARGS,
        },
        {
          type: "toolResult",
          content: [
            {
              type: "text",
              text: "Applied 4 targeted replacements in App.svelte.",
            },
          ],
          details: { diff: EDIT_FIXTURE_ONE_DIFF },
        },
        { type: "text", text: transition },
        {
          type: "toolCall",
          id: toolCallTwoId,
          name: "edit",
          arguments: EDIT_FIXTURE_TWO_ARGS,
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallOneId,
          name: "edit",
          arguments: EDIT_FIXTURE_ONE_ARGS,
        },
        {
          type: "toolResult",
          content: [
            {
              type: "text",
              text: "Applied 4 targeted replacements in App.svelte.",
            },
          ],
          details: { diff: EDIT_FIXTURE_ONE_DIFF },
        },
        { type: "text", text: transition },
        {
          type: "toolCall",
          id: toolCallTwoId,
          name: "edit",
          arguments: EDIT_FIXTURE_TWO_ARGS,
        },
        {
          type: "toolResult",
          content: [
            {
              type: "text",
              text: "Applied 3 targeted replacements in MarkdownRenderer.svelte.",
            },
          ],
          details: { diff: partialDiffTwo },
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallOneId,
          name: "edit",
          arguments: EDIT_FIXTURE_ONE_ARGS,
        },
        {
          type: "toolResult",
          content: [
            {
              type: "text",
              text: "Applied 4 targeted replacements in App.svelte.",
            },
          ],
          details: { diff: EDIT_FIXTURE_ONE_DIFF },
        },
        { type: "text", text: transition },
        {
          type: "toolCall",
          id: toolCallTwoId,
          name: "edit",
          arguments: EDIT_FIXTURE_TWO_ARGS,
        },
        {
          type: "toolResult",
          content: [
            {
              type: "text",
              text: "Applied 3 targeted replacements in MarkdownRenderer.svelte.",
            },
          ],
          details: { diff: EDIT_FIXTURE_TWO_DIFF },
        },
        { type: "text", text: outro },
      ],
    }),
  ];

  return buildSnapshotStream(session, [], snapshots, [
    `${intro}\n${JSON.stringify(EDIT_FIXTURE_ONE_ARGS)}`,
    partialDiffOne,
    `${EDIT_FIXTURE_ONE_DIFF}\n${transition}\n${JSON.stringify(EDIT_FIXTURE_TWO_ARGS)}`,
    partialDiffTwo,
    `${EDIT_FIXTURE_TWO_DIFF}\n${outro}`,
  ]);
}

function progressiveWriteFixture(session: DebugSession): DebugPromptResult {
  const assistantId = nextDebugId("msg");
  const toolCallId = nextDebugId("tool");
  const intro = [
    "### Write Fixture",
    "",
    `Creating \`${WRITE_FIXTURE_PATH}\` to verify the code preview path for write operations.`,
  ].join("\n");
  const outro = [
    "### Write Summary",
    "",
    "- The write tool should preview the written file from tool arguments.",
    "- Expanded state should show code even if the result text itself is short.",
  ].join("\n");

  const snapshots = [
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [{ type: "text", text: intro }],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "write",
          arguments: {
            path: WRITE_FIXTURE_PATH,
            content: WRITE_FIXTURE_CONTENT,
          },
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "write",
          arguments: {
            path: WRITE_FIXTURE_PATH,
            content: WRITE_FIXTURE_CONTENT,
          },
        },
        {
          type: "toolResult",
          content: [{ type: "text", text: `Wrote ${WRITE_FIXTURE_PATH}` }],
        },
      ],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [
        { type: "text", text: intro },
        {
          type: "toolCall",
          id: toolCallId,
          name: "write",
          arguments: {
            path: WRITE_FIXTURE_PATH,
            content: WRITE_FIXTURE_CONTENT,
          },
        },
        {
          type: "toolResult",
          content: [{ type: "text", text: `Wrote ${WRITE_FIXTURE_PATH}` }],
        },
        { type: "text", text: outro },
      ],
    }),
  ];

  return buildSnapshotStream(session, [], snapshots, [
    `${intro}\n${WRITE_FIXTURE_CONTENT}`,
    `Wrote ${WRITE_FIXTURE_PATH}`,
    outro,
  ]);
}

function progressiveMixedFixture(session: DebugSession): DebugPromptResult {
  const assistantMessageId = nextDebugId("msg");
  const toolCallId = nextDebugId("tool");
  const user = userMessage(
    "Please preview the transcript renderer without sending a real LLM request.",
  );
  const thinkingLineOne = "I can stay entirely client-side.";
  const thinkingFull = [
    thinkingLineOne,
    "I should exercise both Markdown and tool block render branches.",
  ].join("\n");
  const toolArguments = {
    path: "packages/svelte/src/components/MarkdownRenderer.svelte",
    offset: 220,
    limit: 18,
  };
  const toolResultText = [
    "function enhanceInlineFileReferences() {",
    "  const root = markdownBody();",
    "  if (!root) return;",
    '  const nodes = root.querySelectorAll<HTMLElement>("code:not(pre code)");',
    "  // ...",
    "}",
  ].join("\n");
  const finalMarkdown = [
    "### Follow-up Markdown",
    "",
    "- Tool blocks should collapse into inline summaries.",
    "- Inline file refs like `packages/svelte/src/components/MarkdownRenderer.svelte:243` should stay clickable.",
  ].join("\n");

  const snapshots = [
    materializeMessage({
      id: assistantMessageId,
      role: "assistant",
      content: [{ type: "thinking", thinking: thinkingLineOne }],
    }),
    materializeMessage({
      id: assistantMessageId,
      role: "assistant",
      content: [
        { type: "thinking", thinking: thinkingFull },
        {
          type: "text",
          text: "I created a local debug session and inserted a synthetic tool call.",
        },
      ],
    }),
    materializeMessage({
      id: assistantMessageId,
      role: "assistant",
      content: [
        { type: "thinking", thinking: thinkingFull },
        {
          type: "text",
          text: "I created a local debug session and inserted a synthetic tool call.",
        },
        {
          type: "toolCall",
          id: toolCallId,
          name: "read",
          arguments: toolArguments,
        },
      ],
    }),
    materializeMessage({
      id: assistantMessageId,
      role: "assistant",
      content: [
        { type: "thinking", thinking: thinkingFull },
        {
          type: "text",
          text: "I created a local debug session and inserted a synthetic tool call.",
        },
        {
          type: "toolCall",
          id: toolCallId,
          name: "read",
          arguments: toolArguments,
        },
        {
          type: "toolResult",
          content: [{ type: "text", text: toolResultText }],
        },
      ],
    }),
    materializeMessage({
      id: assistantMessageId,
      role: "assistant",
      content: [
        { type: "thinking", thinking: thinkingFull },
        {
          type: "text",
          text: "I created a local debug session and inserted a synthetic tool call.",
        },
        {
          type: "toolCall",
          id: toolCallId,
          name: "read",
          arguments: toolArguments,
        },
        {
          type: "toolResult",
          content: [{ type: "text", text: toolResultText }],
        },
        { type: "text", text: finalMarkdown },
      ],
    }),
  ];

  return buildSnapshotStream(session, [user], snapshots, [
    thinkingFull,
    JSON.stringify(toolArguments),
    toolResultText,
    finalMarkdown,
  ]);
}

function progressiveErrorFixture(session: DebugSession): DebugPromptResult {
  const assistantId = nextDebugId("msg");
  const intro = [
    "### Error Fixture",
    "",
    "Preparing a synthetic tool response that will intentionally fail during normalization.",
  ].join("\n");
  const preError = [
    intro,
    "",
    "Attempting to parse the final tool payload...",
  ].join("\n");

  const snapshots = [
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [{ type: "text", text: intro }],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      content: [{ type: "text", text: preError }],
    }),
    materializeMessage({
      id: assistantId,
      role: "assistant",
      stopReason: "error",
      errorMessage: ERROR_FIXTURE_MESSAGE,
    }),
  ];

  return buildSnapshotStream(session, [], snapshots, [
    preError,
    ERROR_FIXTURE_MESSAGE,
  ]);
}

function streamingFixtureResult(
  session: DebugSession,
  name: string,
): DebugPromptResult | null {
  switch (name.trim().toLowerCase()) {
    case "markdown":
      return progressiveMarkdownFixture(session);
    case "tool-read":
    case "read":
      return progressiveReadFixture(session);
    case "tool-bash":
    case "bash":
      return progressiveBashFixture(session);
    case "tool-edit":
    case "edit":
      return progressiveEditFixture(session);
    case "tool-write":
    case "write":
      return progressiveWriteFixture(session);
    case "mixed":
    case "all":
      return progressiveMixedFixture(session);
    case "error":
      return progressiveErrorFixture(session);
    default:
      return null;
  }
}

function buildProgressiveTextMessages(
  lines: readonly string[],
  messageId: string,
  linesPerChunk: number,
): RpcTranscriptMessage[] {
  const snapshots: RpcTranscriptMessage[] = [];
  for (let end = linesPerChunk; end < lines.length; end += linesPerChunk) {
    snapshots.push(
      materializeMessage({
        id: messageId,
        role: "assistant",
        content: lines.slice(0, end).join("\n"),
      }),
    );
  }
  snapshots.push(
    materializeMessage({
      id: messageId,
      role: "assistant",
      content: lines.join("\n"),
    }),
  );
  return snapshots;
}

export function isDebugSessionPath(path: string | null | undefined): boolean {
  return typeof path === "string" && path.startsWith("debug://session/");
}

export function createDebugWorkspaceSummary(): RpcWorkspaceSummary {
  return {
    id: DEBUG_WORKSPACE_ID,
    name: DEBUG_WORKSPACE_NAME,
    path: DEBUG_WORKSPACE_PATH,
    updatedAt: nowIso(),
  };
}

export function createDebugSessionEntry(session: DebugSession): SessionEntry {
  return {
    id: session.id,
    name: session.name,
    path: session.path,
    timestamp: session.updatedAt,
    updatedAt: session.updatedAt,
    workspaceId: DEBUG_WORKSPACE_ID,
    workspaceName: DEBUG_WORKSPACE_NAME,
    workspacePath: DEBUG_WORKSPACE_PATH,
    isRunning: session.sessionState.isStreaming,
  };
}

export function createDebugSession(
  options: {
    model?: RpcModel | null;
    thinkingLevel?: RpcThinkingLevel | null;
    backingWorkspacePath?: string | null;
    backingWorkspaceName?: string | null;
  } = {},
): DebugSession {
  debugSessionCounter += 1;
  const sessionNumber = debugSessionCounter;
  const id = `debug-session-${sessionNumber}`;
  const path = `debug://session/${sessionNumber}`;
  const name = `Debug Session ${sessionNumber}`;
  const model = normalizeModel(options.model);
  const thinkingLevel = options.thinkingLevel ?? "medium";
  const backingWorkspacePath =
    options.backingWorkspacePath?.trim() || undefined;
  const session: DebugSession = {
    id,
    path,
    name,
    updatedAt: nowIso(),
    transcript: [introMessage(name, backingWorkspacePath, DEFAULT_DEBUG_TPS)],
    tokensPerSecond: DEFAULT_DEBUG_TPS,
    backingWorkspacePath,
    backingWorkspaceName: options.backingWorkspaceName?.trim() || undefined,
    sessionState: {
      model,
      thinkingLevel,
      isStreaming: false,
      isCompacting: false,
      steeringMode: "all",
      followUpMode: "all",
      sessionFile: path,
      sessionId: id,
      sessionName: name,
      workspacePath: backingWorkspacePath,
      autoCompactionEnabled: false,
      messageCount: 1,
      pendingMessageCount: 0,
    },
  };

  return syncSession(session);
}

export function renameDebugSession(
  session: DebugSession,
  name: string,
): DebugSession {
  const nextName = trimCommandArgument(
    name,
    "Debug session name cannot be empty.",
  );
  return syncSession(session, { name: nextName });
}

export function setDebugSessionModel(
  session: DebugSession,
  model?: RpcModel | null,
): DebugSession {
  const normalized = normalizeModel(model);
  const next = syncSession(session, {
    sessionState: {
      ...session.sessionState,
      model: normalized,
    },
  });
  if (!normalized) return next;
  return appendMessages(next, [
    systemMessage({
      type: "model_change",
      provider: normalized.provider,
      modelId: normalized.id,
    }),
  ]);
}

export function setDebugSessionThinkingLevel(
  session: DebugSession,
  thinkingLevel: RpcThinkingLevel,
): DebugSession {
  const next = syncSession(session, {
    sessionState: {
      ...session.sessionState,
      thinkingLevel,
    },
  });
  return appendMessages(next, [
    systemMessage({
      type: "thinking_level_change",
      thinkingLevel,
    }),
  ]);
}

export function setDebugSessionAutoCompaction(
  session: DebugSession,
  enabled: boolean,
): DebugSession {
  return syncSession(session, {
    sessionState: {
      ...session.sessionState,
      autoCompactionEnabled: enabled,
    },
  });
}

export function setDebugSessionTps(
  session: DebugSession,
  tokensPerSecond: number,
): DebugSession {
  const nextTps = clampDebugTps(tokensPerSecond);
  return appendMessages(syncSession(session, { tokensPerSecond: nextTps }), [
    assistantMessage(`Debug stream speed set to ${nextTps} TPS.`),
  ]);
}

export function debugSessionModelInfo(
  session: DebugSession,
): RpcModelInfo | null {
  const model = session.sessionState.model;
  if (!model?.id || !model.provider) return null;
  return {
    id: model.id,
    provider: model.provider,
    name: model.name ?? model.id,
    api: model.api,
    reasoning: model.reasoning,
    contextWindow: model.contextWindow,
    maxTokens: model.maxTokens,
  };
}

export function applyDebugPrompt(
  session: DebugSession,
  input: string,
  images: readonly RpcImageContent[] = [],
): DebugPromptResult {
  const trimmed = input.trim();
  if (!trimmed && images.length === 0) return { session };

  try {
    const commandMatch = trimmed.match(/^\/([a-z-]+)(?:\s+([\s\S]*))?$/);
    if (commandMatch) {
      const command = commandMatch[1]!.toLowerCase();
      const body = commandMatch[2] ?? "";

      switch (command) {
        case "assistant":
          return {
            session: appendMessages(session, [
              assistantMessage(
                trimCommandArgument(body, "Assistant content cannot be empty."),
                images,
              ),
            ]),
          };
        case "user":
          return {
            session: appendMessages(session, [
              userMessage(
                trimCommandArgument(body, "User content cannot be empty."),
                images,
              ),
            ]),
          };
        case "fixture": {
          const fixtureName = trimCommandArgument(
            body,
            "Fixture name is required.",
          );
          const streamingResult = streamingFixtureResult(session, fixtureName);
          if (streamingResult) return streamingResult;
          throw new Error(
            "Unknown debug fixture. Use markdown, tool-read, tool-bash, tool-edit, tool-write, mixed, or error.",
          );
        }
        case "json": {
          const jsonPayload = trimCommandArgument(
            body,
            "JSON payload is required.",
          );
          return {
            session: appendMessages(
              session,
              normalizeJsonMessages(JSON.parse(jsonPayload) as unknown),
            ),
          };
        }
        case "tps":
          return {
            session: setDebugSessionTps(
              session,
              parseDebugTps(
                trimCommandArgument(body, "TPS value is required."),
              ),
            ),
          };
        case "name":
          return { session: renameDebugSession(session, body) };
        case "clear":
        case "reset":
          return { session: resetTranscript(session) };
        default:
          throw new Error(
            "Unknown debug command. Use /assistant, /user, /fixture, /json, /tps, /name, or /clear.",
          );
      }
    }

    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      return {
        session: appendMessages(
          session,
          normalizeJsonMessages(JSON.parse(trimmed) as unknown),
        ),
      };
    }

    return {
      session: appendMessages(session, [assistantMessage(trimmed, images)]),
    };
  } catch (error) {
    return {
      session: appendMessages(session, [
        errorMessage(error instanceof Error ? error.message : String(error)),
      ]),
    };
  }
}
