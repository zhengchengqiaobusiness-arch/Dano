import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { expect, it } from "vitest";

// Selected only by the container profile. Portable tests cover policy generation;
// this checks actual namespace execution with the runtime's Linux capabilities.
it("runs bash in a real bubblewrap namespace without writing into host tmp", () => {
  const marker = `/tmp/dano-sandbox-test-${randomUUID()}`;
  const result = spawnSync("bwrap", [
    "--unshare-user", "--unshare-pid", "--die-with-parent",
    "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
    "--", "bash", "-c", 'printf sandbox-ready > "$1"; cat "$1"', "bash", marker,
  ], { encoding: "utf8", timeout: 10_000 });
  expect(result.status, result.stderr).toBe(0);
  expect(result.stdout).toBe("sandbox-ready");
  expect(existsSync(marker)).toBe(false);
});
