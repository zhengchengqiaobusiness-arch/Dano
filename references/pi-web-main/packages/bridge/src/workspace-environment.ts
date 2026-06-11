import * as fs from "node:fs";
import * as path from "node:path";
import type { RpcWorkspaceEnvironment } from "./types.js";

const PYTHON_VENV_ACTIVATE_CANDIDATES = [
  ".venv/bin/activate",
  "venv/bin/activate",
  "env/bin/activate",
  ".venv/Scripts/activate",
  "venv/Scripts/activate",
  "env/Scripts/activate",
] as const;

function fileExists(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

function shellQuote(value: string): string {
  return "'" + value.replace(/'/g, "'\"'\"'") + "'";
}

function readTextFile(filePath: string): string | null {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return null;
  }
}

function findPythonVenvActivateScript(cwd: string): string | null {
  for (const candidate of PYTHON_VENV_ACTIVATE_CANDIDATES) {
    if (fileExists(path.join(cwd, candidate))) {
      return candidate;
    }
  }

  return null;
}

function buildDirenvEnvironment(cwd: string): RpcWorkspaceEnvironment | null {
  if (!fileExists(path.join(cwd, ".envrc"))) {
    return null;
  }

  return {
    type: "direnv",
    label: "direnv",
    detail: ".envrc",
  };
}

function normalizePythonEnvLabel(
  value: string | null | undefined,
): string | null {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }

  return trimmed.replace(/^['"]|['"]$/g, "").trim() || null;
}

function readPythonVenvPrompt(
  cwd: string,
  activateScript: string,
): string | null {
  const activateScriptPath = path.join(cwd, activateScript);
  const venvRoot = path.dirname(path.dirname(activateScriptPath));
  const pyvenvCfg = readTextFile(path.join(venvRoot, "pyvenv.cfg"));
  if (pyvenvCfg) {
    const promptMatch = pyvenvCfg.match(/^prompt\s*=\s*(.+)$/m);
    const prompt = normalizePythonEnvLabel(promptMatch?.[1]);
    if (prompt) {
      return prompt;
    }
  }

  const activateContents = readTextFile(activateScriptPath);
  if (!activateContents) {
    return null;
  }

  const promptMatch = activateContents.match(/^VIRTUAL_ENV_PROMPT=(.+)$/m);
  return normalizePythonEnvLabel(promptMatch?.[1]);
}

function buildPythonVenvEnvironment(
  cwd: string,
): RpcWorkspaceEnvironment | null {
  const activateScript = findPythonVenvActivateScript(cwd);
  if (!activateScript) {
    return null;
  }

  const rootDir = activateScript.split(/[\\/]/, 1)[0] || "venv";
  const configuredPrompt = readPythonVenvPrompt(cwd, activateScript);
  const fallbackWorkspaceName = path.basename(cwd);
  const label =
    configuredPrompt && ![".venv", "venv", "env"].includes(configuredPrompt)
      ? configuredPrompt
      : [".venv", "venv", "env"].includes(rootDir) && fallbackWorkspaceName
        ? fallbackWorkspaceName
        : configuredPrompt || rootDir;

  return {
    type: "python-venv",
    label,
    detail: activateScript,
  };
}

export function detectWorkspaceEnvironments(
  cwd: string | null | undefined,
): RpcWorkspaceEnvironment[] | undefined {
  const normalizedCwd = cwd?.trim();
  if (!normalizedCwd) {
    return undefined;
  }

  const environments = [
    buildDirenvEnvironment(normalizedCwd),
    buildPythonVenvEnvironment(normalizedCwd),
  ].filter((environment): environment is RpcWorkspaceEnvironment =>
    Boolean(environment),
  );

  return environments.length > 0 ? environments : undefined;
}

export function buildWorkspaceActivationPrefix(
  cwd: string,
): string | undefined {
  const normalizedCwd = cwd.trim();
  if (!normalizedCwd) {
    return undefined;
  }

  const activationSteps: string[] = [];
  const environments = detectWorkspaceEnvironments(normalizedCwd) ?? [];

  for (const environment of environments) {
    if (environment.type === "direnv") {
      activationSteps.push(
        [
          "if command -v direnv >/dev/null 2>&1; then",
          '  eval "$(direnv export bash 2>/dev/null)" || true',
          "fi",
        ].join("\n"),
      );
      continue;
    }

    if (environment.type === "python-venv" && environment.detail) {
      const quotedScriptPath = shellQuote(environment.detail);
      activationSteps.push(
        [
          'if [ -z "${VIRTUAL_ENV:-}" ] && [ -f ' +
            quotedScriptPath +
            " ]; then",
          "  . " + quotedScriptPath,
          "fi",
        ].join("\n"),
      );
    }
  }

  return activationSteps.length > 0 ? activationSteps.join("\n") : undefined;
}
