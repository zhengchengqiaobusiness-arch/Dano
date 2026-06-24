import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export interface DanoVersionInfo {
  packageName: string;
  version: string;
  buildSha?: string;
  buildTime?: string;
}

export function readDanoVersionInfo(
  env: Record<string, string | undefined> = process.env,
): DanoVersionInfo {
  const packageName = env.DANO_PACKAGE_NAME?.trim() || "@dano/app";
  const version = env.DANO_VERSION?.trim() || "unknown";
  const buildSha = env.DANO_BUILD_SHA?.trim();
  const buildTime = env.DANO_BUILD_TIME?.trim();

  return {
    packageName,
    version,
    ...(buildSha ? { buildSha } : {}),
    ...(buildTime ? { buildTime } : {}),
  };
}

export const danoVersionTool = defineTool({
  name: "get_dano_version",
  label: "Dano Version",
  description: "Return the package metadata for this running Dano server.",
  promptSnippet:
    "Use get_dano_version when the user asks what Dano version or build is running",
  promptGuidelines: [
    "When the user asks what Dano version or build is running, call get_dano_version and answer from its result.",
    "Do not guess Dano's version from memory or package files.",
  ],
  parameters: Type.Object({}),
  executionMode: "sequential",
  async execute() {
    const versionInfo = readDanoVersionInfo();
    return {
      content: [{ type: "text", text: JSON.stringify(versionInfo) }],
      details: versionInfo,
    };
  },
});
