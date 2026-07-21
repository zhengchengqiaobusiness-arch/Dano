export type SafeBashCommandSummary =
  | { kind: "commands"; executableNames: string[] }
  | { kind: "script" };

const SHELL_CONTROL_WORDS = new Set([
  "case",
  "do",
  "done",
  "elif",
  "else",
  "esac",
  "fi",
  "for",
  "function",
  "if",
  "in",
  "select",
  "then",
  "time",
  "until",
  "while",
]);

export function safeBashCommandSummary(command: string): SafeBashCommandSummary {
  const segments = simpleShellSegments(command);
  if (!segments?.length) return { kind: "script" };

  const executableNames: string[] = [];
  for (const segment of segments) {
    const name = simpleExecutableName(segment);
    if (!name) return { kind: "script" };
    executableNames.push(name);
  }
  return { kind: "commands", executableNames };
}

function simpleShellSegments(command: string): string[] | undefined {
  const segments: string[] = [];
  let current = "";
  let quote = "";
  let escaped = false;
  let comment = false;
  let atWordStart = true;

  const finishSegment = () => {
    const segment = current.trim();
    if (segment) segments.push(segment);
    current = "";
    atWordStart = true;
  };

  for (let index = 0; index < command.length; index += 1) {
    const character = command[index]!;
    if (comment) {
      if (character === "\n") {
        comment = false;
        finishSegment();
      }
      continue;
    }
    if (escaped) {
      current += character;
      escaped = false;
      atWordStart = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      current += character;
      escaped = true;
      continue;
    }
    if (quote) {
      current += character;
      if (character === quote) quote = "";
      continue;
    }
    if (character === "'" || character === '"') {
      current += character;
      quote = character;
      atWordStart = false;
      continue;
    }
    if (character === "#" && atWordStart) {
      comment = true;
      continue;
    }
    if (
      character === "`" || character === "(" || character === ")" ||
      character === "{" || character === "}" ||
      (character === "$" && command[index + 1] === "(") ||
      (character === "<" && command[index + 1] === "<")
    ) return undefined;

    const isRedirectAmpersand = character === "&" &&
      (command[index - 1] === ">" || command[index - 1] === "<" ||
        command[index + 1] === ">");
    const isRedirectPipe = character === "|" && command[index - 1] === ">";
    const isBoundary = character === ";" || character === "\n" ||
      (character === "|" && !isRedirectPipe) ||
      (character === "&" && !isRedirectAmpersand);
    if (isBoundary) {
      finishSegment();
      while (command[index + 1] === character) index += 1;
      continue;
    }

    current += character;
    atWordStart = /\s/u.test(character);
  }

  if (quote || escaped) return undefined;
  finishSegment();
  return segments;
}

function simpleExecutableName(segment: string): string | undefined {
  let index = 0;
  while (index < segment.length) {
    while (/\s/u.test(segment[index] ?? "")) index += 1;
    if (index >= segment.length || startsWithRedirection(segment, index)) {
      return undefined;
    }

    const word = readShellWord(segment, index);
    if (!word) return undefined;
    index = word.endIndex;
    if (/^[A-Za-z_][A-Za-z0-9_]*=/u.test(word.value)) continue;
    if (SHELL_CONTROL_WORDS.has(word.value) || word.value.includes("$")) {
      return undefined;
    }
    const name = word.value.replace(/^.*[\\/]/u, "");
    return /^[\w.+-]+$/u.test(name) ? name : undefined;
  }
  return undefined;
}

function startsWithRedirection(segment: string, startIndex: number): boolean {
  let operatorIndex = startIndex;
  while (/\d/u.test(segment[operatorIndex] ?? "")) operatorIndex += 1;
  return ["<<<", "<<-", "&>>", ">>", "<<", "<&", ">&", "<>", ">|", "&>", ">", "<"]
    .some(operator => segment.startsWith(operator, operatorIndex));
}

function readShellWord(
  source: string,
  startIndex: number,
): { value: string; endIndex: number } | undefined {
  let quote = "";
  let escaped = false;
  let value = "";
  let index = startIndex;

  for (; index < source.length; index += 1) {
    const character = source[index]!;
    if (escaped) {
      value += character;
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = "";
      else value += character;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (/\s/u.test(character)) break;
    value += character;
  }

  return quote || escaped ? undefined : { value, endIndex: index };
}
