// Compatibility shim for OpenAI-compatible gateways that emit a complete
// streamed tool call and [DONE], but omit the required finish_reason chunk.

function completeJson(value) {
  try {
    JSON.parse(value || "{}");
    return true;
  } catch {
    return false;
  }
}

export function normalizeOpenAIToolCallStream(body) {
  const lines = String(body).split(/\r?\n/);
  const calls = new Map();
  let doneIndex = -1;
  let hasFinishReason = false;
  let hasError = false;
  let metadata = {};

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.startsWith("data:")) continue;
    const data = line.slice(5).trim();
    if (data === "[DONE]") {
      doneIndex = index;
      continue;
    }
    let chunk;
    try {
      chunk = JSON.parse(data);
    } catch {
      continue;
    }
    if (chunk?.error) hasError = true;
    metadata = {
      id: chunk?.id || metadata.id,
      created: chunk?.created || metadata.created,
      model: chunk?.model || metadata.model,
    };
    for (const choice of Array.isArray(chunk?.choices) ? chunk.choices : []) {
      if (choice?.finish_reason) hasFinishReason = true;
      for (const toolCall of Array.isArray(choice?.delta?.tool_calls) ? choice.delta.tool_calls : []) {
        const key = Number.isInteger(toolCall?.index) ? toolCall.index : calls.size;
        const current = calls.get(key) || { name: "", arguments: "" };
        current.name += toolCall?.function?.name || "";
        current.arguments += toolCall?.function?.arguments || "";
        calls.set(key, current);
      }
    }
  }

  const toolCallCount = calls.size;
  const completeToolCalls = toolCallCount > 0 && [...calls.values()].every((call) => (
    Boolean(call.name) && completeJson(call.arguments)
  ));
  if (hasFinishReason || hasError || doneIndex < 0 || !completeToolCalls) {
    return { body, repaired: false, toolCallCount };
  }

  const finishChunk = {
    id: metadata.id || "dano-openai-compat",
    object: "chat.completion.chunk",
    created: metadata.created || Math.floor(Date.now() / 1000),
    model: metadata.model || "unknown",
    choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
  };
  lines.splice(doneIndex, 0, `data: ${JSON.stringify(finishChunk)}`, "");
  return { body: lines.join("\n"), repaired: true, toolCallCount };
}

export function installOpenAIToolCallStreamCompatibility({ baseUrl, onRepair } = {}) {
  if (!baseUrl || typeof globalThis.fetch !== "function") return;
  const nativeFetch = globalThis.fetch;
  const normalizedBase = String(baseUrl).replace(/\/+$/, "");

  globalThis.fetch = async (input, init) => {
    const response = await nativeFetch(input, init);
    const url = typeof input === "string" ? input : (input?.url || String(input));
    const isTarget = url.startsWith(`${normalizedBase}/`)
      && /\/chat\/completions(?:\?|$)/.test(url)
      && response.ok
      && response.headers.get("content-type")?.includes("text/event-stream");
    if (!isTarget) return response;

    const result = normalizeOpenAIToolCallStream(await response.text());
    const headers = new Headers(response.headers);
    headers.delete("content-length");
    headers.delete("content-encoding");
    if (result.repaired) onRepair?.(result);
    return new Response(result.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  };
}
