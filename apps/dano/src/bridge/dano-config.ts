import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { BridgeQuickActionConfig, RpcThinkingLevel } from "./types.js";

const DANO_CONFIG_FILE_NAME = "dano.config.json";

export interface DanoConfig {
  defaultProvider?: string;
  defaultModel?: string;
  defaultThinkingLevel?: RpcThinkingLevel;
  defaultProjectTrust?: "always" | string;
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
}

export const DANO_DEFAULT_CONFIG = {
  defaultProvider: "xiaomi-token-plan-cn",
  defaultModel: "mimo-v2.5",
  defaultThinkingLevel: "medium",
  defaultProjectTrust: "always",
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
} satisfies Required<DanoConfig>;

export interface LoadDanoConfigOptions {
  cwd?: string;
  env?: Record<string, string | undefined>;
  startDir?: string;
}

function isThinkingLevel(value: unknown): value is RpcThinkingLevel {
  return (
    value === "off" ||
    value === "minimal" ||
    value === "low" ||
    value === "medium" ||
    value === "high" ||
    value === "xhigh"
  );
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

function normalizeDanoConfig(raw: unknown): DanoConfig {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return {};
  }

  const record = raw as Record<string, unknown>;
  const defaultProvider = readString(record.defaultProvider);
  const defaultModel = readString(record.defaultModel);
  const defaultProjectTrust = readString(record.defaultProjectTrust);
  const defaultThinkingLevel = isThinkingLevel(record.defaultThinkingLevel)
    ? record.defaultThinkingLevel
    : undefined;
  const fieldAssist = readRetryOptions(record.fieldAssist);
  const askUserQuestion = readAskUserQuestionOptions(record.askUserQuestion);
  const slashCommandsAndMentionsEnabled = readBoolean(
    record.slashCommandsAndMentionsEnabled,
  );
  const transcriptProcessSummaryEnabled = readBoolean(
    record.transcriptProcessSummaryEnabled,
  );
  const quickActions = readQuickActions(record.quickActions);

  return {
    ...(defaultProvider ? { defaultProvider } : {}),
    ...(defaultModel ? { defaultModel } : {}),
    ...(defaultThinkingLevel ? { defaultThinkingLevel } : {}),
    ...(defaultProjectTrust ? { defaultProjectTrust } : {}),
    ...(fieldAssist ? { fieldAssist } : {}),
    ...(askUserQuestion ? { askUserQuestion } : {}),
    ...(slashCommandsAndMentionsEnabled !== undefined
      ? { slashCommandsAndMentionsEnabled }
      : {}),
    ...(transcriptProcessSummaryEnabled !== undefined
      ? { transcriptProcessSummaryEnabled }
      : {}),
    ...(quickActions ? { quickActions } : {}),
  };
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
