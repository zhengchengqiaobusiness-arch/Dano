import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  buildBwrapArgs,
  getSandboxPathAccess,
  normalizeSandboxConfig,
  protectHeimdallConfigPaths,
} from "@josephyoung/pi-heimdall/guards/sandbox-guard.js";

describe("Dano Heimdall runtime config", () => {
  it("does not recreate protected config files that the sandbox path policy hides", () => {
    const workspace = "/opt/dano/runtime-data/workspaces/session";
    const runtimeConfig = "/opt/dano/runtime-data/.pi/agent/heimdall.json";
    const workspaceConfig = join(workspace, ".pi/heimdall.json");
    const config = normalizeSandboxConfig({
      enabled: true,
      paths: {
        "/opt": {
          path: "/opt/dano/runtime-data/.agents/skills",
        },
        "/opt/dano/runtime-data/.pi": { mode: "deny" },
      },
    });

    const protectedConfig = protectHeimdallConfigPaths(config, [
      runtimeConfig,
      workspaceConfig,
    ], workspace);

    expect(protectedConfig.paths[resolve(runtimeConfig)]).toBeUndefined();
    expect(protectedConfig.paths[resolve(workspaceConfig)]).toContainEqual({
      path: resolve(workspaceConfig),
      content: "",
    });
  });

  describe("shipped sandbox policy", () => {
    let root: string;
    let runtimeDir: string;
    let runtimePi: string;
    let agentDir: string;
    let workspace: string;
    let skillsDir: string;
    let syntheticDir: string;
    let config: ReturnType<typeof normalizeSandboxConfig>;
    let args: string[];
    let previousEnv: Record<string, string | undefined>;

    beforeEach(() => {
      root = mkdtempSync(join(tmpdir(), "dano-heimdall-runtime-"));
      runtimeDir = join(root, "runtime-data");
      runtimePi = join(runtimeDir, ".pi");
      agentDir = join(runtimePi, "agent");
      workspace = join(runtimeDir, "workspaces/session");
      skillsDir = join(runtimeDir, ".agents/skills");
      syntheticDir = join(root, "synthetic");
      const home = join(root, "home/node");

      for (const directory of [agentDir, workspace, skillsDir, syntheticDir, home]) {
        mkdirSync(directory, { recursive: true });
      }
      for (const file of ["SYSTEM.md", "settings.json", "heimdall.json"]) {
        writeFileSync(join(agentDir, file), `${file} secret\n`);
      }

      previousEnv = Object.fromEntries(
        [
          "DANO_RUNTIME_DIR",
          "PI_CODING_AGENT_DIR",
          "HOME",
          "HEIMDALL_BWRAP_BIND_ROOT",
          "HEIMDALL_BWRAP_BIND_KERNEL_FS",
          "HEIMDALL_BWRAP_BIND_PROC",
        ]
          .map((name) => [name, process.env[name]]),
      );
      process.env.DANO_RUNTIME_DIR = runtimeDir;
      process.env.PI_CODING_AGENT_DIR = agentDir;
      process.env.HOME = home;
      process.env.HEIMDALL_BWRAP_BIND_ROOT = join(runtimeDir, "workspaces");
      process.env.HEIMDALL_BWRAP_BIND_KERNEL_FS = "1";
      process.env.HEIMDALL_BWRAP_BIND_PROC = "0";

      const shippedConfig = JSON.parse(
        readFileSync(resolve("deploy/runtime-defaults/heimdall.json"), "utf8"),
      ) as { sandbox?: Parameters<typeof normalizeSandboxConfig>[0] };
      config = protectHeimdallConfigPaths(
        normalizeSandboxConfig(shippedConfig.sandbox),
        [join(agentDir, "heimdall.json"), join(workspace, ".pi/heimdall.json")],
        workspace,
      );
      args = buildBwrapArgs(config, workspace, syntheticDir, "true");
    });

    afterEach(() => {
      for (const [name, value] of Object.entries(previousEnv)) {
        if (value === undefined) delete process.env[name];
        else process.env[name] = value;
      }
      rmSync(root, { recursive: true, force: true });
    });

    it.each([
      ["runtime config directory", ""],
      ["agent config directory", "agent"],
      ["system prompt", "agent/SYSTEM.md"],
      ["Pi settings", "agent/settings.json"],
      ["Heimdall settings", "agent/heimdall.json"],
    ])("keeps the %s outside chat bash", (_label, relativePath) => {
      expect(
        getSandboxPathAccess(config, workspace, join(runtimePi, relativePath)).access,
      ).toBe("none");
    });

    it("mounts only the writable workspace and read-only runtime skills under runtime data", () => {
      const serializedArgs = args.join("\0");

      expect(serializedArgs).toContain(
        ["--bind", join(runtimeDir, "workspaces"), join(runtimeDir, "workspaces")].join("\0"),
      );
      expect(serializedArgs).toContain(
        ["--ro-bind", skillsDir, skillsDir].join("\0"),
      );
      expect(args).not.toContain("/proc");
      expect(serializedArgs).not.toContain(runtimePi);
    });

    it("keeps workspace Heimdall config protected after hiding runtime config", () => {
      const workspaceConfig = join(workspace, ".pi/heimdall.json");
      const targetIndex = args.lastIndexOf(workspaceConfig);

      expect(args[targetIndex - 2]).toBe("--ro-bind");
      expect(readFileSync(args[targetIndex - 1], "utf8")).toBe("");
    });
  });
});
