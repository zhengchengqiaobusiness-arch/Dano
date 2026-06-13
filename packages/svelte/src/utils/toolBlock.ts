import type {
  ChatContentBlock,
  JsonObject,
  JsonValue,
  ToolBlockStatus,
} from "../composables/bridgeStore.svelte";

export type ToolContentBlock = Extract<ChatContentBlock, { kind: "tool" }>;

export interface ToolInlineModel {
  variant: "tool" | "skill";
  label: string;
  title: string;
  meta?: string;
  diffStats?: { added: number; removed: number };
}

export type ToolDetailModel =
  | {
      kind: "diff";
      text?: string;
      path?: string;
      edits?: Array<{ oldText: string; newText: string }>;
    }
  | { kind: "code"; text: string; path?: string }
  | { kind: "bash"; text?: string; path?: string; command?: string }
  | { kind: "text"; text: string; path?: string }
  | { kind: "empty"; path?: string };

export function buildToolInlineModel(
  toolBlock: ToolContentBlock,
): ToolInlineModel {
  const args = asRecord(toolBlock.toolArgs);
  const skillInvocation = toolInlineSkillInvocation(toolBlock.toolName, args);

  return {
    variant: skillInvocation ? "skill" : "tool",
    label: skillInvocation ? "使用技能" : humanizeToolName(toolBlock.toolName),
    title: skillInvocation?.name ?? formatToolTitle(toolBlock.toolName, args),
    meta:
      skillInvocation?.meta ??
      formatToolMeta(
        toolBlock.toolName,
        args,
        toolBlock.resultText,
        toolBlock.toolStatus,
      ) ??
      toolStatusMeta(toolBlock.toolStatus),
    diffStats: buildDiffStats(
      toolBlock.toolName,
      args,
      toolBlock.resultDetails,
      toolBlock.toolStatus,
    ),
  };
}

export function buildToolDetailModel(
  toolBlock: ToolContentBlock,
): ToolDetailModel {
  const args = asRecord(toolBlock.toolArgs);
  const path = stringValue(args, "path");
  const command =
    toolBlock.toolName === "bash"
      ? formatBashCommand(stringValue(args, "command"))
      : undefined;
  const diff = blockResultDiff(toolBlock.resultDetails)?.replace(/\r/g, "").trim();

  if (toolBlock.toolName === "edit") {
    const edits = editPairs(args);
    if (diff || edits.length > 0) {
      return { kind: "diff", text: diff, path, edits };
    }
  }

  if (toolBlock.toolName === "write") {
    const content = stringValue(args, "content");
    if (typeof content === "string") {
      return content.length > 0
        ? { kind: "code", text: content.replace(/\r/g, ""), path }
        : { kind: "empty", path };
    }
  }

  const text = toolResultText(toolBlock);
  if (!text) {
    if (toolBlock.toolName === "bash" && command) {
      return { kind: "bash", path, command };
    }
    return { kind: "empty", path };
  }
  if (toolBlock.toolName === "read") return { kind: "code", text, path };
  if (toolBlock.toolName === "bash") return { kind: "bash", text, path, command };
  return { kind: "text", text, path };
}

export function toolStatusMeta(
  status: ToolBlockStatus,
): string | undefined {
  if (status === "pending") return "运行中";
  if (status === "error") return "调用失败";
  return undefined;
}

export function isSkillToolName(toolName: string): boolean {
  const normalized = toolName.toLowerCase().replace(/[-_\s]+/g, "_");
  return (
    normalized === "skill" ||
    normalized === "invoke_skill" ||
    normalized === "run_skill" ||
    normalized === "load_skill" ||
    normalized === "read_skill"
  );
}

export function humanizeToolName(toolName: string): string {
  if (!toolName) return "Tool";
  if (isSkillToolName(toolName)) return "Skill";
  return toolName
    .split(/[_-]+/)
    .filter(Boolean)
    .map(part => part[0]!.toUpperCase() + part.slice(1))
    .join(" ");
}

export function detailText(detail: ToolDetailModel): string {
  if (detail.kind === "diff") {
    return detail.text || diffFromEdits(detail.edits ?? []);
  }
  return "text" in detail ? detail.text ?? "" : "";
}

function formatToolTitle(
  toolName: string,
  args: JsonObject | undefined,
): string {
  if (isSkillToolName(toolName)) {
    return (
      stringValue(args, "skillName") ||
      stringValue(args, "skill") ||
      stringValue(args, "name") ||
      humanizeToolName(toolName)
    );
  }

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
      const lines = command.replace(/\r/g, "").split("\n");
      const suffix =
        lines.length > 1
          ? ` (+${lines.length - 1} more line${lines.length - 1 > 1 ? "s" : ""})`
          : "";
      return `${lines[0]}${suffix}`;
    }
    case "edit":
    case "write": {
      return stringValue(args, "path") || humanizeToolName(toolName);
    }
    default:
      return humanizeToolName(toolName);
  }
}

