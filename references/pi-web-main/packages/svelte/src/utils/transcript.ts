import type {
  RpcJsonObject,
  RpcJsonValue,
  RpcToolArguments,
  RpcToolResultDetails,
  RpcTranscriptContent,
  RpcTranscriptContentBlock,
  RpcTranscriptImageBlock,
  RpcTranscriptImageUrlBlock,
  RpcTranscriptMessage,
  RpcTranscriptSystemBlock,
  RpcTranscriptToolResultBlock,
} from "@pi-web/bridge/types";

export type JsonObject = RpcJsonObject;
export type JsonValue = RpcJsonValue;

export type TranscriptEntryLike = RpcTranscriptMessage;

export type ToolBlockStatus = "pending" | "success" | "error";

export interface TextContentBlock {
  kind: "text";
  text: string;
}

export type ToolResultBlock = TextContentBlock | ImageContentBlock;

export interface ToolContentBlock {
  kind: "tool";
  toolName: string;
  toolCallId?: string;
  toolArgs: RpcToolArguments | undefined;
  argumentsText: string;
  resultText?: string;
  resultBlocks?: ToolResultBlock[];
  resultDetails?: RpcToolResultDetails;
  resultSourceMessageId?: string;
  toolStatus: ToolBlockStatus;
}

export interface ThinkingContentBlock {
  kind: "thinking";
  text: string;
}

export interface ImageContentBlock {
  kind: "image";
  src: string;
  alt: string;
  mimeType?: string;
}

export type SystemBlockType =
  | "compaction"
  | "branch_summary"
  | "model_change"
  | "thinking_level_change"
  | "session_info";

export interface SystemContentBlock {
  kind: "system";
  systemType: SystemBlockType;
  label: string;
  title: string;
  body?: string;
  meta?: string;
}

export type ContentBlock =
  | TextContentBlock
  | ToolContentBlock
  | ThinkingContentBlock
  | ImageContentBlock
  | SystemContentBlock;

type ConfigSystemBlock = Extract<
  RpcTranscriptSystemBlock,
  { type: "model_change" | "thinking_level_change" }
>;
type ModelChangeSystemBlock = Extract<
  RpcTranscriptSystemBlock,
  { type: "model_change" }
>;
type ThinkingLevelChangeSystemBlock = Extract<
  RpcTranscriptSystemBlock,
  { type: "thinking_level_change" }
>;

export interface TranscriptMessageDisplayItem {
  kind: "message";
  message: TranscriptEntryLike;
  messageIndex: number;
}

export interface TranscriptSessionEventDisplayItem {
  kind: "session_event";
  key: string;
  label: string;
  model?: {
    provider?: string;
    id: string;
  };
  thinkingLevel?: string;
  sourceMessageIds: string[];
}

export interface PendingTranscriptSessionEvent {
  key: string;
  model?: {
    provider?: string;
    id: string;
  };
  thinkingLevel?: string;
  insertAfterMessageKey?: string | null;
}

export interface TranscriptConfigState {
  model?: {
    provider?: string;
    id: string;
  };
  thinkingLevel?: string;
}

export type TranscriptDisplayItem =
  | TranscriptMessageDisplayItem
  | TranscriptSessionEventDisplayItem;

interface TranscriptToolResultBlockWithSource extends RpcTranscriptToolResultBlock {
  sourceMessageId?: string;
}

type TranscriptContentItem =
  | string
  | (RpcTranscriptContentBlock & { sourceMessageId?: string });
type TranscriptImageBlock =
  | RpcTranscriptImageBlock
  | RpcTranscriptImageUrlBlock;

export function isErrorMessage(msg: TranscriptEntryLike): boolean {
  if (msg.role !== "assistant") return false;
  return msg.stopReason === "error" || msg.stopReason === "aborted";
}

export function errorMessageText(msg: TranscriptEntryLike): string {
  return msg.errorMessage ?? "";
}

export function isAbortedMessage(msg: TranscriptEntryLike): boolean {
  return msg.stopReason === "aborted";
}

export function isToolResultMessage(msg: TranscriptEntryLike): boolean {
  return msg.role === "toolResult" || msg.role === "tool";
}

export function isSystemMessage(msg: TranscriptEntryLike): boolean {
  return msg.role === "system";
}

