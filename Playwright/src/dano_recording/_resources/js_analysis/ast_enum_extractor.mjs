/**
 * Deliberately small static JavaScript/TypeScript literal parser.
 *
 * Application source is tokenised as inert text.  This module must never use
 * eval, Function, vm, dynamic import, or require on the supplied source.
 */

const LABEL_KEYS = ["label", "text", "name", "title"];
const VALUE_KEYS = ["value", "id", "key", "code"];
const CONTAINER_KEYS = new Set(["options", "items", "datasource", "valueenum", "enum", "enums", "choices"]);

function tokenize(source, maxTokens = 500_000) {
  const tokens = [];
  let i = 0;
  const push = (type, value) => {
    if (tokens.length >= maxTokens) throw new Error("token capacity exceeded");
    tokens.push({ type, value });
  };
  while (i < source.length) {
    const ch = source[i];
    if (/\s/u.test(ch)) { i += 1; continue; }
    if (ch === "/" && source[i + 1] === "/") {
      i += 2;
      while (i < source.length && source[i] !== "\n") i += 1;
      continue;
    }
    if (ch === "/" && source[i + 1] === "*") {
      const end = source.indexOf("*/", i + 2);
      i = end < 0 ? source.length : end + 2;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      const quote = ch;
      let value = "";
      let complete = false;
      i += 1;
      while (i < source.length) {
        const current = source[i++];
        if (current === "\\") {
          if (i >= source.length) break;
          const escaped = source[i++];
          const escapes = { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f", v: "\v" };
          value += escapes[escaped] ?? escaped;
        } else if (current === quote) {
          complete = true;
          break;
        } else if (quote === "`" && current === "$" && source[i] === "{") {
          // Interpolated templates are not literals and are intentionally skipped.
          complete = false;
          while (i < source.length && source[i] !== "`") i += 1;
          if (source[i] === "`") i += 1;
          break;
        } else {
          value += current;
        }
      }
      push(complete ? "string" : "unknown", value);
      continue;
    }
    if (/[A-Za-z_$]/u.test(ch)) {
      const start = i++;
      while (i < source.length && /[A-Za-z0-9_$]/u.test(source[i])) i += 1;
      push("identifier", source.slice(start, i));
      continue;
    }
    if (/[0-9]/u.test(ch) || (ch === "-" && /[0-9]/u.test(source[i + 1] ?? ""))) {
      const start = i++;
      while (i < source.length && /[0-9A-Fa-f_xXobOB.eE+-]/u.test(source[i])) i += 1;
      const raw = source.slice(start, i).replaceAll("_", "");
      const value = Number(raw);
      push(Number.isFinite(value) ? "number" : "unknown", Number.isFinite(value) ? value : raw);
      continue;
    }
    push("punct", ch);
    i += 1;
  }
  return tokens;
}
function parseValue(tokens, start, depth = 0) {
  if (depth > 40 || start >= tokens.length) return { ok: false, next: start + 1 };
  const token = tokens[start];
  if (token.type === "string" || token.type === "number") {
    return { ok: true, value: token.value, next: start + 1, complete: true };
  }
  if (token.type === "identifier" && ["true", "false", "null"].includes(token.value)) {
    return {
      ok: true,
      value: token.value === "null" ? null : token.value === "true",
      next: start + 1,
      complete: true,
    };
  }
  if (token.value === "[") {
    const output = [];
    let i = start + 1;
    let complete = true;
    while (i < tokens.length && tokens[i].value !== "]") {
      if (tokens[i].value === ",") { i += 1; continue; }
      const child = parseValue(tokens, i, depth + 1);
      if (!child.ok) {
        complete = false;
        while (i < tokens.length && ![",", "]"].includes(tokens[i].value)) i += 1;
      } else {
        output.push(child.value);
        complete &&= child.complete;
        i = child.next;
      }
    }
    return { ok: i < tokens.length, value: output, next: i + 1, complete: complete && i < tokens.length };
  }
  if (token.value === "{") {
    const output = Object.create(null);
    let i = start + 1;
    let complete = true;
    while (i < tokens.length && tokens[i].value !== "}") {
      if (tokens[i].value === ",") { i += 1; continue; }
      const keyToken = tokens[i++];
      if (!["identifier", "string", "number"].includes(keyToken.type)) {
        complete = false;
        while (i < tokens.length && ![",", "}"].includes(tokens[i].value)) i += 1;
        continue;
      }
      const key = String(keyToken.value);
      if (tokens[i]?.value !== ":") {
        // Object shorthand is not a static value we can prove.
        complete = false;
        while (i < tokens.length && ![",", "}"].includes(tokens[i].value)) i += 1;
        continue;
      }
      const child = parseValue(tokens, i + 1, depth + 1);
      if (!child.ok) {
        complete = false;
        i += 2;
        while (i < tokens.length && ![",", "}"].includes(tokens[i].value)) i += 1;
      } else {
        output[key] = child.value;
        complete &&= child.complete;
        i = child.next;
      }
    }
    return { ok: i < tokens.length, value: output, next: i + 1, complete: complete && i < tokens.length };
  }
  return { ok: false, next: start + 1 };
}

function ownValue(object, keys) {
  for (const key of keys) {
    if (Object.hasOwn(object, key)) return object[key];
  }
  return undefined;
}

function optionsFromLiteral(value, symbolPath) {
  if (Array.isArray(value)) {
    if (value.length === 0) return [];
    const objectOptions = [];
    for (const item of value) {
      if (!item || typeof item !== "object" || Array.isArray(item)) continue;
      const label = ownValue(item, LABEL_KEYS);
      const optionValue = ownValue(item, VALUE_KEYS);
      if (label !== undefined && optionValue !== undefined) {
        objectOptions.push({ label: String(label), value: optionValue, disabled: Boolean(item.disabled) });
      }
    }
    if (objectOptions.length === value.length) return objectOptions;
    if (CONTAINER_KEYS.has(symbolPath.split(".").at(-1)?.toLowerCase() ?? "") &&
        value.every((item) => ["string", "number", "boolean"].includes(typeof item))) {
      return value.map((item) => ({ label: String(item), value: item, disabled: false }));
    }
    return [];
  }
  if (value && typeof value === "object") {
    const options = [];
    for (const [key, item] of Object.entries(value)) {
      // Drop TypeScript numeric reverse mappings (e.g. 0 -> "Pending").
      if (/^-?\d+$/u.test(key) && typeof item === "string") continue;
      if (item && typeof item === "object" && !Array.isArray(item)) {
        const label = ownValue(item, LABEL_KEYS);
        const optionValue = ownValue(item, VALUE_KEYS);
        options.push({
          label: String(label ?? key),
          value: optionValue ?? key,
          disabled: Boolean(item.disabled),
        });
      } else if (["string", "number", "boolean"].includes(typeof item)) {
        options.push({ label: String(item), value: key, disabled: false });
      }
    }
    return options;
  }
  return [];
}

function scanLiterals(tokens, maxCandidates) {
  const candidates = [];
  const seen = new Set();
  const add = (symbolPath, parsed) => {
    if (!parsed.ok || candidates.length >= maxCandidates) return;
    const options = optionsFromLiteral(parsed.value, symbolPath);
    if (options.length < 1 || options.length > 5000) return;
    const signature = `${symbolPath}\0${JSON.stringify(options)}`;
    if (seen.has(signature)) return;
    seen.add(signature);
    candidates.push({
      symbol_path: symbolPath,
      options,
      completeness: parsed.complete ? "complete" : "partial",
      proofs: [`static literal bound to ${symbolPath}`],
    });
  };

  for (let i = 0; i < tokens.length && candidates.length < maxCandidates; i += 1) {
    const token = tokens[i];
    if (token.type !== "identifier" && token.type !== "string") continue;
    const name = String(token.value);
    const next = tokens[i + 1]?.value;
    if ((next === ":" || next === "=") && ["[", "{"].includes(tokens[i + 2]?.value)) {
      const lastPart = name.toLowerCase();
      const parsed = parseValue(tokens, i + 2);
      if (CONTAINER_KEYS.has(lastPart) || optionsFromLiteral(parsed.value, name).length > 0) add(name, parsed);
    }
  }
  return candidates;
}

function scanTypeScriptEnums(tokens, maxCandidates) {
  const output = [];
  for (let i = 0; i < tokens.length - 3 && output.length < maxCandidates; i += 1) {
    if (tokens[i].value !== "enum" || tokens[i + 1].type !== "identifier" || tokens[i + 2].value !== "{") continue;
    const name = tokens[i + 1].value;
    const options = [];
    let cursor = i + 3;
    let ordinal = 0;
    let complete = true;
    while (cursor < tokens.length && tokens[cursor].value !== "}") {
      if (tokens[cursor].value === ",") { cursor += 1; continue; }
      const member = tokens[cursor++];
      if (!["identifier", "string"].includes(member.type)) { complete = false; break; }
      let value = ordinal;
      if (tokens[cursor]?.value === "=") {
        const parsed = parseValue(tokens, cursor + 1);
        if (!parsed.ok || !["string", "number"].includes(typeof parsed.value)) { complete = false; break; }
        value = parsed.value;
        cursor = parsed.next;
      }
      options.push({ label: String(member.value), value, disabled: false });
      if (typeof value === "number") ordinal = value + 1; else ordinal += 1;
    }
    if (options.length) {
      output.push({
        symbol_path: name,
        options,
        completeness: complete ? "complete" : "partial",
        proofs: [`static TypeScript enum ${name}`],
      });
    }
  }
  return output;
}

export function extractEnumCandidates(source, { maxTokens = 500_000, maxCandidates = 1000 } = {}) {
  if (typeof source !== "string") throw new TypeError("source must be a string");
  const tokens = tokenize(source, maxTokens);
  const candidates = [
    ...scanTypeScriptEnums(tokens, maxCandidates),
    ...scanLiterals(tokens, maxCandidates),
  ].slice(0, maxCandidates);
  return { status: "ok", candidates };
}
