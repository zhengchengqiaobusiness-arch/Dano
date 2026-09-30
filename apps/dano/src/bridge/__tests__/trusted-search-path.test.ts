import { beforeEach, expect, it, vi } from "vitest";
import { lstat, realpath } from "node:fs/promises";
import { rootSearchPath } from "../trusted-installation.js";
vi.mock("node:fs/promises", () => ({ lstat: vi.fn(), realpath: vi.fn(), readdir: vi.fn() }));
beforeEach(() => {
  vi.mocked(realpath).mockImplementation(async path => String(path) === "/bin" ? "/usr/bin" : String(path));
  vi.mocked(lstat).mockImplementation(async () => ({ uid: 0, mode: 0o755, isDirectory: () => true }) as never);
});
it("canonicalizes immutable executable search directories", async () => {
  await expect(rootSearchPath("/bin:/usr/local/bin")).resolves.toBe("/usr/bin:/usr/local/bin");
});
it("rejects relative and current-directory search entries", async () => {
  for (const path of ["", ".:/usr/bin", "/usr/bin:", "bin"]) await expect(rootSearchPath(path)).rejects.toThrow();
});
it("rejects writable or non-root ancestors even when the final directory is protected", async () => {
  for (const metadata of [{ uid: 10001, mode: 0o755 }, { uid: 0, mode: 0o775 }, { uid: 0, mode: 0o777 }]) {
    vi.mocked(lstat).mockImplementation(async path => ({ ...(path === "/usr" ? metadata : { uid: 0, mode: 0o755 }), isDirectory: () => true }) as never);
    await expect(rootSearchPath("/usr/bin")).rejects.toThrow("WORKER_BROKER_INSTALLATION_REQUIRED");
  }
});