function formatToolMeta(
  toolName: string,
  args: JsonObject | undefined,
  resultText: string | undefined,
  status: ToolBlockStatus,
): string | undefined {
  if (isSkillToolName(toolName)) return status === "success" ? undefined : toolStatusMeta(status);

  switch (toolName) {
    case "bash": {
      const parts: string[] = [];
      const exitCode = bashExitCode(resultText, status);
      if (exitCode !== undefined) parts.push(`exit ${exitCode}`);
      const timeout = numberValue(args, "timeout");
      if (timeout !== undefined) parts.push(`timeout ${timeout}s`);
      return parts.join(" · ") || undefined;
    }
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

function toolInlineSkillInvocation(
  toolName: string,
  args: JsonObject | undefined,
): { name: string; meta?: string } | undefined {
  if (isSkillToolName(toolName)) {
    return {
      name:
        stringValue(args, "skillName") ||
        stringValue(args, "skill") ||
        stringValue(args, "name") ||
        humanizeToolName(toolName),
    };
  }

  if (toolName !== "read") return undefined;
  const skillName = skillNameFromSkillPath(stringValue(args, "path"));
  if (!skillName) return undefined;
  return { name: skillName };
}

function skillNameFromSkillPath(path: string | undefined): string | undefined {
  if (!path) return undefined;
  const normalizedPath = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const segments = normalizedPath.split("/").filter(Boolean);
  if (segments.at(-1)?.toLowerCase() !== "skill.md") return undefined;
  return segments.at(-2);
}

function asRecord(value: JsonValue | undefined): JsonObject | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value;
}

function stringValue(args: JsonObject | undefined, key: string): string | undefined {
  const value = args?.[key];
  return typeof value === "string" ? value : undefined;
}

function numberValue(args: JsonObject | undefined, key: string): number | undefined {
  const value = args?.[key];
  return typeof value === "number" ? value : undefined;
}

function arrayValue(args: JsonObject | undefined, key: string): JsonValue[] | undefined {
  const value = args?.[key];
  return Array.isArray(value) ? value : undefined;
}

function bashExitCode(
  resultText: string | undefined,
  status: ToolBlockStatus,
): number | undefined {
  if (!resultText) return status === "success" ? 0 : undefined;
  const match = resultText.match(/Command exited with code (\d+)/i);
  if (match) return Number(match[1]);
  return status === "success" ? 0 : undefined;
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

function toolResultText(toolBlock: ToolContentBlock): string {
  return toolBlock.resultText?.replace(/\r/g, "").trim() ?? "";
}

function blockResultDiff(resultDetails: JsonValue | undefined): string | undefined {
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

  if (typeof value !== "object") return undefined;
  for (const key of ["diff", "patch", "unifiedDiff"]) {
    const diff = findDiffString(value[key], depth + 1);
    if (diff) return diff;
  }
  for (const key of ["details", "result", "data"]) {
    const diff = findDiffString(value[key], depth + 1);
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

function buildDiffStats(
  toolName: string,
  args: JsonObject | undefined,
  resultDetails: JsonValue | undefined,
  status: ToolBlockStatus,
): { added: number; removed: number } | undefined {
  if (toolName !== "edit" || status !== "success") return undefined;
  return editDiffStats(args, blockResultDiff(resultDetails));
}

function editDiffStats(
  args: JsonObject | undefined,
  diffText: string | undefined,
): { added: number; removed: number } | undefined {
  const fromDiff = diffStatsFromDiff(diffText);
  if (fromDiff) return fromDiff;

  const edits = editPairs(args);
  if (edits.length === 0) return undefined;

  return edits.reduce(
    (stats, edit) => ({
      added: stats.added + countLines(edit.newText),
      removed: stats.removed + countLines(edit.oldText),
    }),
    { added: 0, removed: 0 },
  );
}

function editPairs(args: JsonObject | undefined): Array<{ oldText: string; newText: string }> {
  const edits = arrayValue(args, "edits");
  if (!edits) return [];

  return edits.flatMap(edit => {
    const record = asRecord(edit);
    if (!record) return [];
    return [
      {
        oldText: typeof record.oldText === "string" ? record.oldText : "",
        newText: typeof record.newText === "string" ? record.newText : "",
      },
    ];
  });
}

function diffFromEdits(edits: Array<{ oldText: string; newText: string }>): string {
  return edits
    .map((edit, index) => {
      const removed = edit.oldText
        ? edit.oldText.replace(/\r/g, "").split("\n").map(line => `- ${line}`)
        : [];
      const added = edit.newText
        ? edit.newText.replace(/\r/g, "").split("\n").map(line => `+ ${line}`)
        : [];
      return [`@@ edit ${index + 1} @@`, ...removed, ...added].join("\n");
    })
    .join("\n");
}

function countLines(text: string): number {
  if (!text) return 0;
  const lines = text.replace(/\r/g, "").split("\n");
  if (lines.at(-1) === "") lines.pop();
  return lines.length;
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

  return added || removed ? { added, removed } : undefined;
}
