import type { ClientMessage } from "@dano/types/protocol";

export const PROMPT_ACKNOWLEDGEMENT_TIMEOUT_MS = 10_000;

export class BridgePromptAcknowledgementTimeoutError extends Error {
  constructor() {
    super("Prompt acknowledgement timed out");
    this.name = "BridgePromptAcknowledgementTimeoutError";
  }
}

export class BridgePromptDispatchHttpError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail || `Bridge prompt dispatch failed with status ${status}`);
    this.name = "BridgePromptDispatchHttpError";
  }
}

async function responseErrorDetail(response: Response): Promise<string> {
  try {
    const data = (await response.clone().json()) as { error?: unknown };
    if (typeof data.error === "string") return data.error;
  } catch {
    // Fall through to plain text for non-JSON Bridge responses.
  }
  return response.text().catch(() => "");
}

export async function dispatchPromptForAcknowledgement(
  url: string,
  message: ClientMessage,
  options: {
    fetchImpl?: typeof fetch;
    timeoutMs?: number;
  } = {},
): Promise<void> {
  const controller = new AbortController();
  let acknowledgementTimedOut = false;
  const timeoutMs =
    options.timeoutMs ?? PROMPT_ACKNOWLEDGEMENT_TIMEOUT_MS;
  let timeout: ReturnType<typeof setTimeout> | undefined;

  const timeoutPromise = new Promise<never>((_resolve, reject) => {
    timeout = setTimeout(() => {
      acknowledgementTimedOut = true;
      controller.abort();
      reject(new BridgePromptAcknowledgementTimeoutError());
    }, timeoutMs);
  });

  try {
    const response = await Promise.race([
      (options.fetchImpl ?? fetch)(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(message),
        signal: controller.signal,
      }),
      timeoutPromise,
    ]);
    if (response.status !== 202) {
      throw new BridgePromptDispatchHttpError(
        response.status,
        await responseErrorDetail(response),
      );
    }
  } catch (error) {
    if (acknowledgementTimedOut) {
      throw new BridgePromptAcknowledgementTimeoutError();
    }
    throw error;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}
