import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, statSync, writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
// @ts-expect-error Public deployment script has no TypeScript declaration.
import { initializeHelp } from "../../../../../scripts/initialize-help.mjs";

const defaults = new URL("../../../../../deploy/help/", import.meta.url);
const roots: string[] = [];
afterEach(() => roots.splice(0).forEach(root => rmSync(root, { recursive: true, force: true })));
function target() {
  const root = mkdtempSync(join(tmpdir(), "dano-help-defaults-"));
  roots.push(root);
  return join(root, "custom-help");
}

describe("host help defaults", () => {
  it.each([false, true])("initializes a new or empty custom directory (exists=%s)", exists => {
    const directory = target();
    if (exists) mkdirSync(directory);
    expect(initializeHelp(directory, defaults)).toBe(true);
    expect(JSON.parse(readFileSync(join(directory, "index.json"), "utf8"))).toHaveLength(2);
    expect(readFileSync(join(directory, "demo.md"), "utf8")).toContain("约需30分钟");
    for (const file of readdirSync(join(directory, "应用入口"))) {
      const content = readFileSync(join(directory, "应用入口", file), "utf8");
      expect(content).toContain('target="_blank" rel="noopener noreferrer"');
    }
    expect(initializeHelp(directory, defaults)).toBe(false);
  });

  it("makes new public defaults readable under a private deployment umask", () => {
    const directory = target();
    const previous = process.umask(0o077);
    try { initializeHelp(directory, defaults); } finally { process.umask(previous); }
    expect(statSync(directory).mode & 0o777).toBe(0o755);
    expect(statSync(join(directory, "应用入口")).mode & 0o777).toBe(0o755);
    expect(statSync(join(directory, "demo.md")).mode & 0o777).toBe(0o644);
  });

  it("preserves edits and deletions without restoring missing defaults", () => {
    const directory = target();
    initializeHelp(directory, defaults);
    writeFileSync(join(directory, "demo.md"), "用户修改");
    unlinkSync(join(directory, "maintenance.md"));
    expect(initializeHelp(directory, defaults)).toBe(false);
    expect(readFileSync(join(directory, "demo.md"), "utf8")).toBe("用户修改");
    expect(readdirSync(directory)).not.toContain("maintenance.md");
  });

  it("leaves even a non-default existing manual untouched", () => {
    const directory = target(); mkdirSync(directory);
    writeFileSync(join(directory, ".keep"), "existing");
    expect(initializeHelp(directory, defaults)).toBe(false);
    expect(readdirSync(directory)).toEqual([".keep"]);
  });

  it("rejects symlinks, files and relative destinations", () => {
    const directory = target(); symlinkSync(join(directory, "missing"), directory);
    expect(() => initializeHelp(directory, defaults)).toThrow("real directory");
    unlinkSync(directory); writeFileSync(directory, "not a folder");
    expect(() => initializeHelp(directory, defaults)).toThrow("real directory");
    expect(() => initializeHelp("relative", defaults)).toThrow("absolute");
  });
});
