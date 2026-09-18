import { randomBytes } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { copyFile, lstat, mkdtemp, rename, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { isAbsolute, join } from "node:path";
import { pipeline } from "node:stream/promises";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import type {
  CredentialBroker,
  ProviderRequest,
  ProviderSendEvidence,
} from "./credential-broker.js";

interface ProviderPythonOptions {
  broker: CredentialBroker;
  scope: string;
  agentSessionId: string;
  cwd: string;
  signal?: AbortSignal;
  /** Protected, read-only installation path provisioned by the trusted launcher. */
  moduleDirectory?: string;
}

function shellQuote(value: string): string {
  return "'" + value.replaceAll("'", "'\"'\"'") + "'";
}

const PYTHON_MODULE_NAMES = ["dano_provider.py", "sitecustomize.py"] as const;

/** A loopback listener and unguessable capability exist only during one bash call. */
export async function withProviderPython<T>(
  options: ProviderPythonOptions,
  execute: (
    commandPrefix: string,
    redact: <V>(value: V) => V,
    requests: ProviderPythonRequest[],
    redactFile: (path: string) => Promise<void>,
  ) => Promise<T>,
): Promise<T> {
  if (options.moduleDirectory !== undefined) {
    if (!isAbsolute(options.moduleDirectory)) throw new Error("Provider Python module directory must be absolute");
    try {
      if (!(await lstat(options.moduleDirectory)).isDirectory()) throw new Error();
      for (const name of PYTHON_MODULE_NAMES) {
        if (!(await lstat(join(options.moduleDirectory, name))).isFile()) throw new Error();
      }
    } catch {
      throw new Error("Provider Python installation modules unavailable");
    }
  }
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
  const redactFile = async (path: string) => {
    const staged = `${path}.${randomBytes(16).toString("hex")}`;
    try {
      await pipeline(
        createReadStream(path),
        async function* (source) {
          const secret = Buffer.from(capability);
          let pending = Buffer.alloc(0);
          for await (const chunk of source) {
            pending = Buffer.concat([pending, chunk]);
            let match: number;
            while ((match = pending.indexOf(secret)) !== -1) {
              yield pending.subarray(0, match);
              yield Buffer.from("[redacted]");
              pending = pending.subarray(match + secret.length);
            }
            // Retain enough text for a capability split between read chunks.
            const safeLength = Math.max(
              0,
              pending.length - secret.length + 1,
            );
            yield pending.subarray(0, safeLength);
            pending = pending.subarray(safeLength);
          }
          yield pending;
        },
        createWriteStream(staged, { flags: "wx", mode: 0o600 }),
      );
      await rename(staged, path);
    } finally {
      await rm(staged, { force: true });
    }
  };
  const lifetime = new AbortController();
  const signal = options.signal
    ? AbortSignal.any([options.signal, lifetime.signal])
    : lifetime.signal;
  const directory = options.moduleDirectory ?? await mkdtemp(join(options.cwd, ".dano-provider-"));
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
      const connection = new AbortController();
      const sends: ProviderSendEvidence[] = [];
      const disconnected = () => connection.abort();
      res.once("close", disconnected);
      const response = await request(
        input as ProviderRequest,
        AbortSignal.any([signal, connection.signal]),
        evidence => sends.push(evidence),
      ).finally(() => res.off("close", disconnected));
      let businessCode: number | undefined;
      if (response.ok) {
        try {
          const body = JSON.parse(response.body);
          if (body && typeof body.code === "number" && Number.isFinite(body.code))
            businessCode = body.code;
        } catch {
          // Non-JSON responses have no business-code evidence. Never retain an
          // arbitrary string "code", response body or private payload in audit.
        }
      }
      requests.push({
        method: typeof input.method === "string" ? input.method : "",
        path: typeof input.path === "string" ? input.path.split("?")[0] : "",
        loginSessionBound:
          sends.length > 0 &&
          sends.every(send => send.authorizationMatched && send.targetMatched),
        sends,
        ...(businessCode === undefined ? {} : { businessCode }),
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
    if (!options.moduleDirectory) {
      for (const name of PYTHON_MODULE_NAMES) {
        await copyFile(
          new URL(`./python/${name}`, import.meta.url),
          join(directory, name),
        );
      }
    }
    const origin = options.broker.pythonRequestOrigin(
      options.scope,
      options.agentSessionId,
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
      `export DANO_PROVIDER_ORIGIN=${shellQuote(origin ?? "")}; ` +
      `export DANO_PROVIDER_URL=${shellQuote(`http://127.0.0.1:${address.port}/request`)}; ` +
        `export DANO_PROVIDER_CAPABILITY=${shellQuote(capability)}; ` +
        `export PYTHONPATH=${shellQuote(directory)}"\${PYTHONPATH:+:$PYTHONPATH}"; `,
      redact,
      requests,
      redactFile,
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
    if (!options.moduleDirectory) await rm(directory, { recursive: true, force: true });
  }
}

interface ProviderPythonRequest {
  method: string;
  path: string;
  loginSessionBound: boolean;
  sends: ProviderSendEvidence[];
  businessCode?: number;
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
      "Python urllib and standard HTTPX transport requests to the exact configured OA origin automatically use the initiating login during bash execution. Non-matching origins retain their original authentication. Run existing Skill scripts unchanged: do not edit their source, token configuration or URLs, and do not replace them with provider_request calls. Custom transports, other HTTP clients and Python -S/-I/-E are not covered. Never print token configuration or credentials.",
      "When bash output is truncated, missing visible text does not mean an empty business result or a network/authentication failure. Use the reported provider request evidence to distinguish HTTP outcomes from business outcomes. Read the saved output in bounded pages or use the Skill's supported pagination; do not repeat writes or change authentication to recover truncated output.",
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
        async (prefix, redact, requests, redactFile) => {
          const input = params as { command: string };
          const artifacts = new Set<string>();
          const artifactPath = (value: { details?: unknown }) => {
            const path = (
              value.details as { fullOutputPath?: unknown } | undefined
            )?.fullOutputPath;
            if (typeof path === "string") artifacts.add(path);
            return typeof path === "string" ? path : undefined;
          };
          const result = await tool
            .execute(
              id,
              { ...input, command: prefix + input.command },
              executionSignal,
              update => {
                const path = artifactPath(update);
                // The underlying accumulator may still append raw data. Publish its
                // artifact only after execution ends and the complete file is scrubbed.
                const safe = redact(update);
                if (path) {
                  delete (safe.details as { fullOutputPath?: unknown })
                    .fullOutputPath;
                  for (const part of safe.content) {
                    if (part.type === "text")
                      part.text = part.text.replaceAll(
                        path,
                        "[available after execution]",
                      );
                  }
                }
                onUpdate?.(safe);
              },
              context,
            )
            .then(result => {
              artifactPath(result);
              return result;
            })
            .finally(async () => {
              for (const path of artifacts) {
                await redactFile(path);
              }
            });
          const safeResult = redact(result);
          const truncation = (safeResult.details as {
            truncation?: { truncated?: boolean; outputBytes?: number };
          } | undefined)?.truncation;
          if (truncation?.truncated) {
            if (truncation.outputBytes === 0) {
              for (const part of safeResult.content) {
                if (part.type === "text")
                  part.text = part.text.replace(/^\(no output\)/, "[Output omitted by truncation]");
              }
            }
            const httpSuccess = requests.filter(r => r.status !== undefined && r.status >= 200 && r.status < 300).length;
            const bound = requests.filter(r => r.loginSessionBound).length;
            const businessSuccess = requests.filter(r => r.businessCode === 0).length;
            const httpOther = requests.filter(r => r.status !== undefined && (r.status < 200 || r.status >= 300)).length;
            const requestErrors = requests.filter(r => r.error !== undefined).length;
            const businessOther = requests.filter(r => r.businessCode !== undefined && r.businessCode !== 0).length;
            safeResult.content.push({ type: "text", text:
              `[Dano output evidence: output was truncated, not empty. ${requests.length} provider requests observed; ${httpSuccess} HTTP 2xx; ${bound} bound to the initiating login; ${httpOther} non-2xx HTTP responses; ${requestErrors} request errors; ${businessSuccess} reported business code 0; ${businessOther} reported nonzero business codes. HTTP success alone does not establish business success. Missing outcomes remain unconfirmed. Read the saved full output in bounded pages or use supported query pagination. Do not infer network/authentication failure from missing output or retry writes.]`,
            });
          }
          return {
            ...safeResult,
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
