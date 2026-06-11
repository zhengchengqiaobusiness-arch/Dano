import { ConversationController } from "../http-command-adapter.js";
import { createPiCodingAgentRuntimeFactory } from "../llm-runtime.js";
import { startHttpServer, type DanoHttpServerController } from "../server.js";
import type { ChatServerConfig, ServerLlmRuntimeFactory } from "../types.js";

export interface StartStandaloneServerOptions {
  runtimeFactory?: ServerLlmRuntimeFactory;
}

export async function startStandaloneServer(
  config: ChatServerConfig,
  options: StartStandaloneServerOptions = {},
): Promise<DanoHttpServerController> {
  const runtimeFactory =
    options.runtimeFactory ??
    createPiCodingAgentRuntimeFactory({
      cwd: config.cwd,
      sessionDir: config.sessionDir,
      timeoutMs: readTimeoutMs(),
    });

  const controller = new ConversationController({ runtimeFactory });
  return startHttpServer(controller, config);
}

function readTimeoutMs(): number {
  const value = Number.parseInt(process.env.DANO_LLM_TIMEOUT_MS ?? "", 10);
  return Number.isFinite(value) && value > 0 ? value : 30_000;
}
