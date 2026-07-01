import type {
  JsonObject,
  JsonValue,
  ToolBlockStatus,
  ToolContentBlock,
} from "./transcript";

export interface ToolInlineModel {
  label: string;
  title: string;
  meta?: string;
  diffStats?: { added: number; removed: number };
}

export interface ToolDetailModel {
  kind: "diff" | "code" | "bash" | "text" | "empty";
  text?: string;
  path?: string;
  command?: string;
  edits?: Array<{ oldText: string; newText: string }>;
}

export type ReadClassification = { kind: "skill"; label: string };

type ToolArgsRecord = JsonObject;

export function buildToolInlineModel(block: ToolContentBlock): ToolInlineModel {
  const args = asRecord(block.toolArgs);

  return {
    label: humanizeToolName(block.toolName),
    title: formatToolTitle(block.toolName, args),
    meta: formatToolMeta(
      block.toolName,
      args,
      block.resultDetails,
    ),
    diffStats: buildDiffStats(
      block.toolName,
      args,
      block.resultDetails,
      block.toolStatus,
    ),
  };
}

function formatToolTitle(
  toolName: string,
  args: ToolArgsRecord | undefined,
): string {
  switch (toolName) {
    case "read": {
      const path = stringValue(args, "path");
      if (!path) return humanizeToolName(toolName);
      const offset = numberValue(args, "offset");
      const limit = numberValue(args, "limit");
      if (offset === undefined && limit === undefined) return path;
      const startLine = offset ?? 1;
      const endLine = limit !== undefined ? startLine + limit - 1 : undefined;
      return endLine !== undefined
        ? `${path}:${startLine}-${endLine}`
        : `${path}:${startLine}`;
    }
    case "bash": {
      const command = stringValue(args, "command");
      if (!command) return humanizeToolName(toolName);
      const firstLine = command.replace(/\r/g, "").split("\n")[0]!;
      const totalLines = command.replace(/\r/g, "").split("\n").length;
      const suffix =
        totalLines > 1
          ? ` (+${totalLines - 1} more line${totalLines - 1 > 1 ? "s" : ""})`
          : "";
      return firstLine + suffix;
    }
    case "curl":
      return formatCurlArgs(args) || humanizeToolName(toolName);
    case "edit": {
      const path = stringValue(args, "path");
      return path || humanizeToolName(toolName);
    }
    case "write": {
      const path = stringValue(args, "path");
      return path || humanizeToolName(toolName);
    }
    default:
      return humanizeToolName(toolName);
  }
}

function formatToolMeta(
  toolName: string,
  args: ToolArgsRecord | undefined,
  resultDetails: JsonValue | undefined,
): string | undefined {
  switch (toolName) {
    case "bash": {
      const parts: string[] = [];
      const timeout = numberValue(args, "timeout");
      if (timeout !== undefined) parts.push(`timeout ${timeout}s`);
      return parts.join(" · ") || undefined;
    }
    case "curl":
      return undefined;
    case "edit":
      return undefined;
    case "write": {
      const content = stringValue(args, "content");
      if (!content) return undefined;
      const lines = content.replace(/\r/g, "").split("\n").length;
      return `${lines} line${lines === 1 ? "" : "s"}`;
    }
    default:
      return undefined;
  }
}

