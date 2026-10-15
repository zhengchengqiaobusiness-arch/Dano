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

export function createToolCallStreamFixer(knownNames = []) {
  const known = Array.isArray(knownNames) ? knownNames : [];
  const calls = new Map();
  const renamed = new Map();
  let held = [];
  let hasFinishReason = false;
  let hasError = false;
  let metadata = {};
  let repaired = false;

  const unresolved = () => [...calls.entries()].some(([index, call]) => call.name && !renamed.has(index) && !call.arguments);

  function rewrite(line) {
    if (!line.startsWith("data:")) return line;
    const data = line.slice(5).trim();
    if (data === "[DONE]") return line;
    let chunk;
    try {
      chunk = JSON.parse(data);
    } catch {
      return line;
    }
    let dirty = false;
    for (const choice of Array.isArray(chunk?.choices) ? chunk.choices : []) {
      for (const toolCall of Array.isArray(choice?.delta?.tool_calls) ? choice.delta.tool_calls : []) {
        const key = Number.isInteger(toolCall?.index) ? toolCall.index : 0;
        const canon = renamed.get(key);
        if (!canon || canon.same || toolCall?.function?.name == null || toolCall.function.name === "") continue;
        toolCall.function.name = canon.emitted ? "" : canon.name;
        canon.emitted = true;
        dirty = true;
      }
    }
    return dirty ? `data: ${JSON.stringify(chunk)}` : line;
  }

  function resolveNames() {
    for (const [index, call] of calls) {
      if (renamed.has(index) || !call.name) continue;
      const canon = canonicalToolName(call.name, known);
      const settled = known.includes(canon) || Boolean(call.arguments);
      if (!settled) continue;
      if (canon !== call.name) {
        renamed.set(index, { name: canon, emitted: false });
        repaired = true;
      } else {
        renamed.set(index, { name: call.name, emitted: true, same: true });
      }
    }
  }

  function flush() {
    const out = held.map((line) => rewrite(line));
    held = [];
    return out;
  }

  function finishLines() {
    const complete = calls.size > 0 && [...calls.values()].every((call) => Boolean(call.name) && completeJson(call.arguments));
    if (hasFinishReason || hasError || !complete) return [];
    repaired = true;
    const finishChunk = {
      id: metadata.id || "pi-openai-compat",
      object: "chat.completion.chunk",
      created: metadata.created || Math.floor(Date.now() / 1000),
      model: metadata.model || "unknown",
      choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
    };
    return [`data: ${JSON.stringify(finishChunk)}`, ""];
  }

  return {
    push(line) {
      if (line.startsWith("data:")) {
        const data = line.slice(5).trim();
        if (data === "[DONE]") {
          resolveNames();
          return [...flush(), ...finishLines(), line];
        }
        try {
          const chunk = JSON.parse(data);
          if (chunk?.error) hasError = true;
          metadata = { id: chunk?.id || metadata.id, created: chunk?.created || metadata.created, model: chunk?.model || metadata.model };
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
        } catch {
          // 不是 JSON 的 data 行原样转发
        }
      }
      held.push(line);
      resolveNames();
      if (unresolved()) return [];
      return flush();
    },
    end() {
      resolveNames();
      return flush();
    },
    get repaired() {
      return repaired;
    },
    get toolCallCount() {
      return calls.size;
    },
  };
}

export function normalizeOpenAIToolCallStream(body, knownNames = []) {
  const fixer = createToolCallStreamFixer(knownNames);
  const out = [];
  for (const line of String(body).split(/\r?\n/)) out.push(...fixer.push(line));
  out.push(...fixer.end());
  if (!fixer.repaired) return { body, repaired: false, toolCallCount: fixer.toolCallCount };
  return { body: out.join("\n"), repaired: true, toolCallCount: fixer.toolCallCount };
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
    if (!isTarget || !response.body) return response;
    const fixer = createToolCallStreamFixer(known);
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    let carry = "";
    const body = response.body.pipeThrough(new TransformStream({
      transform(chunk, controller) {
        carry += decoder.decode(chunk, { stream: true });
        const lines = carry.split(/\r?\n/);
        carry = lines.pop();
        for (const line of lines) for (const out of fixer.push(line)) controller.enqueue(encoder.encode(`${out}\n`));
      },
      flush(controller) {
        carry += decoder.decode();
        if (carry) for (const out of fixer.push(carry)) controller.enqueue(encoder.encode(`${out}\n`));
        for (const out of fixer.end()) controller.enqueue(encoder.encode(`${out}\n`));
        if (fixer.repaired) onRepair?.({ repaired: true, toolCallCount: fixer.toolCallCount });
      },
    }));
    const headers = new Headers(response.headers);
    headers.delete("content-length");
    headers.delete("content-encoding");
    return new Response(body, { status: response.status, statusText: response.statusText, headers });
  };
}
