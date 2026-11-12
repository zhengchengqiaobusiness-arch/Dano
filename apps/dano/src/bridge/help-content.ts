import type { IncomingMessage, ServerResponse } from "node:http";
import { readdir, readFile, realpath, stat } from "node:fs/promises";
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
/** Optional editorial metadata augments files discovered in the mounted directory. */
async function helpCatalog(root: string) {
  const files: string[] = [];
  async function visit(relative: string) {
    const directory = path.join(root, relative);
    // A directory may have been replaced while its parent was being read.
    if (await realpath(directory) !== directory) return;
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (entry.name.startsWith(".") || entry.isSymbolicLink()) continue;
      const file = relative ? relative + "/" + entry.name : entry.name;
      if (entry.isDirectory()) await visit(file);
      else if (entry.isFile() && /\.(md|mp4|webm)$/i.test(file)) files.push(file);
    }
  }
  await visit("");
  files.sort((a, b) => a.localeCompare(b, "zh-CN", { numeric: true }));
  let configured: Record<string, unknown>[] = [];
  try {
    const indexFile = path.join(root, "index.json");
    if (await realpath(indexFile) !== indexFile) throw Error("Invalid help index");
    const value: unknown = JSON.parse(await readFile(indexFile, "utf8"));
    if (!Array.isArray(value) || value.some(item => !item || typeof item !== "object" || Array.isArray(item)))
      throw Error("Invalid help index");
    configured = value;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  configured = configured.map(item => ({ ...item,
    ...(typeof item.file === "string" ? { file: path.posix.normalize(item.file) } : {}),
    ...(typeof item.video === "string" ? { video: path.posix.normalize(item.video) } : {}),
  }));
  const explicitVideos = new Set(configured.map(item => item.video));
  configured = configured.flatMap(item => {
    const file = typeof item.file === "string" && files.includes(item.file) ? item.file : undefined;
    const explicitVideo = typeof item.video === "string" && files.includes(item.video) ? item.video : undefined;
    const stem = file?.slice(0, -path.extname(file).length);
    const companion = file && item.video === undefined ? files.find(candidate =>
      /\.(mp4|webm)$/i.test(candidate) && candidate.slice(0, -path.extname(candidate).length) === stem && !explicitVideos.has(candidate)) : undefined;
    const video = explicitVideo ?? companion;
    return file || video ? [{ ...item, file, video }] : [];
  });
  const referenced = new Set(configured.flatMap(item => [item.file, item.video]));
  const ids = new Set(configured.map(item => item.id));
  const discovered: Record<string, unknown>[] = [];
  for (const file of files) {
    if (referenced.has(file)) continue;
    const extension = path.extname(file);
    const stem = file.slice(0, -extension.length);
    const title = path.posix.basename(stem);
    const group = path.posix.dirname(file);
    if (extension.toLowerCase() !== ".md" && files.some(candidate => /\.md$/i.test(candidate) && candidate.slice(0, -path.extname(candidate).length) === stem && !referenced.has(candidate))) continue;
    let id = "file:" + file;
    let suffix = 0;
    while (ids.has(id)) id = "file:" + file + "#" + (++suffix);
    ids.add(id);
    const article: Record<string, unknown> = {
      id, title, ...(group === "." ? {} : { group }),
    };
    if (extension.toLowerCase() === ".md") {
      article.file = file;
      const video = files.find(candidate => candidate.slice(0, -path.extname(candidate).length) === stem && /\.(mp4|webm)$/i.test(candidate) && !referenced.has(candidate));
      if (video) { article.video = video; referenced.add(video); }
    } else article.video = file;
    discovered.push(article);
  }
  return [...configured, ...discovered];
}

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
    if (relative === "index.json") {
      const body = Buffer.from(JSON.stringify(await helpCatalog(root)));
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "Content-Length": body.length });
      res.end(req.method === "HEAD" ? undefined : body);
      return;
    }
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
