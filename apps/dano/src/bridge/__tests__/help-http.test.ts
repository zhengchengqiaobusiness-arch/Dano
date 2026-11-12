import { mkdtemp, writeFile, rm, symlink, mkdir } from "node:fs/promises";
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

it("discovers Markdown and standalone videos without an index, pairs matching files and reflects folder edits", async () => {
  const root = await mkdtemp(join(tmpdir(), "help-discovery-"));
  const server = createServer((req, res) => void serveHelp(req, res, root));
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/help/`;
  try {
    await writeFile(join(root, "介绍.md"), "# 产品介绍");
    await writeFile(join(root, "演示.mp4"), "video");
    await writeFile(join(root, ".upload.mp4"), "partial");
    await writeFile(join(root, "image.png"), "image");
    await symlink("/etc/passwd", join(root, "private.md"));
    const catalog = await fetch(base + "index.json");
    expect(catalog.status).toBe(200);
    expect(catalog.headers.get("cache-control")).toBe("no-store");
    expect(await catalog.json()).toEqual(expect.arrayContaining([
      expect.objectContaining({ title: "介绍", file: "介绍.md" }),
      expect.objectContaining({ title: "演示", video: "演示.mp4" }),
    ]));
    expect(await (await fetch(base + "index.json")).json()).toHaveLength(2);
    await writeFile(join(root, "演示.md"), "视频说明");
    const paired = await (await fetch(base + "index.json")).json();
    expect(paired).toHaveLength(2);
    expect(paired).toContainEqual(expect.objectContaining({ file: "演示.md", video: "演示.mp4" }));
    await writeFile(join(root, "index.json"), JSON.stringify([
      { id: "custom", title: "产品演示", file: "演示.md", video: "演示.mp4", group: "入门" },
    ]));
    await mkdir(join(root, "指南"));
    await writeFile(join(root, "指南", "操作.MD"), "# 操作");
    await symlink(root, join(root, "loop"));
    const customized = await (await fetch(base + "index.json")).json() as { title: string }[];
    expect(customized).toHaveLength(3);
    expect(customized[0]).toMatchObject({ id: "custom", title: "产品演示" });
    expect(customized).toContainEqual(expect.objectContaining({ title: "操作", file: "指南/操作.MD", group: "指南" }));
    await rm(join(root, "index.json"));
    await rm(join(root, "演示.mp4"));
    expect(await (await fetch(base + "index.json")).json()).toContainEqual(
      expect.objectContaining({ title: "演示", file: "演示.md" }),
    );
  } finally {
    server.closeAllConnections();
    await new Promise<void>((r) => server.close(() => r()));
    await rm(root, { recursive: true, force: true });
  }
});
