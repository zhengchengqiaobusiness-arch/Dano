import type { IncomingMessage, ServerResponse } from "node:http";
import { realpath, stat } from "node:fs/promises";
import path from "node:path";
import send from "send";

const allowed = new Set([
  ".json",
  ".md",
  ".mp4",
  ".webm",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".vtt",
]);
/** Read-only product documentation, separate from user uploads and workspaces. */
export async function serveHelp(
  req: IncomingMessage,
  res: ServerResponse,
  directory?: string,
): Promise<void> {
  const fail = (status: number) => {
    res.writeHead(status, {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
    });
    res.end("帮助内容暂不可用");
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    fail(405);
    return;
  }
  if (!directory) {
    fail(404);
    return;
  }
  try {
    const pathname = new URL(req.url ?? "/", "http://localhost").pathname;
    const relative = decodeURIComponent(pathname.slice("/api/help/".length));
    if (
      !relative ||
      relative.includes("\\") ||
      relative.split("/").some((p) => p.startsWith(".") || !p) ||
      !allowed.has(path.extname(relative).toLowerCase())
    ) {
      fail(404);
      return;
    }
    const root = await realpath(directory);
    const file = await realpath(path.resolve(root, relative));
    if (!file.startsWith(root + path.sep) || !(await stat(file)).isFile()) {
      fail(404);
      return;
    }
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    send(req, encodeURI(file), {
      index: false,
      cacheControl: false,
      etag: false,
      lastModified: false,
      dotfiles: "deny",
    })
      .on("error", (error: { statusCode?: number }) => {
        if (!res.headersSent) fail(error.statusCode ?? 404);
        else res.destroy();
      })
      .pipe(res);
  } catch {
    if (!res.headersSent) fail(404);
    else res.destroy();
  }
}