export function messageContent(
  msg: Pick<TranscriptEntryLike, "content" | "text">,
): string {
  const content = msg.content;
  if (typeof content === "string") return content;
  if (typeof msg.text === "string") return msg.text;
  if (!Array.isArray(content)) return "";

  return content.map(contentItemText).filter(Boolean).join("\n");
}

export function contentBlocks(msg: TranscriptEntryLike): ContentBlock[] {
  const content = msg.content;
  const blocks: ContentBlock[] = [];

  if (typeof content === "string") {
    blocks.push({ kind: "text", text: content });
    return blocks;
  }

  if (typeof msg.text === "string") {
    blocks.push({ kind: "text", text: msg.text });
    return blocks;
  }

  if (!Array.isArray(content)) return blocks;

  for (let index = 0; index < content.length; index++) {
    const block = content[index];
    if (typeof block === "string") {
      blocks.push({ kind: "text", text: block });
      continue;
    }

    if (isSystemBlock(block)) {
      if (!isHiddenSystemBlock(block)) {
        blocks.push(systemContentBlock(block));
      }
      continue;
    }

    switch (block.type) {
      case "text":
        blocks.push({ kind: "text", text: block.text });
        continue;
      case "thinking": {
        const thinkingText = block.thinking.trim();
        if (thinkingText) {
          blocks.push({ kind: "thinking", text: thinkingText });
        }
        continue;
      }
      case "toolCall": {
        const nextToolResult = toolResultBlockFromItem(content[index + 1]);
        const resultText = nextToolResult
          ? toolResultText(nextToolResult)
          : undefined;
        const resultBlocks = nextToolResult
          ? toolResultBlocks(nextToolResult)
          : undefined;
        const resultDetails = nextToolResult?.details;
        const resultIsError = nextToolResult?.isError;
        const resultSourceMessageId = nextToolResult?.sourceMessageId;

        blocks.push({
          kind: "tool",
          toolName: block.name ?? "unknown",
          toolCallId: block.id,
          toolArgs: parseToolArguments(block.arguments),
          argumentsText: toolArgumentsText(block.arguments),
          resultText,
          resultBlocks,
          resultDetails,
          resultSourceMessageId,
          toolStatus: toolStatusFromResult(
            resultText,
            resultBlocks,
            resultIsError,
          ),
        });

        if (nextToolResult) {
          index += 1;
        }
        continue;
      }
      case "toolResult":
        blocks.push(...toolResultBlocks(block));
        continue;
      case "image":
      case "image_url": {
        const src = imageBlockSource(block);
        if (src) {
          blocks.push({
            kind: "image",
            src,
            alt: block.text ?? "Image attachment",
            mimeType: block.mimeType,
          });
        } else {
          blocks.push({ kind: "text", text: "[image]" });
        }
        continue;
      }
    }
  }

  return blocks;
}

export function normalizeTranscript(
  messages: readonly TranscriptEntryLike[],
): TranscriptEntryLike[] {
  const normalized: TranscriptEntryLike[] = [];

  for (const message of messages) {
    if (isToolResultMessage(message)) {
      const merged = appendToolResultToPreviousAssistant(normalized, message);
      if (!merged) {
        normalized.push(cloneMessage(message));
      }
      continue;
    }

    normalized.push(cloneMessage(message));
  }

  return normalized;
}

export function transcriptConfigState(
  messages: readonly TranscriptEntryLike[],
): TranscriptConfigState {
  const state: TranscriptConfigState = {};

  for (const message of messages) {
    const block = configSystemBlock(message);
    if (!block) continue;

    if (block.type === "model_change") {
      state.model = {
        provider: normalizeOptionalText(block.provider),
        id: normalizeText(block.modelId, "Unknown model"),
      };
    } else {
      state.thinkingLevel = normalizeText(block.thinkingLevel, "Unknown");
    }
  }

  return state;
}

