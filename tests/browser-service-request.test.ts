import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import extension from "../.pi/extensions/business-skill-studio.js";

async function withService(handler: http.RequestListener, run: (tool: any) => Promise<void>) {
  const server = http.createServer(handler);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const previousUrl = process.env.BSS_BROWSER_SERVICE_URL;
  const previousToken = process.env.BSS_BROWSER_SERVICE_TOKEN;
  process.env.BSS_BROWSER_SERVICE_URL = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  process.env.BSS_BROWSER_SERVICE_TOKEN = "local-test-token";
  const tools = new Map<string, any>();
  try {
    extension({ registerProvider() {}, on() {}, registerTool(tool: any) { tools.set(tool.name, tool); } } as any);
    await run(tools.get("business_skill_record_start"));
  } finally {
    if (previousUrl === undefined) delete process.env.BSS_BROWSER_SERVICE_URL;
    else process.env.BSS_BROWSER_SERVICE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.BSS_BROWSER_SERVICE_TOKEN;
    else process.env.BSS_BROWSER_SERVICE_TOKEN = previousToken;
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
}

test("manual takeover may delay response headers without restarting the recording", async () => {
  // Set to 310000 for the real five-minute fetch-header-timeout reproduction.
  const delay = Number(process.env.BSS_TEST_MANUAL_WAIT_MS || 30);
  let requests = 0;
  await withService((request, response) => {
    requests++;
    assert.equal(request.url, "/internal/browser/start");
    assert.equal(request.headers.authorization, "Bearer local-test-token");
    const timer = setTimeout(() => response.end(JSON.stringify({ id: "same-recording" })), delay);
    response.on("close", () => clearTimeout(timer));
  }, async tool => {
    const result = await tool.execute("start", { url: "http://oa.test/" }, new AbortController().signal);
    assert.equal(result.details.id, "same-recording");
    assert.equal(requests, 1, "waiting must not create a second recording request");
  });
});

test("human cancellation aborts a pending manual takeover request", async () => {
  const controller = new AbortController();
  await withService(() => { controller.abort(); }, async tool => {
    await assert.rejects(tool.execute("start", { url: "http://oa.test/" }, controller.signal), { name: "AbortError" });
  });
});

test("browser service errors remain visible to Pi", async () => {
  await withService((_request, response) => {
    response.writeHead(409).end(JSON.stringify({ error: "录制冲突" }));
  }, async tool => {
    await assert.rejects(tool.execute("start", { url: "http://oa.test/" }), /录制冲突/);
  });
});
