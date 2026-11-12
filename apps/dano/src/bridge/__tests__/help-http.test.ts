import { mkdtemp, writeFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:http";
import { once } from "node:events";
import { it, expect } from "vitest";
import { serveHelp } from "../help-content.js";
it("reads host edits on the next request and streams media ranges without exposing outside files", async () => {
  const root = await mkdtemp(join(tmpdir(), "help-http-"));
  const server = createServer((req, res) => void serveHelp(req, res, root));
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/help/`;
  try {
    await writeFile(
      join(root, "index.json"),
      JSON.stringify([
        { id: "intro", title: "入门", file: "intro.md", group: "开始" },
      ]),
    );
    await writeFile(join(root, "intro.md"), "# 第一版");
    expect(await (await fetch(base + "intro.md")).text()).toBe("# 第一版");
    await writeFile(join(root, "intro.md"), "# 修改后的内容");
    expect(await (await fetch(base + "intro.md")).text()).toBe(
      "# 修改后的内容",
    );
    await writeFile(join(root, "demo.mp4"), "0123456789");
    const video = await fetch(base + "demo.mp4", {
      headers: { Range: "bytes=2-5" },
    });
    expect(video.status).toBe(206);
    expect(await video.text()).toBe("2345");
    expect(
      (await fetch(base + "demo.mp4", { method: "HEAD" })).headers.get(
        "content-type",
      ),
    ).toContain("video/mp4");
    await symlink("/etc/passwd", join(root, "outside.md"));
    expect((await fetch(base + "outside.md")).status).toBe(404);
    expect((await fetch(base + "missing.md")).status).toBe(404);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((r) => server.close(() => r()));
    await rm(root, { recursive: true, force: true });
  }
});
