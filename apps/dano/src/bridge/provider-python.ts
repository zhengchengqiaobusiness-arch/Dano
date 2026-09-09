import { randomBytes } from "node:crypto";
import { copyFile, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { join } from "node:path";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import type { CredentialBroker, ProviderRequest } from "./credential-broker.js";

interface ProviderPythonOptions {
  broker: CredentialBroker;
  scope: string;
  agentSessionId: string;
  cwd: string;
  signal?: AbortSignal;
}

function shellQuote(value: string): string {
  return "'" + value.replaceAll("'", "'\"'\"'") + "'";
}

/** A loopback listener and unguessable capability exist only during one bash call. */
export async function withProviderPython<T>(
  options: ProviderPythonOptions,
  execute: (
    commandPrefix: string,
    redact: <V>(value: V) => V,
    requests: ProviderPythonRequest[],
  ) => Promise<T>,
): Promise<T> {
  const request = options.broker.bindRequest(
    options.scope,
    options.agentSessionId,
  );
  const capability = randomBytes(32).toString("hex");
  const redact = <V>(value: V): V =>
    value === undefined
      ? value
      : JSON.parse(JSON.stringify(value).replaceAll(capability, "[redacted]"));
  const requests: ProviderPythonRequest[] = [];
  const lifetime = new AbortController();
  const signal = options.signal
    ? AbortSignal.any([options.signal, lifetime.signal])
    : lifetime.signal;
  const directory = await mkdtemp(join(options.cwd, ".dano-provider-"));
  const server = createServer(async (req, res) => {
    res.setHeader("content-type", "application/json");
    if (
      signal.aborted ||
      req.method !== "POST" ||
      req.url !== "/request" ||
      req.headers.authorization !== `Bearer ${capability}`
    ) {
      res
        .writeHead(403)
        .end('{"ok":false,"error":{"code":"authentication_required"}}');
      return;
    }
    try {
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 1024 * 1024) {
          res
            .writeHead(413)
            .end('{"ok":false,"error":{"code":"request_too_large"}}');
          return;
        }
        chunks.push(chunk);
      }
      const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!input || typeof input !== "object" || Array.isArray(input)) {
        res
          .writeHead(400)
          .end('{"ok":false,"error":{"code":"invalid_provider_request"}}');
        return;
      }
      const response = await request(input as ProviderRequest, signal);
      requests.push({
        method: typeof input.method === "string" ? input.method : "",
        path: typeof input.path === "string" ? input.path.split("?")[0] : "",
        loginSessionBound: response.ok,
        ...(response.ok
          ? { status: response.status }
          : { error: response.error.code }),
      });
      res.end(JSON.stringify(response));
    } catch {
      res
        .writeHead(400)
        .end('{"ok":false,"error":{"code":"provider_request_failed"}}');
    }
  });
  try {
    await copyFile(
      new URL("./python/dano_provider.py", import.meta.url),
      join(directory, "dano_provider.py"),
    );
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (!address || typeof address === "string")
      throw new Error("Provider listener unavailable");
    if (signal.aborted) throw new Error("Provider execution cancelled");
    return await execute(
      `export DANO_PROVIDER_URL=${shellQuote(`http://127.0.0.1:${address.port}/request`)}; ` +
        `export DANO_PROVIDER_CAPABILITY=${shellQuote(capability)}; ` +
        `export PYTHONPATH=${shellQuote(directory)}; `,
      redact,
      requests,
    );
  } catch (error) {
    throw new Error(
      error instanceof Error
        ? error.message.replaceAll(capability, "[redacted]")
        : "Provider execution failed",
    );
  } finally {
    lifetime.abort();
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(directory, { recursive: true, force: true });
  }
}

interface ProviderPythonRequest {
  method: string;
  path: string;
  loginSessionBound: boolean;
  status?: number;
  error?: string;
}

export function wrapProviderBash(
  tool: ToolDefinition,
  options: Omit<ProviderPythonOptions, "agentSessionId">,
): ToolDefinition {
  return {
    ...tool,
    promptGuidelines: [
      ...(tool.promptGuidelines ?? []),
      "Python Skills can import dano_provider.request(method, relative_path, headers=None, body=None) inside bash to use the current login. Provider credentials stay in Dano; no token configuration is needed.",
    ],
    async execute(id, params, signal, onUpdate, context) {
      const executionSignal =
        options.signal && signal
          ? AbortSignal.any([options.signal, signal])
          : (options.signal ?? signal);
      return withProviderPython(
        {
          ...options,
          agentSessionId: context.sessionManager.getSessionId(),
          signal: executionSignal,
        },
        async (prefix, redact, requests) => {
          const input = params as { command: string };
          const result = await tool.execute(
            id,
            { ...input, command: prefix + input.command },
            executionSignal,
            onUpdate ? update => onUpdate(redact(update)) : undefined,
            context,
          );
          return {
            ...redact(result),
            details: {
              ...(redact(result.details) as object),
              providerRequests: requests,
            },
          };
        },
      );
    },
  };
}
