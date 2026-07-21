import { t } from "../i18n";
import type { ImageContentBlock, ToolContentBlock } from "./transcript";
import { classifyReadToolBlock } from "./toolBlock";

export type ToolActivityKind =
  | "read"
  | "update"
  | "external"
  | "process"
  | "question"
  | "skill"
  | "generic";

export interface ToolActivitySource {
  key: string;
  block: ToolContentBlock;
}

export interface ToolActivity {
  key: string;
  sourceKeys: string[];
  kind: ToolActivityKind;
  status: ToolContentBlock["toolStatus"];
  count: number;
  label: string;
  skillName?: string;
  details: string[];
  rawDetails: string[];
  overflowCount: number;
  images: ImageContentBlock[];
}

const MAX_ACTIVITY_DETAILS = 5;

export function buildSkillActivity(
  key: string,
  skillName: string,
  status: ToolContentBlock["toolStatus"] = "success",
): ToolActivity {
  const safeSkillName = containsHan(skillName) ? skillName.trim() : undefined;
  return {
    key,
    sourceKeys: [key],
    kind: "skill",
    status,
    count: 1,
    label: toolActivityLabel("skill", status, 1, safeSkillName),
    ...(safeSkillName ? { skillName: safeSkillName } : {}),
    details: [],
    rawDetails: [],
    overflowCount: 0,
    images: [],
  };
}

export function buildToolActivities(
  sources: readonly ToolActivitySource[],
): ToolActivity[] {
  const activities: ToolActivity[] = [];
  const visibleSources = sources.filter((source, index) =>
    !isRecoveredFailure(source, index, sources)
  );

  for (const source of visibleSources) {
    const descriptor = toolActivityDescriptor(source.block);
    const { kind, skillName } = descriptor;
    const previous = activities.at(-1);
    if (
      previous &&
      previous.kind === kind &&
      kind !== "skill" &&
      previous.status !== "error" &&
      source.block.toolStatus !== "error"
    ) {
      previous.sourceKeys.push(source.key);
      previous.count += 1;
      previous.details = uniqueStrings([
        ...previous.details,
        ...safeToolActivityDetails(source.block),
      ]);
      previous.images = uniqueImages([
        ...previous.images,
        ...safeToolActivityImages(source.block),
      ]);
      if (source.block.toolStatus === "pending") previous.status = "pending";
      previous.label = toolActivityLabel(
        previous.kind,
        previous.status,
        previous.count,
        previous.skillName,
      );
      continue;
    }

    activities.push({
      key: source.key,
      sourceKeys: [source.key],
      kind,
      status: source.block.toolStatus,
      count: 1,
      label: toolActivityLabel(
        kind,
        source.block.toolStatus,
        1,
        skillName,
      ),
      ...(skillName ? { skillName } : {}),
      details: source.block.toolStatus === "error" || kind === "skill"
        ? []
        : safeToolActivityDetails(source.block),
      rawDetails: source.block.toolStatus === "error"
        ? rawToolFailureDetails(source.block)
        : [],
      overflowCount: 0,
      images: safeToolActivityImages(source.block),
    });
  }

  return activities.map(activity => ({
    ...activity,
    details: activity.details.slice(0, MAX_ACTIVITY_DETAILS),
    overflowCount: Math.max(0, activity.details.length - MAX_ACTIVITY_DETAILS),
  }));
}

function isRecoveredFailure(
  source: ToolActivitySource,
  index: number,
  sources: readonly ToolActivitySource[],
): boolean {
  if (source.block.toolStatus !== "error") return false;
  const identity = toolRetryIdentity(source.block);
  if (!identity) return false;
  return sources.slice(index + 1).some(candidate =>
    candidate.block.toolStatus === "success" &&
    toolRetryIdentity(candidate.block) === identity
  );
}

function toolRetryIdentity(block: ToolContentBlock): string | undefined {
  const args = asRecord(block.toolArgs);
  let target: string | undefined;
  switch (block.toolName) {
    case "read":
    case "edit":
    case "write":
      target = stringField(args, "path") ?? stringField(args, "file_path");
      break;
    case "bash":
      target = stringField(args, "command");
      break;
    case "curl":
      target = stableJson(block.toolArgs);
      break;
    default:
      target = stableJson(block.toolArgs);
      break;
  }
  return target ? `${block.toolName.trim()}:${target}` : undefined;
}

