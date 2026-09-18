import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UserContext } from "../user-context.js";
import { UserRuntimeRegistry } from "../user-runtime-registry.js";
import type { ProtectedSessionTools } from "../protected-session-tools.js";

const runtimeRoots: string[] = [];

afterEach(() => {
  for (const root of runtimeRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

describe("UserRuntimeRegistry owner transfer", () => {
  it("binds each server user context to its own protected backend profile", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-protected-users-"));
    runtimeRoots.push(root);
    const alice = userContext(root, "alice");
    const bob = userContext(root, "bob");
    const profiles = new Map<string, ProtectedSessionTools>();
    const protectedToolsForUser = vi.fn(async (context: UserContext) => {
      const profile: ProtectedSessionTools = {
        agentDir: path.join(root, "private", context.user.id), trustedSkillPaths: [],
        async resolveWorker() { throw new Error("not executed by the registry"); },
      };
      profiles.set(context.user.id, profile);
      return profile;
    });
    const dispose = vi.fn(async () => {});
    const backend = vi.fn(async () => ({ dispose }) as never);
    const registry = new UserRuntimeRegistry(backend, { protectedToolsForUser });
    try {
      const [a, b, repeated] = await Promise.all([registry.get(alice), registry.get(bob), registry.get(alice)]);
      expect(repeated).toBe(a);
      expect(b).not.toBe(a);
      expect(protectedToolsForUser).toHaveBeenCalledTimes(2);
      expect(protectedToolsForUser).toHaveBeenCalledWith(alice);
      expect(protectedToolsForUser).toHaveBeenCalledWith(bob);
      for (const context of [alice, bob]) {
        expect(backend).toHaveBeenCalledWith(expect.objectContaining({
          cwd: path.join(context.folderPath, "workspaces", "default"),
          credentialBrokerScope: context.user.id,
          protectedTools: profiles.get(context.user.id),
        }));
      }
    } finally {
      await registry.dispose();
    }
    expect(dispose).toHaveBeenCalledTimes(2);
  });

  it("merges preference objects while preserving unrelated file conflicts", async () => {
    const runtimeRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "dano-owner-transfer-"),
    );
    runtimeRoots.push(runtimeRoot);
    const source = userContext(runtimeRoot, "anonymous-source");
    const target = userContext(runtimeRoot, "authenticated-target");
    writeJson(path.join(source.folderPath, "preferences", "layout.json"), {
      sourceOnly: "migrated",
      shared: "anonymous",
    });
    writeJson(path.join(target.folderPath, "preferences", "layout.json"), {
      targetOnly: "retained",
      shared: "authenticated",
    });
    writeText(
      path.join(source.folderPath, "workspaces", "default", "shared.txt"),
      "anonymous content",
    );
    writeText(
      path.join(target.folderPath, "workspaces", "default", "shared.txt"),
      "authenticated content",
    );
    const registry = new UserRuntimeRegistry(async () => {
      throw new Error("test backend should not be created");
    });

    await registry.transferOwnership(source, target, {
      assertIdle() {},
      async commitOwnership() {},
    });

    expect(
      JSON.parse(
        fs.readFileSync(
          path.join(target.folderPath, "preferences", "layout.json"),
          "utf8",
        ),
      ),
    ).toEqual({
      sourceOnly: "migrated",
      targetOnly: "retained",
      shared: "authenticated",
    });
    expect(
      fs.existsSync(
        path.join(target.folderPath, "preferences", "layout.anonymous-1.json"),
      ),
    ).toBe(false);
    expect(
      fs.readFileSync(
        path.join(target.folderPath, "workspaces", "default", "shared.txt"),
        "utf8",
      ),
    ).toBe("authenticated content");
    expect(
      fs.readFileSync(
        path.join(
          target.folderPath,
          "workspaces",
          "default",
          "shared.anonymous-1.txt",
        ),
        "utf8",
      ),
    ).toBe("anonymous content");
  });
});

function userContext(runtimeRoot: string, userId: string): UserContext {
  return {
    user: { id: userId },
    folderPath: path.join(runtimeRoot, "users", userId),
  };
}

function writeJson(filePath: string, value: Record<string, unknown>): void {
  writeText(filePath, `${JSON.stringify(value)}\n`);
}

function writeText(filePath: string, value: string): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, value, "utf8");
}
