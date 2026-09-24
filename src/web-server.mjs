import http from "node:http";
import { createReadStream } from "node:fs";
import path from "node:path";
import { webDir } from "./paths.mjs";

const port = Number(process.env.WEB_PORT || 19082);
const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const file = url.pathname === "/" || url.pathname === "/index.html" ? "index.html" : "";
  if (!file) {
    res.writeHead(404);
    res.end();
    return;
  }
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  createReadStream(path.join(webDir(), file)).pipe(res);
});
server.listen(port, "127.0.0.1", () => {
  console.log(`playwright-cabp web http://127.0.0.1:${port}`);
});