function stableJson(value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (Array.isArray(value)) {
    return `[${value.map(item => stableJson(item) ?? "null").join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item) ?? "null"}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value);
}

function safeToolActivityDetails(block: ToolContentBlock): string[] {
  if (block.toolName === "read" || block.toolName === "edit" || block.toolName === "write") {
    const args = asRecord(block.toolArgs);
    const path = stringField(args, "path") ?? stringField(args, "file_path");
    const name = path ? safeBaseName(path) : "";
    return name ? [name] : [];
  }

  if (block.toolName === "curl") {
    const args = asRecord(block.toolArgs);
    const candidates = [
      ...stringValues(args?.args),
      ...stringValues(args?.url),
    ];
    return uniqueStrings(candidates.flatMap(safeUrlHostname));
  }

  return [];
}

function rawToolFailureDetails(block: ToolContentBlock): string[] {
  const details: string[] = [];
  if (typeof block.resultText === "string" && block.resultText.length > 0) {
    details.push(block.resultText);
  }
  if (hasContent(block.resultDetails)) {
    details.push(
      typeof block.resultDetails === "string"
        ? block.resultDetails
        : JSON.stringify(block.resultDetails, null, 2),
    );
  }
  return details;
}

function hasContent(value: unknown): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === "string" || Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function safeToolActivityImages(block: ToolContentBlock): ImageContentBlock[] {
  if (!(["read", "edit", "write", "curl", "bash"] as const).includes(
    block.toolName as "read" | "edit" | "write" | "curl" | "bash",
  )) return [];
  return (block.resultBlocks ?? [])
    .filter((result): result is ImageContentBlock => result.kind === "image")
    .map(image => ({
      ...image,
      alt: t("transcript.imageAttachmentAlt"),
    }));
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function stringField(
  value: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const field = value?.[key];
  return typeof field === "string" && field.trim() ? field.trim() : undefined;
}

function stringValues(value: unknown): string[] {
  if (typeof value === "string") return value.trim() ? [value.trim()] : [];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function safeBaseName(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized.split("/").at(-1)?.trim() ?? "";
}

function safeUrlHostname(value: string): string[] {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:"
      ? [url.hostname]
      : [];
  } catch {
    return [];
  }
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function uniqueImages(images: readonly ImageContentBlock[]): ImageContentBlock[] {
  const seen = new Set<string>();
  return images.filter(image => {
    if (seen.has(image.src)) return false;
    seen.add(image.src);
    return true;
  });
}

function toolActivityDescriptor(block: ToolContentBlock): {
  kind: ToolActivityKind;
  skillName?: string;
} {
  const skill = classifyReadToolBlock(block);
  if (skill) {
    const frontmatterName = skillFrontmatterName(block.resultText ?? "");
    return frontmatterName && containsHan(frontmatterName)
      ? { kind: "skill", skillName: frontmatterName }
      : { kind: "skill" };
  }

  switch (block.toolName.trim()) {
    case "read":
      return { kind: "read" };
    case "edit":
    case "write":
      return { kind: "update" };
    case "curl":
      return { kind: "external" };
    case "bash":
      return { kind: "process" };
    case "ask_user_question":
      return { kind: "question" };
    default:
      return { kind: "generic" };
  }
}

function skillFrontmatterName(text: string): string | undefined {
  const frontmatter = text.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)?.[1];
  if (!frontmatter) return undefined;
  for (const line of frontmatter.split(/\r?\n/)) {
    const value = line.match(/^\s*name\s*:\s*(.+?)\s*$/)?.[1];
    if (!value) continue;
    return (value.match(/^(['"])(.*)\1$/)?.[2] ?? value).trim() || undefined;
  }
  return undefined;
}

function containsHan(value: string): boolean {
  return /\p{Script=Han}/u.test(value);
}

export function toolActivityLabel(
  kind: ToolActivityKind,
  status: ToolContentBlock["toolStatus"],
  count: number,
  skillName?: string,
): string {
  const safeCount = Number.isSafeInteger(count) && count > 0 ? count : 1;
  const phase = status === "pending"
    ? "pending"
    : status === "error"
      ? "error"
      : "success";
  const action = kind === "skill"
    ? skillName
      ? t(`chatTranscript.activity.skill.${phase}Named`, { name: skillName })
      : t(`chatTranscript.activity.skill.${phase}`)
    : t(
        `chatTranscript.activity.${kind}.${phase}${safeCount > 1 ? "Count" : ""}`,
        { count: safeCount },
      );
  return action;
}
