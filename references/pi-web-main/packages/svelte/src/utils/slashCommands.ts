import type { RpcSlashCommand } from "@pi-web/bridge/types";

export interface SlashCommandOption {
  name: string;
  description?: string;
}

export interface SlashCommandContext {
  query: string;
  start: number;
  end: number;
}

const BUILTIN_SLASH_COMMANDS: SlashCommandOption[] = [
  {
    name: "compact",
    description: "Compact context now, optionally with custom instructions",
  },
];

const DEBUG_SLASH_COMMANDS: SlashCommandOption[] = [
  {
    name: "fixture",
    description:
      "Insert a local debug fixture like markdown, edit, bash, or mixed",
  },
  {
    name: "tps",
    description: "Set local debug streaming speed in tokens per second",
  },
  {
    name: "assistant",
    description:
      "Append a synthetic assistant message without sending an LLM request",
  },
  {
    name: "user",
    description: "Append a synthetic user message to the debug transcript",
  },
  {
    name: "json",
    description: "Append raw transcript JSON or content block JSON",
  },
  {
    name: "name",
    description: "Rename the current in-memory debug session",
  },
  {
    name: "clear",
    description: "Reset the debug transcript back to its intro message",
  },
];

export function slashCommandOptionsFromRpc(
  commands: readonly Pick<RpcSlashCommand, "name" | "description">[],
): SlashCommandOption[] {
  return commands
    .filter(command => typeof command.name === "string" && command.name.trim())
    .map(command => ({
      name: command.name,
      description: command.description,
    }));
}

export function debugSlashCommandOptions(): SlashCommandOption[] {
  return DEBUG_SLASH_COMMANDS.map(command => ({ ...command }));
}

export function mergeSlashCommandOptions(
  commands: readonly SlashCommandOption[],
  builtins: readonly SlashCommandOption[] = BUILTIN_SLASH_COMMANDS,
): SlashCommandOption[] {
  const merged = commands.map(command => ({ ...command }));
  const seen = new Set(merged.map(command => command.name.toLowerCase()));

  for (const command of builtins) {
    const key = command.name.toLowerCase();
    if (seen.has(key)) continue;
    merged.push({ ...command });
    seen.add(key);
  }

  return merged;
}

export function getSlashCommandContext(
  text: string,
  cursor: number,
): SlashCommandContext | null {
  const safeCursor = Math.max(0, Math.min(cursor, text.length));
  const prefix = text.slice(0, safeCursor);
  const match = prefix.match(/^\s*\/([^\s\n\r]*)$/);

  if (!match) return null;

  return {
    query: match[1] ?? "",
    start: prefix.lastIndexOf("/"),
    end: safeCursor,
  };
}

export function applySlashCommandCompletion(
  text: string,
  context: SlashCommandContext,
  command: SlashCommandOption,
): { text: string; cursor: number } {
  const suffix = text.slice(context.end);
  const needsSeparator = suffix.length === 0 || !/^\s/.test(suffix);
  const replacement = `/${command.name}${needsSeparator ? " " : ""}`;
  const nextText =
    text.slice(0, context.start) + replacement + text.slice(context.end);

  return {
    text: nextText,
    cursor: context.start + replacement.length,
  };
}

export function parseCompactSlashCommand(text: string): {
  customInstructions?: string;
} | null {
  const normalized = text.trim();
  const match = normalized.match(/^\/([^\s]+)(?:\s+([\s\S]*))?$/);

  if (!match) return null;
  if (match[1]?.toLowerCase() !== "compact") return null;

  const customInstructions = match[2]?.trim();
  if (!customInstructions) return {};
  return { customInstructions };
}
