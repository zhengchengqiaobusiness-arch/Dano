import { expect, it, vi } from "vitest";
import { normalizeSandboxConfig, getSandboxPathAccess, buildBwrapArgs } from "@josephyoung/pi-heimdall/guards/sandbox-guard.js";
import { workerSandboxPolicy } from "../worker-workspace.js";

// The deployment's /app resources are absent on the macOS test host. Keep the
// actual Heimdall access/mount logic; simulate only those files' existence.
vi.mock("node:fs", async importOriginal => {
  const fs = await importOriginal<typeof import("node:fs")>();
  return { ...fs, existsSync: (path: import("node:fs").PathLike) =>
    typeof path === "string" && path.startsWith("/app/") ? true : fs.existsSync(path) };
});

it("makes only approved installation resources readable in the real Heimdall policy and Shell mounts", () => {
  const workspace = "/var/lib/dano/users/alice/workspaces/default";
  const skill = "/app/skills/approved", modules = "/app/provider-python";
  const before = normalizeSandboxConfig(workerSandboxPolicy(workspace).sandbox);
  expect(getSandboxPathAccess(before, workspace, `${skill}/SKILL.md`).access).toBe("none");
  const after = normalizeSandboxConfig(workerSandboxPolicy(workspace, [skill, modules]).sandbox);
  for (const path of [`${skill}/SKILL.md`, `${skill}/scripts/run.py`, `${modules}/dano_provider.py`]) {
    expect(getSandboxPathAccess(after, workspace, path).access).toBe("read");
  }
  for (const path of ["/app/skills/unapproved/SKILL.md", "/app/server/private.json", "/var/lib/dano/host-state/key"]) {
    expect(getSandboxPathAccess(after, workspace, path).access).toBe("none");
  }
  expect(getSandboxPathAccess(after, workspace, `${workspace}/output.txt`).access).toBe("write");
  const args = buildBwrapArgs(after, workspace, "/unused-synthetic-directory", "true");
  for (const path of [skill, modules]) {
    const index = args.indexOf(path);
    expect(index).toBeGreaterThan(0);
    expect(args.slice(index - 1, index + 2)).toEqual(["--ro-bind", path, path]);
  }
});

it("rejects noncanonical or workspace-overlapping privileged resource paths", () => {
  for (const path of ["relative", "/", "/workspace", "/workspace/sub", "/app/../workspace"]) {
    expect(() => workerSandboxPolicy("/workspace", [path])).toThrow("UNSAFE_WORKER_WORKSPACE");
  }
});