export function buildTranscriptDisplayItems(
  messages: readonly TranscriptEntryLike[],
  options?: {
    pendingSessionEvent?: PendingTranscriptSessionEvent | null;
  },
): TranscriptDisplayItem[] {
  const items: TranscriptDisplayItem[] = [];
  let hasSeenNonConfigMessage = false;
  let index = 0;

  while (index < messages.length) {
    if (isHiddenTranscriptMessage(messages[index])) {
      index += 1;
      continue;
    }

    if (!configSystemBlock(messages[index])) {
      items.push({
        kind: "message",
        message: messages[index],
        messageIndex: index,
      });
      hasSeenNonConfigMessage = true;
      index += 1;
      continue;
    }

    const startIndex = index;
    let model: ModelChangeSystemBlock | undefined;
    let thinking: ThinkingLevelChangeSystemBlock | undefined;
    const sourceMessageIds: string[] = [];

    while (index < messages.length) {
      if (isHiddenTranscriptMessage(messages[index])) {
        index += 1;
        continue;
      }

      const block = configSystemBlock(messages[index]);
      if (!block) break;

      if (block.type === "model_change") {
        model = block;
      } else {
        thinking = block;
      }

      const messageId = messages[index]?.id;
      if (typeof messageId === "string" && messageId.trim()) {
        sourceMessageIds.push(messageId);
      }
      index += 1;
    }

    const normalizedModel = model
      ? {
          provider: normalizeOptionalText(model.provider),
          id: normalizeText(model.modelId, "Unknown model"),
        }
      : undefined;
    const normalizedThinkingLevel = thinking
      ? normalizeText(thinking.thinkingLevel, "Unknown")
      : undefined;

    items.push({
      kind: "session_event",
      key: sessionEventKey(messages, startIndex, index - 1),
      label: sessionEventLabel(
        hasSeenNonConfigMessage,
        Boolean(normalizedModel),
        Boolean(normalizedThinkingLevel),
      ),
      model: normalizedModel,
      thinkingLevel: normalizedThinkingLevel,
      sourceMessageIds,
    });
  }

  return insertPendingSessionEvent(items, options?.pendingSessionEvent);
}

interface TranscriptDisplayState extends TranscriptConfigState {
  hasSeenNonConfigMessage: boolean;
}

function insertPendingSessionEvent(
  items: TranscriptDisplayItem[],
  pendingEvent: PendingTranscriptSessionEvent | null | undefined,
): TranscriptDisplayItem[] {
  if (!pendingEvent || items.length === 0) return items;

  const insertIndex = pendingSessionEventInsertIndex(items, pendingEvent);
  const insertionState = displayStateBeforeIndex(items, insertIndex);
  const finalState = displayStateBeforeIndex(items, items.length);
  const pendingModel = pendingEvent.model
    ? normalizePendingSessionEventModel(pendingEvent.model)
    : undefined;
  const pendingThinkingLevel = normalizeOptionalText(
    pendingEvent.thinkingLevel,
  );
  const nextModel =
    pendingModel &&
    !sameTranscriptModel(insertionState.model, pendingModel) &&
    !sameTranscriptModel(finalState.model, pendingModel)
      ? pendingModel
      : undefined;
  const nextThinkingLevel =
    pendingThinkingLevel &&
    pendingThinkingLevel !== insertionState.thinkingLevel &&
    pendingThinkingLevel !== finalState.thinkingLevel
      ? pendingThinkingLevel
      : undefined;

  if (!nextModel && !nextThinkingLevel) return items;

  const item: TranscriptSessionEventDisplayItem = {
    kind: "session_event",
    key: pendingEvent.key,
    label: sessionEventLabel(
      insertionState.hasSeenNonConfigMessage,
      Boolean(nextModel),
      Boolean(nextThinkingLevel),
    ),
    model: nextModel,
    thinkingLevel: nextThinkingLevel,
    sourceMessageIds: [],
  };

  const previousItem = items[insertIndex - 1];
  if (
    previousItem?.kind === "session_event" &&
    !insertionState.hasSeenNonConfigMessage
  ) {
    const mergedItem: TranscriptSessionEventDisplayItem = {
      kind: "session_event",
      key: `${previousItem.key}:${pendingEvent.key}`,
      label: item.label,
      model: item.model ?? previousItem.model,
      thinkingLevel: item.thinkingLevel ?? previousItem.thinkingLevel,
      sourceMessageIds: previousItem.sourceMessageIds,
    };
    return [
      ...items.slice(0, insertIndex - 1),
      mergedItem,
      ...items.slice(insertIndex),
    ];
  }

  return [...items.slice(0, insertIndex), item, ...items.slice(insertIndex)];
}

function pendingSessionEventInsertIndex(
  items: readonly TranscriptDisplayItem[],
  pendingEvent: PendingTranscriptSessionEvent,
): number {
  const anchorKey = pendingEvent.insertAfterMessageKey;

  if (anchorKey === null) {
    return leadingSessionEventCount(items);
  }
  if (typeof anchorKey !== "string" || !anchorKey.trim()) {
    return items.length;
  }

  const anchoredIndex = items.findIndex(item =>
    displayItemContainsMessageKey(item, anchorKey),
  );
  return anchoredIndex >= 0 ? anchoredIndex + 1 : items.length;
}

