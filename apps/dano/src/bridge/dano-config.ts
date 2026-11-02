import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { BridgeQuickActionConfig, BridgeShortcutLinkConfig } from "./types.js";

const DANO_CONFIG_FILE_NAME = "dano.config.json";

export interface DanoConfig {
  /** Server-only. Deliberately absent from the browser projection. */
  openVikingSettings?: Record<string, unknown>;
  productName?: string;
  fieldAssist?: {
    maxRetries?: number;
  };
  askUserQuestion?: {
    maxRetries?: number;
    defaultTitle?: string;
  };
  slashCommandsAndMentionsEnabled?: boolean;
  transcriptProcessSummaryEnabled?: boolean;
  quickActions?: BridgeQuickActionConfig[];
  shortcutLinks?: BridgeShortcutLinkConfig[];
}

export const DANO_DEFAULT_CONFIG = {
  fieldAssist: {
    maxRetries: 10,
  },
  askUserQuestion: {
    maxRetries: 10,
    defaultTitle: "表单",
  },
  slashCommandsAndMentionsEnabled: false,
  transcriptProcessSummaryEnabled: false,
  quickActions: [],
  shortcutLinks: [],
} satisfies Required<Omit<DanoConfig, "productName" | "openVikingSettings">>;

export interface LoadDanoConfigOptions {
  cwd?: string;
  env?: Record<string, string | undefined>;
  startDir?: string;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readNonNegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : undefined;
}

function readBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function readSlashCommandsAndMentionsEnabled(
  env: Record<string, string | undefined>,
  configured: boolean | undefined,
): boolean {
  const raw = env.DANO_SLASH_COMMANDS_AND_MENTIONS_ENABLED?.trim();
  if (!raw) return configured ?? false;

  switch (raw.toLowerCase()) {
    case "true":
    case "1":
      return true;
    case "false":
    case "0":
      return false;
    default:
      throw new Error(
        `DANO_SLASH_COMMANDS_AND_MENTIONS_ENABLED has invalid value "${raw}"`,
      );
  }
}

function readRetryOptions(value: unknown): { maxRetries?: number } | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const maxRetries = readNonNegativeInteger(
    (value as Record<string, unknown>).maxRetries,
  );
  return maxRetries === undefined ? undefined : { maxRetries };
}

function readAskUserQuestionOptions(
  value: unknown,
): DanoConfig["askUserQuestion"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  const maxRetries = readNonNegativeInteger(record.maxRetries);
  const defaultTitle = readString(record.defaultTitle);
  return maxRetries === undefined && defaultTitle === undefined
    ? undefined
    : {
        ...(maxRetries !== undefined ? { maxRetries } : {}),
        ...(defaultTitle ? { defaultTitle } : {}),
      };
}

function readQuickActions(value: unknown): BridgeQuickActionConfig[] | undefined {
  if (!Array.isArray(value)) return undefined;

  const actions = value.flatMap(item => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const record = item as Record<string, unknown>;
    const label = readString(record.label);
    const prompt = readString(record.prompt);
    return label && prompt ? [{ label, prompt }] : [];
  });

  return actions.length > 0 ? actions : undefined;
}

export function readShortcutLinks(value: unknown): BridgeShortcutLinkConfig[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.flatMap(item => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const record = item as Record<string, unknown>;
    const label = readString(record.label);
    const href = readString(record.href);
    const color = readString(record.color);
    if (!label || !href || !color) return [];
    // Only same-origin paths and plain hex colors can enter link/style attributes.
    if (!href.startsWith("/") || href.startsWith("//") || /[\\\s]/.test(href)) return [];
    if (!/^#[\da-f]{6}$/i.test(color)) return [];
    return [{ label, href, color }];
  });
}

function normalizeDanoConfig(raw: unknown): DanoConfig {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return {};
  }

  const record = raw as Record<string, unknown>;
  const productName = readString(record.productName);
  const fieldAssist = readRetryOptions(record.fieldAssist);
  const askUserQuestion = readAskUserQuestionOptions(record.askUserQuestion);
  const slashCommandsAndMentionsEnabled = readBoolean(
    record.slashCommandsAndMentionsEnabled,
  );
  const transcriptProcessSummaryEnabled = readBoolean(
    record.transcriptProcessSummaryEnabled,
  );
  const quickActions = readQuickActions(record.quickActions);
  const shortcutLinks = readShortcutLinks(record.shortcutLinks);

  return {
    ...(record.openVikingSettings === undefined ? {} : { openVikingSettings: readOpenVikingSettings(record.openVikingSettings) }),
    ...(productName ? { productName } : {}),
    ...(fieldAssist ? { fieldAssist } : {}),
    ...(askUserQuestion ? { askUserQuestion } : {}),
    ...(slashCommandsAndMentionsEnabled !== undefined
      ? { slashCommandsAndMentionsEnabled }
      : {}),
    ...(transcriptProcessSummaryEnabled !== undefined
      ? { transcriptProcessSummaryEnabled }
      : {}),
    ...(quickActions ? { quickActions } : {}),
    ...(shortcutLinks ? { shortcutLinks } : {}),
  };
}

function readOpenVikingSettings(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_OPENVIKING_SETTINGS");
  return value as Record<string, unknown>;
}

function findNearestDanoConfig(startDir: string): string | undefined {
  let current = resolve(startDir);

  for (;;) {
    const candidate = join(current, DANO_CONFIG_FILE_NAME);
    if (existsSync(candidate)) {
      return candidate;
    }

    const parent = dirname(current);
    if (parent === current) {
      return undefined;
    }
    current = parent;
  }
}

export function loadDanoConfig(
  options: LoadDanoConfigOptions = {},
): DanoConfig {
  const env = options.env ?? process.env;
  const cwd = options.cwd ?? process.cwd();
  const explicitPath = env.DANO_CONFIG_PATH?.trim();
  const configPath = explicitPath
    ? resolve(cwd, explicitPath)
    : (findNearestDanoConfig(options.startDir ?? cwd) ??
      findNearestDanoConfig(dirname(fileURLToPath(import.meta.url))));

  const configured =
    configPath && existsSync(configPath)
      ? normalizeDanoConfig(
          JSON.parse(readFileSync(configPath, "utf8")) as unknown,
        )
      : {};

  return {
    ...configured,
    slashCommandsAndMentionsEnabled: readSlashCommandsAndMentionsEnabled(
      env,
      configured.slashCommandsAndMentionsEnabled,
    ),
    transcriptProcessSummaryEnabled:
      configured.transcriptProcessSummaryEnabled ?? false,
  };
}
