import { execFileSync } from "node:child_process";
import { chmodSync, linkSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { archiveBuildContext, prepareBuildContext } from "../../../../../scripts/deploy-build-context.mjs";

const roots: string[] = [];
afterEach(() => { for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true }); });
function repository() {
  const root = mkdtempSync(join(tmpdir(), "dano-build-context-")); roots.push(root);
  const git = (...args: string[]) => execFileSync("git", args, { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  git("init"); git("config", "user.name", "Fixture"); git("config", "user.email", "fixture@example.invalid");
  mkdirSync(join(root, "deploy"));
  writeFileSync(join(root, "config.json"), '{"synthetic":true}\n');
  writeFileSync(join(root, "deploy", "entry.sh"), "#!/bin/sh\nexit 0\n"); chmodSync(join(root, "deploy", "entry.sh"), 0o755);
  git("add", "."); git("commit", "-m", "fixture");
  return { root, git, sha: git("rev-parse", "HEAD") };
}
it.each([0o002, 0o022, 0o077])("normalizes tracked modes and verified content independently of umask %i", mask => {
  const { root, sha } = repository(), original = process.umask(mask);
  try {
    chmodSync(join(root, "config.json"), mask === 0o077 ? 0o600 : 0o664);
    chmodSync(join(root, "deploy", "entry.sh"), mask === 0o077 ? 0o700 : 0o775);
    const result = prepareBuildContext(root, sha);
    expect(result).toMatchObject({ targetSha: sha, files: 2 });
    expect(statSync(join(root, "config.json")).mode & 0o777).toBe(0o644);
    expect(statSync(join(root, "deploy", "entry.sh")).mode & 0o777).toBe(0o755);
    expect(statSync(join(root, "deploy")).mode & 0o777).toBe(0o755);
    expect(readFileSync(join(root, "config.json"), "utf8")).toBe('{"synthetic":true}\n');
    expect(process.umask()).toBe(mask);
  } finally { process.umask(original); }
});
it.each([0o022, 0o077])("prepares the archive fallback from the exact Git tree under umask %i", mask => {
  const { root, sha } = repository(), original = process.umask(mask);
  try {
    const result = archiveBuildContext(root, tmpdir(), sha); roots.push(result.directory);
    expect(result).toMatchObject({ targetSha: sha, files: 2 });
    expect(statSync(join(result.directory, "config.json")).mode & 0o777).toBe(0o644);
    expect(statSync(join(result.directory, "deploy", "entry.sh")).mode & 0o777).toBe(0o755);
    expect(process.umask()).toBe(mask);
  } finally { process.umask(original); }
});
it("rejects a dirty or mismatched source and untracked content", () => {
  const { root, sha } = repository();
  expect(() => prepareBuildContext(root, "b".repeat(40))).toThrow("UNSAFE_RELEASE_BUILD_CONTEXT");
  writeFileSync(join(root, "untracked.txt"), "synthetic");
  expect(() => prepareBuildContext(root, sha)).toThrow("UNSAFE_RELEASE_BUILD_CONTEXT");
  rmSync(join(root, "untracked.txt"));
  writeFileSync(join(root, "config.json"), "changed");
  expect(() => prepareBuildContext(root, sha)).toThrow("UNSAFE_RELEASE_BUILD_CONTEXT");
});
it("rejects tracked symlinks and hardlinks without changing their target modes", () => {
  const { root, git, sha } = repository();
  const outside = mkdtempSync(join(tmpdir(), "dano-build-outside-")); roots.push(outside);
  const file = join(outside, "private.txt"); writeFileSync(file, "private", { mode: 0o600 });
  symlinkSync(file, join(root, "linked")); git("add", "linked"); git("commit", "-m", "link");
  expect(() => prepareBuildContext(root)).toThrow("UNSAFE_RELEASE_BUILD_CONTEXT");
  expect(statSync(file).mode & 0o777).toBe(0o600);
  git("reset", "--hard", sha);
  const linked = join(outside, "config-link"); linkSync(join(root, "config.json"), linked);
  expect(() => prepareBuildContext(root)).toThrow("UNSAFE_RELEASE_BUILD_CONTEXT");
  expect(statSync(linked).mode & 0o777).toBe(0o644);
});
