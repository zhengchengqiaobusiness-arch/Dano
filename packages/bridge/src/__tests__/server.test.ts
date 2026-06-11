import * as http from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import { ConversationController } from "../http-command-adapter.js";
import { createHttpRequestHandler } from "../server.js";
import type { RuntimeCallbacks, ServerLlmRuntime } from "../types.js";

class EchoRuntime implements ServerLlmRuntime {
  async sendUserMessage(text: string, callbacks: RuntimeCallbacks): Promise<void> {
    callbacks.onDelta(`Echo: ${text}`);
    callbacks.onComplete(`Echo: ${text}`);
  }
}

async function startTestServer() {
  const controller = new ConversationController({
    runtimeFactory: () => new EchoRuntime(),
  });
  const server = http.createServer(
    createHttpRequestHandler(controller, { heartbeatMs: 50 }),
  );

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  if (!address || typeof address !== "object") {
    throw new Error("Missing test server address");
  }

  return {
    controller,
    url: `http://127.0.0.1:${address.port}`,
    stop: () =>
      new Promise<void>((resolve, reject) => {
        server.close(error => {
          if (error) reject(error);
          else resolve();
        });
      }),
  };
}

describe("HTTP server", () => {
  const stops: Array<() => Promise<void>> = [];

  afterEach(async () => {
    await Promise.all(stops.splice(0).map(stop => stop()));
  });

  it("returns health through the API", async () => {
    const server = await startTestServer();
    stops.push(server.stop);

    const response = await fetch(`${server.url}/api/health`);

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
  });

  it("creates conversations without exposing server secrets", async () => {
    const server = await startTestServer();
    stops.push(server.stop);
    process.env.OPENAI_API_KEY = "sk-test-secret";

    const response = await fetch(`${server.url}/api/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const body = await response.text();

    delete process.env.OPENAI_API_KEY;
    expect(response.status).toBe(201);
    expect(body).toContain("conv_1");
    expect(body).not.toContain("sk-test-secret");
  });

  it("opens EventSource-compatible streams with required headers", async () => {
    const server = await startTestServer();
    stops.push(server.stop);
    const created = await fetch(`${server.url}/api/conversations`, {
      method: "POST",
      body: "{}",
    }).then(response => response.json() as Promise<{ eventsUrl: string }>);

    const abort = new AbortController();
    const response = await fetch(`${server.url}${created.eventsUrl}`, {
      signal: abort.signal,
    });
    abort.abort();

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
  });

  it("sends messages through HTTP POST and emits completion events", async () => {
    const server = await startTestServer();
    stops.push(server.stop);
    const created = await fetch(`${server.url}/api/conversations`, {
      method: "POST",
      body: "{}",
    }).then(response => response.json() as Promise<{ conversationId: string }>);

    const response = await fetch(
      `${server.url}/api/conversations/${created.conversationId}/messages`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientMessageId: "smoke-1", text: "Hello" }),
      },
    );

    await new Promise(resolve => setTimeout(resolve, 0));

    expect(response.status).toBe(202);
    expect(
      server.controller.eventBus
        .getHistory(created.conversationId)
        .map(event => event.event),
    ).toContain("assistant.completed");
  });
});
