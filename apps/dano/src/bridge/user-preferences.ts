import * as fs from "node:fs";
import * as path from "node:path";
import { randomUUID } from "node:crypto";
import {
  ACCENT_COLOR_PRESET_KEYS,
  DEFAULT_ACCENT_COLOR_PRESET,
  type AccentColorPreset,
  type BridgeThemeColorPreference,
} from "../../types/protocol.js";
import type { AuthenticatedUserContext } from "./user-context.js";

const THEME_PREFERENCE_DIRECTORY = "preferences";
const THEME_PREFERENCE_FILE = "theme.json";

function isAccentColorPreset(value: unknown): value is AccentColorPreset {
  return (
    typeof value === "string" &&
    (ACCENT_COLOR_PRESET_KEYS as readonly string[]).includes(value)
  );
}

export function parseThemeColorPreference(
  value: unknown,
): BridgeThemeColorPreference | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const accentColorPreset = (value as { accentColorPreset?: unknown })
    .accentColorPreset;
  return isAccentColorPreset(accentColorPreset)
    ? { accentColorPreset }
    : null;
}

export async function readThemeColorPreference(
  userContext: AuthenticatedUserContext,
): Promise<BridgeThemeColorPreference> {
  try {
    const content = await fs.promises.readFile(themePreferencePath(userContext), "utf8");
    const preference = parseThemeColorPreference(JSON.parse(content) as unknown);
    if (preference) return preference;
  } catch {
    // Missing, unreadable, and malformed preferences all use the product default.
  }
  return { accentColorPreset: DEFAULT_ACCENT_COLOR_PRESET };
}

export async function saveThemeColorPreference(
  userContext: AuthenticatedUserContext,
  preference: BridgeThemeColorPreference,
): Promise<void> {
  const directoryPath = path.join(
    userContext.folderPath,
    THEME_PREFERENCE_DIRECTORY,
  );
  await ensurePreferenceDirectory(directoryPath);
  const filePath = themePreferencePath(userContext);
  const temporaryPath = path.join(
    directoryPath,
    `.${THEME_PREFERENCE_FILE}.${randomUUID()}.tmp`,
  );

  try {
    await fs.promises.writeFile(
      temporaryPath,
      `${JSON.stringify(preference)}\n`,
      { encoding: "utf8", mode: 0o600, flag: "wx" },
    );
    await fs.promises.rename(temporaryPath, filePath);
  } catch (error) {
    await fs.promises.rm(temporaryPath, { force: true }).catch(() => undefined);
    throw error;
  }
}

function themePreferencePath(userContext: AuthenticatedUserContext): string {
  return path.join(
    userContext.folderPath,
    THEME_PREFERENCE_DIRECTORY,
    THEME_PREFERENCE_FILE,
  );
}

async function ensurePreferenceDirectory(directoryPath: string): Promise<void> {
  try {
    const stats = await fs.promises.lstat(directoryPath);
    if (stats.isSymbolicLink() || !stats.isDirectory()) {
      throw new Error("User preferences path is not a safe directory");
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    try {
      await fs.promises.mkdir(directoryPath, { mode: 0o700 });
    } catch (mkdirError) {
      if ((mkdirError as NodeJS.ErrnoException).code !== "EEXIST") {
        throw mkdirError;
      }
    }
    const stats = await fs.promises.lstat(directoryPath);
    if (stats.isSymbolicLink() || !stats.isDirectory()) {
      throw new Error("User preferences path is not a safe directory");
    }
  }
}