export function buildToolDetailModel(block: ToolContentBlock): ToolDetailModel {
  const args = asRecord(block.toolArgs);
  const path = stringValue(args, "path");
  const command =
    block.toolName === "bash"
      ? formatBashCommand(stringValue(args, "command"))
      : block.toolName === "curl"
        ? formatCurlCommand(args)
      : undefined;
  const diff = blockResultDiff(block.resultDetails)?.replace(/\r/g, "").trim();
  if (block.toolName === "edit") {
    const edits = editPairs(args);
    if (diff || edits.length > 0) {
      return { kind: "diff", text: diff, path, edits };
    }
  }

  if (block.toolName === "write") {
    const content = stringValue(args, "content");
    if (typeof content === "string") {
      return content.length > 0
        ? { kind: "code", text: content.replace(/\r/g, ""), path }
        : { kind: "empty", path };
    }
  }

  const text =
    toolResultText(block) ||
    (block.toolName === "curl"
      ? stringValue(asRecord(block.resultDetails), "stderr")?.trim() ?? ""
      : "");
  if (!text) {
    if ((block.toolName === "bash" || block.toolName === "curl") && command) {
      return { kind: "bash", path, command };
    }
    return { kind: "empty", path };
  }
  if (block.toolName === "read") return { kind: "code", text, path };
  if (block.toolName === "bash" || block.toolName === "curl") {
    return { kind: "bash", text, path, command };
  }
  return { kind: "text", text, path };
}

export function classifyReadToolBlock(
  block: ToolContentBlock,
): ReadClassification | null {
  if (block.toolName !== "read") return null;
  const args = asRecord(block.toolArgs);
  const rawPath = stringValue(args, "file_path") ?? stringValue(args, "path");
  if (!rawPath) return null;
  const normalized = rawPath.replace(/\\/g, "/");
  const segments = normalized.split("/");
  const fileName = segments.at(-1) ?? "";
  if (fileName !== "SKILL.md") return null;
  const fallback = segments.at(-2) ?? fileName;
  return {
    kind: "skill",
    label: skillNameFromText(toolResultText(block)) ?? fallback,
  };
}

function skillNameFromText(text: string): string | undefined {
  const match = text.match(/^---\n([\s\S]*?)\n---(?:\n|$)/);
  if (!match) return undefined;
  for (const line of match[1].split("\n")) {
    const name = line.match(/^\s*name\s*:\s*(.+?)\s*$/)?.[1];
    if (!name) continue;
    const unquoted = name.match(/^(['"])(.*)\1$/)?.[2] ?? name;
    const trimmed = unquoted.trim();
    if (trimmed) return trimmed;
  }
  return undefined;
}

function humanizeToolName(toolName: string): string {
  if (!toolName) return "Tool";
  return toolName
    .split(/[_-]+/)
    .filter(Boolean)
    .map(part => part[0]!.toUpperCase() + part.slice(1))
    .join(" ");
}

function asRecord(value: JsonValue | undefined): ToolArgsRecord | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value;
}

function stringValue(
  args: ToolArgsRecord | undefined,
  key: string,
): string | undefined {
  const value = args?.[key];
  return typeof value === "string" ? value : undefined;
}

function numberValue(
  args: ToolArgsRecord | undefined,
  key: string,
): number | undefined {
  const value = args?.[key];
  return typeof value === "number" ? value : undefined;
}

function editDiffStats(
  args: ToolArgsRecord | undefined,
  diffText: string | undefined,
): { added: number; removed: number } | undefined {
  const fromDiff = diffStatsFromDiff(diffText);
  if (fromDiff) return fromDiff;
  const edits = arrayValue(args, "edits");
  if (!edits) return undefined;
  let added = 0;
  let removed = 0;
  let sawEdit = false;

  for (const edit of edits) {
    const record = asRecord(edit);
    if (!record) continue;
    const oldText = typeof record.oldText === "string" ? record.oldText : "";
    const newText = typeof record.newText === "string" ? record.newText : "";
    removed += countLines(oldText);
    added += countLines(newText);
    sawEdit = true;
  }

  return sawEdit ? { added, removed } : undefined;
}

function editPairs(
  args: ToolArgsRecord | undefined,
): Array<{ oldText: string; newText: string }> {
  const edits = arrayValue(args, "edits");
  if (!edits) return [];
  const pairs: Array<{ oldText: string; newText: string }> = [];

  for (const edit of edits) {
    const record = asRecord(edit);
    if (!record) continue;
    pairs.push({
      oldText: typeof record.oldText === "string" ? record.oldText : "",
      newText: typeof record.newText === "string" ? record.newText : "",
    });
  }

  return pairs;
}

function countLines(text: string): number {
  if (!text) return 0;
  const lines = text.replace(/\r/g, "").split("\n");
  if (lines.at(-1) === "") lines.pop();
  return lines.length;
}

function arrayValue(
  args: ToolArgsRecord | undefined,
  key: string,
): JsonValue[] | undefined {
  const value = args?.[key];
  return Array.isArray(value) ? value : undefined;
}

function formatBashCommand(command: string | undefined): string | undefined {
  if (!command) return undefined;
  const normalized = command.replace(/\r/g, "");
  if (!normalized.trim()) return undefined;
  return normalized
    .split("\n")
    .map(line => `$ ${line}`)
    .join("\n");
}

function formatCurlArgs(args: ToolArgsRecord | undefined): string | undefined {
  const values = arrayValue(args, "args")?.filter(
    (value): value is string => typeof value === "string",
  );
  if (!values?.length) return undefined;
  return values.map(formatCommandArg).join(" ");
}

function formatCurlCommand(args: ToolArgsRecord | undefined): string | undefined {
  const formattedArgs = formatCurlArgs(args);
  return formattedArgs ? `$ curl ${formattedArgs}` : undefined;
}

function formatCommandArg(value: string): string {
  if (/^[A-Za-z0-9_@%+=:,./?-]+$/.test(value)) return value;
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function blockResultDiff(
  resultDetails: JsonValue | undefined,
): string | undefined {
  return findDiffString(resultDetails, 0);
}

function findDiffString(
  value: JsonValue | undefined,
  depth: number,
): string | undefined {
  if (depth > 3 || value === undefined || value === null) return undefined;

  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return undefined;

    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        return findDiffString(JSON.parse(trimmed) as JsonValue, depth + 1);
      } catch {
        // Fall through and treat the string itself as a possible diff.
      }
    }

    return looksLikeDiffString(trimmed) ? trimmed : undefined;
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      const diff = findDiffString(item, depth + 1);
      if (diff) return diff;
    }
    return undefined;
  }

  const details = asRecord(value);
  if (!details) return undefined;

  for (const key of ["diff", "patch", "unifiedDiff"]) {
    const diff = findDiffString(details[key], depth + 1);
    if (diff) return diff;
  }

  for (const key of ["details", "result", "data"]) {
    const diff = findDiffString(details[key], depth + 1);
    if (diff) return diff;
  }

  return undefined;
}

