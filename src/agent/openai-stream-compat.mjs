function completeJson(value) {
  try {
    JSON.parse(value || "{}");
    return true;
  } catch {
    return false;
  }
}

export function canonicalToolName(name, known = []) {
  let raw = String(name || "").trim().replace(/^`+|`+$/g, "");
  const prefixed = raw.match(/^(?:bfunction|function|tool|call|name)\s*[=:]\s*(.+)$/i);
  if (prefixed) raw = prefixed[1].trim().replace(/^`+|`+$/g, "");
  if (!raw) return String(name || "").trim();
  if (!known.length) return raw;
  if (known.includes(raw)) return raw;
  const hit = known.find((item) => item.toLowerCase() === raw.toLowerCase());
  return hit || raw;
}

function rewriteToolNameDeltas(lines, calls, known) {
  if (!known.length) return false;
  const canonByIndex = new Map();
  for (const [index, call] of calls) {
    const canon = canonicalToolName(call.name, known);
    if (canon && canon !== call.name) {
      canonByIndex.set(index, canon);
      call.name = canon;
    }
  }
  if (!canonByIndex.size) return false;
  const seen = new Set();
  for (let i = 0; i < lines.length; i += 1) {
    if (!lines[i].startsWith("data:")) continue;
    const data = lines[i].slice(5).trim();
    if (data === "[DONE]") continue;
    let chunk;
    try {
      chunk = JSON.parse(data);
    } catch {
      continue;
    }
    let dirty = false;
    for (const choice of Array.isArray(chunk?.choices) ? chunk.choices : []) {
      for (const toolCall of Array.isArray(choice?.delta?.tool_calls) ? choice.delta.tool_calls : []) {
        const key = Number.isInteger(toolCall?.index) ? toolCall.index : 0;
        const canon = canonByIndex.get(key);
        if (!canon || toolCall?.function?.name == null || toolCall.function.name === "") continue;
        toolCall.function.name = seen.has(key) ? "" : canon;
        seen.add(key);
        dirty = true;
      }
    }
    if (dirty) lines[i] = `data: ${JSON.stringify(chunk)}`;
  }
  return true;
}

export function normalizeOpenAIToolCallStream(body, knownNames = []) {
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
  let repaired = rewriteToolNameDeltas(lines, calls, knownNames);
  const toolCallCount = calls.size;
  const completeToolCalls = toolCallCount > 0 && [...calls.values()].every((call) => (
    Boolean(call.name) && completeJson(call.arguments)
  ));
  if (!hasFinishReason && !hasError && doneIndex >= 0 && completeToolCalls) {
    const finishChunk = {
      id: metadata.id || "pi-openai-compat",
      object: "chat.completion.chunk",
      created: metadata.created || Math.floor(Date.now() / 1000),
      model: metadata.model || "unknown",
      choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
    };
    lines.splice(doneIndex, 0, `data: ${JSON.stringify(finishChunk)}`, "");
    repaired = true;
  }
  if (!repaired) return { body, repaired: false, toolCallCount };
  return { body: lines.join("\n"), repaired: true, toolCallCount };
}

export function installOpenAIToolCallStreamCompatibility({ baseUrl, onRepair, toolNames = [] } = {}) {
  if (!baseUrl || typeof globalThis.fetch !== "function") return;
  const nativeFetch = globalThis.fetch;
  const normalizedBase = String(baseUrl).replace(/\/+$/, "");
  const known = Array.isArray(toolNames) ? toolNames : [];
  globalThis.fetch = async (input, init) => {
    const response = await nativeFetch(input, init);
    const url = typeof input === "string" ? input : (input?.url || String(input));
    const isTarget = url.startsWith(`${normalizedBase}/`)
      && /\/chat\/completions(?:\?|$)/.test(url)
      && response.ok
      && response.headers.get("content-type")?.includes("text/event-stream");
    if (!isTarget) return response;
    const result = normalizeOpenAIToolCallStream(await response.text(), known);
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