function leadingSessionEventCount(
  items: readonly TranscriptDisplayItem[],
): number {
  let index = 0;
  while (items[index]?.kind === "session_event") {
    index += 1;
  }
  return index;
}

function displayItemContainsMessageKey(
  item: TranscriptDisplayItem,
  messageKey: string,
): boolean {
  if (item.kind === "session_event") {
    return item.sourceMessageIds.includes(messageKey);
  }
  return transcriptMessageKey(item.message, item.messageIndex) === messageKey;
}

function displayStateBeforeIndex(
  items: readonly TranscriptDisplayItem[],
  index: number,
): TranscriptDisplayState {
  const state: TranscriptDisplayState = { hasSeenNonConfigMessage: false };

  for (let itemIndex = 0; itemIndex < index; itemIndex++) {
    const item = items[itemIndex];
    if (!item) continue;

    if (item.kind === "message") {
      state.hasSeenNonConfigMessage = true;
      continue;
    }
    if (item.model) {
      state.model = item.model;
    }
    if (item.thinkingLevel) {
      state.thinkingLevel = item.thinkingLevel;
    }
  }

  return state;
}

function appendToolResultToPreviousAssistant(
  normalized: TranscriptEntryLike[],
  toolResultMessage: TranscriptEntryLike,
): boolean {
  for (let index = normalized.length - 1; index >= 0; index--) {
    const candidate = normalized[index];
    if (candidate.role !== "assistant") continue;

    const mergedContent = mergeToolResultIntoContent(
      candidate.content,
      toolResultMessage,
    );
    if (!mergedContent) continue;

    normalized[index] = {
      ...candidate,
      content: mergedContent,
    };
    return true;
  }

  return false;
}

function mergeToolResultIntoContent(
  content: RpcTranscriptContent | undefined,
  toolResultMessage: TranscriptEntryLike,
): TranscriptContentItem[] | null {
  if (!Array.isArray(content)) return null;

  const cloned = content.map(cloneContentItem);
  const targetIndex = findNextUnmatchedToolCallIndex(cloned);
  if (targetIndex === -1) return null;

  const toolResultBlock: TranscriptToolResultBlockWithSource = {
    type: "toolResult",
    text: messageContent(toolResultMessage),
    content: cloneToolResultContent(toolResultMessage.content),
    details: toolResultMessage.details,
    isError: toolResultMessage.isError,
    sourceMessageId:
      typeof toolResultMessage.id === "string" && toolResultMessage.id
        ? toolResultMessage.id
        : undefined,
  };

  cloned.splice(targetIndex + 1, 0, toolResultBlock);
  return cloned;
}

function findNextUnmatchedToolCallIndex(
  content: TranscriptContentItem[],
): number {
  const unmatchedToolCallIndexes: number[] = [];

  for (let index = 0; index < content.length; index++) {
    const block = content[index];
    if (typeof block === "string") continue;

    if (block.type === "toolCall") {
      unmatchedToolCallIndexes.push(index);
      continue;
    }
    if (block.type === "toolResult" && unmatchedToolCallIndexes.length > 0) {
      unmatchedToolCallIndexes.shift();
    }
  }

  return unmatchedToolCallIndexes[0] ?? -1;
}

function cloneMessage(message: TranscriptEntryLike): TranscriptEntryLike {
  return {
    ...message,
    content: cloneContent(message.content),
  };
}

function cloneContent(
  content: RpcTranscriptContent | undefined,
): RpcTranscriptContent | undefined {
  if (!Array.isArray(content)) return content;
  return content.map(cloneContentItem);
}

function cloneContentItem(block: TranscriptContentItem): TranscriptContentItem {
  if (typeof block === "string") return block;
  return { ...block };
}

function cloneToolResultContent(
  content: RpcTranscriptContent | undefined,
): RpcTranscriptToolResultBlock["content"] | undefined {
  if (!Array.isArray(content)) return undefined;

  const cloned: NonNullable<RpcTranscriptToolResultBlock["content"]> = [];
  for (const item of content) {
    if (typeof item === "string") {
      cloned.push(item);
      continue;
    }

    switch (item.type) {
      case "text":
      case "image":
      case "image_url":
        cloned.push({ ...item });
        break;
      default:
        break;
    }
  }

  return cloned;
}