function looksLikeDiffString(value: string): boolean {
  return (
    value.startsWith("--- ") ||
    value.startsWith("+++ ") ||
    value.startsWith("@@ ") ||
    value.includes("\n@@ ") ||
    /^[ +-]\s*\d+\s/m.test(value)
  );
}

function toolResultText(block: ToolContentBlock): string {
  const text = (block.resultBlocks ?? [])
    .flatMap(item => (item.kind === "text" ? [item.text] : []))
    .join("\n")
    .replace(/\r/g, "")
    .trim();
  if (text) return text;
  return block.resultText?.replace(/\r/g, "").trim() ?? "";
}

function diffStatsFromDiff(
  diffText: string | undefined,
): { added: number; removed: number } | undefined {
  if (!diffText) return undefined;
  let added = 0;
  let removed = 0;

  for (const line of diffText.replace(/\r/g, "").split("\n")) {
    if (
      line.startsWith("+++") ||
      line.startsWith("---") ||
      line.startsWith("@@")
    ) {
      continue;
    }
    if (line.startsWith("+")) added += 1;
    if (line.startsWith("-")) removed += 1;
  }

  if (added === 0 && removed === 0) return undefined;
  return { added, removed };
}

function buildDiffStats(
  toolName: string,
  args: ToolArgsRecord | undefined,
  resultDetails: JsonValue | undefined,
  status: ToolBlockStatus,
): { added: number; removed: number } | undefined {
  if (toolName !== "edit" || status !== "success") return undefined;
  return editDiffStats(args, blockResultDiff(resultDetails));
}
