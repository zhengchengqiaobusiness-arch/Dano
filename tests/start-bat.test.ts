import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import http from "node:http";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

test("start.bat reuses an already running Studio instead of exiting with a port error", { skip: process.platform !== "win32" }, async () => {
  const server = http.createServer((request, response) => {
    if (request.url === "/api/status") {
      response.setHeader("content-type", "application/json");
      response.end('{"agent":{"ready":true}}');
      return;
    }
    response.writeHead(404).end();
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");

  try {
    const result = await execFileAsync("cmd.exe", ["/d", "/c", "start.bat"], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        BSS_PORT: String(address.port),
        BSS_OPEN_UI: "false",
        BSS_NO_PAUSE: "true"
      },
      timeout: 10_000
    });
    assert.match(result.stdout, /already running/i);
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