function configSystemBlock(
  message: TranscriptEntryLike,
): ConfigSystemBlock | null {
  if (message.role !== "system" || !Array.isArray(message.content)) {
    return null;
  }
  if (message.content.length !== 1) return null;

  const [block] = message.content;
  if (typeof block !== "object" || block === null) return null;
  if (block.type !== "model_change" && block.type !== "thinking_level_change") {
    return null;
  }

  return block as ConfigSystemBlock;
}

function isHiddenTranscriptMessage(message: TranscriptEntryLike): boolean {
  if (message.role !== "system" || !Array.isArray(message.content)) {
    return false;
  }
  if (message.content.length === 0) return false;

  return message.content.every(
    block =>
      typeof block === "object" &&
      block !== null &&
      isSystemBlock(block) &&
      isHiddenSystemBlock(block),
  );
}

function sessionEventKey(
  messages: readonly TranscriptEntryLike[],
  startIndex: number,
  endIndex: number,
): string {
  const startKey = transcriptMessageKey(messages[startIndex], startIndex);
  const endKey = transcriptMessageKey(messages[endIndex], endIndex);
  return startKey === endKey
    ? `session-event:${startKey}`
    : `session-event:${startKey}-${endKey}`;
}

function transcriptMessageKey(
  message: TranscriptEntryLike | undefined,
  index: number,
): string {
  if (!message) return `message:${index}`;
  return message.transcriptKey ?? message.id ?? `message:${index}`;
}

function sessionEventLabel(
  hasSeenNonConfigMessage: boolean,
  hasModel: boolean,
  hasThinking: boolean,
): string {
  if (!hasSeenNonConfigMessage) return "Session configured";
  if (hasModel && hasThinking) return "Settings changed";
  if (hasModel) return "Model switched";
  if (hasThinking) return "Thinking changed";
  return "Settings changed";
}

function normalizeOptionalText(value: string | undefined): string | undefined {
  const trimmed = typeof value === "string" ? value.trim() : "";
  return trimmed || undefined;
}

function normalizeText(value: string | undefined, fallback: string): string {
  return normalizeOptionalText(value) ?? fallback;
}

function normalizePendingSessionEventModel(value: {
  provider?: string;
  id: string;
}): {
  provider?: string;
  id: string;
} {
  return {
    provider: normalizeOptionalText(value.provider),
    id: normalizeText(value.id, "Unknown model"),
  };
}

function sameTranscriptModel(
  left:
    | {
        provider?: string;
        id: string;
      }
    | undefined,
  right:
    | {
        provider?: string;
        id: string;
      }
    | undefined,
): boolean {
  if (!left || !right) return false;
  return left.id === right.id && left.provider === right.provider;
}

function isSystemBlock(
  block: RpcTranscriptContentBlock,
): block is RpcTranscriptSystemBlock {
  return (
    block.type === "compaction" ||
    block.type === "branch_summary" ||
    block.type === "model_change" ||
    block.type === "thinking_level_change" ||
    block.type === "session_info"
  );
}

function isHiddenSystemBlock(block: RpcTranscriptSystemBlock): boolean {
  return block.type === "session_info";
}

function contentItemText(block: TranscriptContentItem): string {
  if (typeof block === "string") return block;
  if (isSystemBlock(block)) {
    return isHiddenSystemBlock(block) ? "" : systemBlockText(block);
  }

  switch (block.type) {
    case "text":
      return block.text;
    case "toolResult":
      return toolResultText(block);
    default:
      return "";
  }
}

function toolResultBlockFromItem(
  block: TranscriptContentItem | undefined,
): TranscriptToolResultBlockWithSource | undefined {
  if (!block || typeof block === "string" || block.type !== "toolResult") {
    return undefined;
  }
  return block as TranscriptToolResultBlockWithSource;
}

