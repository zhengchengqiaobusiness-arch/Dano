type HeredocSpec = {
  delimiter: string;
  stripLeadingTabs: boolean;
};

type ShellQuote = "" | "'" | '"';

type ShellWord = {
  endIndex: number;
  value: string;
};

export function safeBashExecutableNames(command: string): string[] {
  return shellCommandSegments(withoutHeredocBodies(command)).flatMap(segment => {
    const executable = firstShellWord(segment);
    if (!executable || executable.includes("$")) return [];
    const name = executable.replace(/^.*[\\/]/u, "");
    return /^[\w.+-]+$/u.test(name) ? [name] : [];
  });
}

function withoutHeredocBodies(command: string): string {
  const visibleLines: string[] = [];
  const pendingHeredocs: HeredocSpec[] = [];
  let quote: ShellQuote = "";

  for (const line of command.split(/\r?\n/u)) {
    const activeHeredoc = pendingHeredocs[0];
    if (activeHeredoc) {
      const delimiterCandidate = activeHeredoc.stripLeadingTabs
        ? line.replace(/^\t+/u, "")
        : line;
      if (delimiterCandidate === activeHeredoc.delimiter) {
        pendingHeredocs.shift();
      }
      continue;
    }

    visibleLines.push(line);
    const scan = heredocSpecs(line, quote);
    quote = scan.quote;
    pendingHeredocs.push(...scan.specs);
  }

  return visibleLines.join("\n");
}

function heredocSpecs(
  line: string,
  initialQuote: ShellQuote,
): { specs: HeredocSpec[]; quote: ShellQuote } {
  const specs: HeredocSpec[] = [];
  const quote = scanUnquotedShellCharacters(line, initialQuote, (character, index) => {
    if (character !== "<" || line[index + 1] !== "<" || line[index + 2] === "<") {
      return;
    }

    const parsed = readHeredocSpec(line, index + 2);
    if (!parsed) return;
    specs.push(parsed.spec);
    return parsed.endIndex - 1;
  });

  return { specs, quote };
}

function readHeredocSpec(
  line: string,
  startIndex: number,
): { spec: HeredocSpec; endIndex: number } | undefined {
  let index = startIndex;
  const stripLeadingTabs = line[index] === "-";
  if (stripLeadingTabs) index += 1;
  while (/\s/u.test(line[index] ?? "")) index += 1;
  const delimiter = readShellWord(
    line,
    index,
    character => /[\s;|&<>]/u.test(character),
  );
  if (!delimiter?.value) return undefined;
  return {
    spec: { delimiter: delimiter.value, stripLeadingTabs },
    endIndex: delimiter.endIndex,
  };
}

function shellCommandSegments(command: string): string[] {
  const segments: string[] = [];
  let start = 0;
  scanUnquotedShellCharacters(command, "", (character, index) => {
    const isRedirectAmpersand = character === "&" &&
      (command[index - 1] === ">" || command[index - 1] === "<" ||
        command[index + 1] === ">");
    const isBoundary = character === ";" || character === "|" ||
      character === "\n" || (character === "&" && !isRedirectAmpersand);
    if (!isBoundary) return;
    const segment = command.slice(start, index).trim();
    if (segment) segments.push(segment);
    let endIndex = index;
    while (command[endIndex + 1] === character) endIndex += 1;
    start = endIndex + 1;
    return endIndex;
  });

  const finalSegment = command.slice(start).trim();
  if (finalSegment) segments.push(finalSegment);
  return segments;
}

function firstShellWord(segment: string): string | undefined {
  let index = 0;
  while (index < segment.length) {
    while (/\s/u.test(segment[index] ?? "")) index += 1;
    if (index >= segment.length || segment[index] === "#") return undefined;
    const word = readShellWord(segment, index, character => /\s/u.test(character));
    if (!word) return undefined;
    index = word.endIndex;
    if (!/^[A-Za-z_][A-Za-z0-9_]*=/u.test(word.value)) {
      return word.value || undefined;
    }
  }
  return undefined;
}

function scanUnquotedShellCharacters(
  source: string,
  initialQuote: ShellQuote,
  visit: (character: string, index: number) => number | void,
): ShellQuote {
  let quote = initialQuote;
  let escaped = false;
  let comment = false;
  let atWordStart = !quote;

  for (let index = 0; index < source.length; index += 1) {
    const character = source[index]!;
    if (comment) {
      if (character !== "\n") continue;
      comment = false;
    }
    if (escaped) {
      escaped = false;
      atWordStart = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      atWordStart = false;
      continue;
    }
    if (quote) {
      if (character === quote) quote = "";
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      atWordStart = false;
      continue;
    }
    if (character === "#" && atWordStart) {
      comment = true;
      continue;
    }

    const nextIndex = visit(character, index);
    if (typeof nextIndex === "number") index = nextIndex;
    atWordStart = /[\s;|&()<>]/u.test(character);
  }

  return quote;
}

function readShellWord(
  source: string,
  startIndex: number,
  isTerminator: (character: string) => boolean,
): ShellWord | undefined {
  let quote: ShellQuote = "";
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
    if (isTerminator(character)) break;
    value += character;
  }

  return quote ? undefined : { value, endIndex: index };
}
