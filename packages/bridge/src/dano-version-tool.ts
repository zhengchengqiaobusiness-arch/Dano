import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export interface DanoPackageVersionInfo {
  key: string;
  packageName: string;
  version: string;
}

export interface DanoVersionInfo {
  packages: DanoPackageVersionInfo[];
  buildSha?: string;
  buildTime?: string;
}

function readPackageVersions(
  env: Record<string, string | undefined>,
): DanoPackageVersionInfo[] {
  const configured = env.DANO_PACKAGE_VERSIONS?.trim();
  if (configured) {
    try {
      const parsed = JSON.parse(configured) as unknown;
      if (Array.isArray(parsed)) {
        return parsed.flatMap(item => {
          if (!item || typeof item !== "object") return [];
          const record = item as Record<string, unknown>;
          const key = typeof record.key === "string" ? record.key.trim() : "";
          const packageName =
            typeof record.packageName === "string"
              ? record.packageName.trim()
              : "";
          const version =
            typeof record.version === "string" ? record.version.trim() : "";
          return key && packageName && version
            ? [{ key, packageName, version }]
            : [];
        });
      }
    } catch {
      // Fall through to the single-package compatibility path.
    }
  }

  return [
    {
      key: "app",
      packageName: env.DANO_PACKAGE_NAME?.trim() || "@dano/app",
      version: env.DANO_VERSION?.trim() || "unknown",
    },
  ];
}

export function readDanoVersionInfo(
  env: Record<string, string | undefined> = process.env,
): DanoVersionInfo {
  const buildSha = env.DANO_BUILD_SHA?.trim();
  const buildTime = env.DANO_BUILD_TIME?.trim();

  return {
    packages: readPackageVersions(env),
    ...(buildSha ? { buildSha } : {}),
    ...(buildTime ? { buildTime } : {}),
  };
}

export const danoVersionTool = defineTool({
  name: "get_dano_version",
  label: "Dano Version",
  description: "Return all package version metadata for this running Dano server.",
  promptSnippet:
    "Use get_dano_version when the user asks what Dano versions or build are running",
  promptGuidelines: [
    "When the user asks what Dano version or build is running, call get_dano_version and answer with all package versions from its result.",
    "Do not guess Dano versions from memory or package files.",
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