function systemContentBlock(
  block: RpcTranscriptSystemBlock,
): SystemContentBlock {
  switch (block.type) {
    case "compaction": {
      const tokensBefore = Number.isFinite(block.tokensBefore)
        ? block.tokensBefore
        : null;
      return {
        kind: "system",
        systemType: "compaction",
        label: "Compaction",
        title: "Context compacted",
        body: block.summary.trim() ? block.summary.trim() : undefined,
        meta:
          tokensBefore === null ? undefined : formatTokenCount(tokensBefore),
      };
    }
    case "branch_summary":
      return {
        kind: "system",
        systemType: "branch_summary",
        label: "Branch Summary",
        title: "Branch summarized",
        body: block.summary.trim() ? block.summary.trim() : undefined,
      };
    case "model_change": {
      const provider = block.provider.trim()
        ? block.provider.trim()
        : undefined;
      const modelId = block.modelId.trim()
        ? block.modelId.trim()
        : "Unknown model";
      return {
        kind: "system",
        systemType: "model_change",
        label: "Model",
        title: modelId,
        meta: provider,
      };
    }
    case "thinking_level_change": {
      const level = block.thinkingLevel.trim()
        ? block.thinkingLevel.trim()
        : "Unknown";
      return {
        kind: "system",
        systemType: "thinking_level_change",
        label: "Thinking",
        title: level,
      };
    }
    case "session_info": {
      const name = block.name?.trim() ? block.name.trim() : "Untitled session";
      return {
        kind: "system",
        systemType: "session_info",
        label: "Session",
        title: name,
      };
    }
  }
}

function systemBlockText(block: RpcTranscriptSystemBlock): string {
  const contentBlock = systemContentBlock(block);
  return contentBlock.body
    ? `${contentBlock.title}\n${contentBlock.body}`
    : contentBlock.title;
}

function formatTokenCount(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M tokens`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k tokens`;
  return `${count} tokens`;
}

function toolResultText(block: RpcTranscriptToolResultBlock): string {
  if (Array.isArray(block.content)) {
    return block.content
      .map(item => {
        if (typeof item === "string") return item;
        return item.type === "text" ? item.text : "";
      })
      .filter(Boolean)
      .join("\n");
  }

  if (typeof block.text === "string") return block.text;
  return JSON.stringify(block, null, 2);
}

function toolResultBlocks(
  block: RpcTranscriptToolResultBlock,
): ToolResultBlock[] {
  if (Array.isArray(block.content)) {
    const blocks: ToolResultBlock[] = [];
    for (const item of block.content) {
      if (typeof item === "string") {
        blocks.push({ kind: "text", text: item });
        continue;
      }

      switch (item.type) {
        case "text":
          blocks.push({ kind: "text", text: item.text });
          continue;
        case "image":
        case "image_url": {
          const src = imageBlockSource(item);
          if (src) {
            blocks.push({
              kind: "image",
              src,
              alt: item.text ?? "Image attachment",
              mimeType: item.mimeType,
            });
          }
          continue;
        }
      }
    }

    if (blocks.length > 0) return blocks;
  }

  const text = toolResultText(block);
  return text ? [{ kind: "text", text }] : [];
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseToolArguments(
  args: RpcToolArguments | undefined,
): RpcToolArguments | undefined {
  if (typeof args !== "string") return args;
  const trimmed = args.trim();
  if (!trimmed) return "";
  try {
    const parsed = JSON.parse(trimmed);
    return isJsonObject(parsed) ? parsed : args;
  } catch {
    return args;
  }
}

function toolArgumentsText(args: RpcToolArguments | undefined): string {
  if (typeof args === "string") return args;
  return JSON.stringify(args ?? "", null, 2);
}

function toolStatusFromResult(
  resultText: string | undefined,
  resultBlocks: ToolResultBlock[] | undefined,
  isError: boolean | undefined,
): ToolBlockStatus {
  const hasText =
    typeof resultText === "string" && resultText.trim().length > 0;
  const hasBlocks = Array.isArray(resultBlocks) && resultBlocks.length > 0;
  if (!hasText && !hasBlocks) return "pending";
  return isError ? "error" : "success";
}

function imageBlockSource(block: TranscriptImageBlock): string | null {
  switch (block.type) {
    case "image":
      if (
        typeof block.data === "string" &&
        typeof block.mimeType === "string"
      ) {
        return `data:${block.mimeType};base64,${block.data}`;
      }
      return typeof block.url === "string" ? block.url : null;
    case "image_url":
      if (typeof block.url === "string") {
        return block.url;
      }
      if (typeof block.image_url === "string") {
        return block.image_url;
      }
      if (
        typeof block.image_url === "object" &&
        block.image_url !== null &&
        typeof block.image_url.url === "string"
      ) {
        return block.image_url.url;
      }
      return null;
  }
}
