import { createServer, type ServerResponse } from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

const cli = fileURLToPath(new URL("../../../../scripts/smoke-dano-deploy.mjs", import.meta.url));
const assets = ["entry.js", "entry.css"];

async function smoke(fault?: string) {
  let stream: ServerResponse | undefined;
  let disconnected = false;
  const server = createServer(async (request, response) => {
    if (request.url === "/") {
      response.end('<html><script src="/assets/entry.js"></script><link rel="stylesheet" href="/assets/entry.css"></html>');
    } else if (request.url?.startsWith("/assets/")) {
      response.writeHead(fault === "missing asset" ? 404 : 200, {
        "Content-Type": fault === "HTML asset" ? "text/html" : request.url.endsWith(".css") ? "text/css" : "text/javascript",
      });
      response.end(fault === "empty asset" ? "" : "synthetic asset");
    } else if (request.url === "/api/health") {
      response.end(JSON.stringify({ status: "ok" }));
    } else if (request.url === "/api/clients") {
      response.writeHead(201, { "Set-Cookie": `dano_guest=${"a".repeat(43)}; Path=/; HttpOnly; SameSite=Lax` });
      response.end(JSON.stringify({ client: { id: "fixture" }, eventsUrl: "/events", messagesUrl: "/messages", authentication: { status: "anonymous" } }));
    } else if (request.url === "/events") {
      stream = response;
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.flushHeaders();
    } else if (request.url === "/messages") {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const command = JSON.parse(Buffer.concat(chunks).toString()).payload;
      response.writeHead(202);
      response.end(JSON.stringify({ status: "accepted" }));
      stream!.write(`data: ${JSON.stringify({ type: "response", payload: {
        id: command.id, type: fault === "wrong type" ? "event" : "response",
        command: fault === "wrong command" ? "other" : "get_state",
        success: fault !== "command failure",
        data: fault === "missing state" ? undefined : {
          sessionId: "fixture-session", messageCount: fault === "bad state" ? -1 : 0, isStreaming: false,
        },
      } })}\n\n`);
    } else if (request.url?.endsWith("/disconnect")) {
      disconnected = true;
      stream?.end();
      response.writeHead(202); response.end("{}");
    } else { response.writeHead(404); response.end(); }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address() as { port: number };
  try {
    const child = spawn(process.execPath, [cli], { env: {
      ...process.env, DANO_SMOKE_BASE_URL: `http://127.0.0.1:${address.port}`,
      DANO_SMOKE_TIMEOUT_MS: "2000",
      DANO_SMOKE_EXPECTED_ASSETS_JSON: JSON.stringify(fault === "stale manifest" ? ["previous.js", "entry.css"] : assets),
    } });
    let output = "";
    child.stdout.on("data", chunk => { output += chunk; });
    child.stderr.on("data", chunk => { output += chunk; });
    const [status] = await once(child, "exit");
    return { status, output, disconnected };
  } finally {
    stream?.end(); server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
}

it("checks real HTTP assets, image manifest and the successful HTTP/SSE state round trip", async () => {
  const result = await smoke();
  expect(result.status, result.output).toBe(0);
  expect(result.output).toContain("homepage assets match image manifest");
  expect(result.disconnected).toBe(true);
});

it.each(["command failure", "wrong type", "wrong command", "missing state", "bad state"])(
  "rejects %s and still disconnects the client", async fault => {
    const result = await smoke(fault);
    expect(result.status).not.toBe(0);
    expect(result.disconnected).toBe(true);
  },
);

it.each(["missing asset", "HTML asset", "empty asset", "stale manifest"])(
  "rejects %s before creating a client", async fault => {
    const result = await smoke(fault);
    expect(result.status).not.toBe(0);
    expect(result.disconnected).toBe(false);
  },
);
