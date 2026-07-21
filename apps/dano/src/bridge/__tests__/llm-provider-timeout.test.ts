import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import {
  SessionManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";
import { afterEach, describe, expect, it, vi } from "vitest";

import { configureDanoLlmResilience } from "../llm-resilience.js";
import {
  DANO_LLM_RATE_LIMIT_ERROR,
  normalizeLlmErrorMessage,
} from "../llm-error.js";

const roots: string[] = [];
const servers: http.Server[] = [];

afterEach(async () => {
  for (const server of servers.splice(0)) {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
  for (const root of roots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

async function startProvider(
  handler: http.RequestListener,
): Promise<string> {
  const server = http.createServer(handler);
  servers.push(server);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("Provider test server did not bind to a TCP port");
  }
  return `http://127.0.0.1:${address.port}/v1`;
}

async function createProviderSession(
  baseUrl: string,
  timeoutMs: number,
  options: { persistSession?: boolean } = {},
) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-llm-timeout-"));
  roots.push(root);
  const model = {
    id: "timeout-fixture",
    name: "Timeout Fixture",
    provider: "timeout-fixture",
    api: "openai-completions" as const,
    baseUrl,
    reasoning: false,
    input: ["text" as const],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 16_000,
    maxTokens: 1_024,
  };
  const { session } = await createAgentSession({
    cwd: root,
    agentDir: path.join(root, "agent"),
    model,
    noTools: "all",
    sessionManager: options.persistSession
      ? SessionManager.create(root, root)
      : SessionManager.inMemory(root),
  });
  vi.spyOn(session.modelRegistry, "hasConfiguredAuth").mockReturnValue(true);
  vi.spyOn(session.modelRegistry, "getApiKeyAndHeaders").mockResolvedValue({
    ok: true,
    apiKey: "test-key",
  });
  configureDanoLlmResilience(session.settingsManager, session, {
    DANO_LLM_TIMEOUT_MS: String(timeoutMs),
  });
  session.settingsManager.applyOverrides({ retry: { enabled: false } });
  return session;
}

function writeChunk(
  response: http.ServerResponse,
  content: string,
  finishReason: string | null = null,
): void {
  response.write(
    `data: ${JSON.stringify({
      id: "chatcmpl-fixture",
      object: "chat.completion.chunk",
      created: 1,
      model: "timeout-fixture",
      choices: [
        { index: 0, delta: content ? { content } : {}, finish_reason: finishReason },
      ],
    })}\n\n`,
  );
}

describe("Dano provider timeout", () => {
  it("persists the user JSONL before presenting a real provider 429 fixture", async () => {
    const baseUrl = await startProvider((_request, response) => {
      response.writeHead(429, { "content-type": "application/json" });
      response.end(
        JSON.stringify({
          error: {
            message: "429 rate limit exceeded",
            type: "rate_limit_error",
          },
        }),
      );
    });
    const session = await createProviderSession(baseUrl, 1_000, {
      persistSession: true,
    });

    try {
      await session.prompt("provider-rate-limit-prompt");
      const assistant = session.messages.at(-1);
      if (!assistant) throw new Error("provider did not produce a response");
      const sessionFile = session.sessionManager.getSessionFile();
      const jsonl = sessionFile ? fs.readFileSync(sessionFile, "utf8") : "";

      expect(jsonl).toContain('"role":"user"');
      expect(jsonl).toContain("provider-rate-limit-prompt");
      expect(assistant).toMatchObject({ role: "assistant", stopReason: "error" });
      expect(normalizeLlmErrorMessage(assistant)).toBe(
        DANO_LLM_RATE_LIMIT_ERROR,
      );
    } finally {
      session.dispose();
    }
  });

  it("aborts a provider request that never returns response headers", async () => {
    const baseUrl = await startProvider((_request, _response) => {});
    const session = await createProviderSession(baseUrl, 50);

    try {
      await session.prompt("hello");
      const assistant = session.messages.at(-1);
      expect(assistant).toMatchObject({ role: "assistant", stopReason: "error" });
      expect(
        (assistant as { errorMessage?: string }).errorMessage,
      ).toMatch(/timed? out|timeout/i);
    } finally {
      session.dispose();
    }
  });

  it("keeps an active response stream alive after the first chunk", async () => {
    const baseUrl = await startProvider((_request, response) => {
      response.writeHead(200, {
        "content-type": "text/event-stream",
        connection: "keep-alive",
      });
      writeChunk(response, "first ");
      setTimeout(() => writeChunk(response, "second "), 40);
      setTimeout(() => writeChunk(response, "third"), 80);
      setTimeout(() => {
        writeChunk(response, "", "stop");
        response.end("data: [DONE]\n\n");
      }, 120);
    });
    const session = await createProviderSession(baseUrl, 60);

    try {
      await session.prompt("hello");
      const assistant = session.messages.at(-1);
      expect(assistant).toMatchObject({
        role: "assistant",
        stopReason: "stop",
        content: [{ type: "text", text: "first second third" }],
      });
    } finally {
      session.dispose();
    }
  });
});
