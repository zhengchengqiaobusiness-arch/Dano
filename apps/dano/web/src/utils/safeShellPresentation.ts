type HeredocSpec = {
  delimiter: string;
  stripLeadingTabs: boolean;
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
    pendingHeredocs.push(...heredocSpecs(line));
  }

  return visibleLines.join("\n");
}

function heredocSpecs(line: string): HeredocSpec[] {
  const specs: HeredocSpec[] = [];
  let quote = "";
  let escaped = false;

  for (let index = 0; index < line.length; index += 1) {
    const character = line[index]!;
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = "";
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (character === "#" && (index === 0 || /[\s;|&]/u.test(line[index - 1]!))) {
      break;
    }
    if (character !== "<" || line[index + 1] !== "<" || line[index + 2] === "<") {
      continue;
    }

    const parsed = readHeredocSpec(line, index + 2);
    if (!parsed) continue;
    specs.push(parsed.spec);
    index = parsed.endIndex - 1;
  }

  return specs;
}

function readHeredocSpec(
  line: string,
  startIndex: number,
): { spec: HeredocSpec; endIndex: number } | undefined {
  let index = startIndex;
  const stripLeadingTabs = line[index] === "-";
  if (stripLeadingTabs) index += 1;
  while (/\s/u.test(line[index] ?? "")) index += 1;

  const quote = line[index] === "'" || line[index] === '"'
    ? line[index]!
    : "";
  if (quote) index += 1;

  let delimiter = "";
  let escaped = false;
  for (; index < line.length; index += 1) {
    const character = line[index]!;
    if (escaped) {
      delimiter += character;
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) {
        index += 1;
        return delimiter
          ? { spec: { delimiter, stripLeadingTabs }, endIndex: index }
          : undefined;
      }
      delimiter += character;
      continue;
    }
    if (/[\s;|&<>]/u.test(character)) break;
    delimiter += character;
  }

  if (quote || !delimiter) return undefined;
  return { spec: { delimiter, stripLeadingTabs }, endIndex: index };
}

function shellCommandSegments(command: string): string[] {
  const segments: string[] = [];
  let start = 0;
  let quote = "";
  let escaped = false;

  for (let index = 0; index < command.length; index += 1) {
    const character = command[index]!;
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = "";
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    const isRedirectAmpersand = character === "&" &&
      (command[index - 1] === ">" || command[index - 1] === "<" ||
        command[index + 1] === ">");
    const isBoundary = character === ";" || character === "|" ||
      character === "\n" || (character === "&" && !isRedirectAmpersand);
    if (!isBoundary) continue;
    const segment = command.slice(start, index).trim();
    if (segment) segments.push(segment);
    while (command[index + 1] === character) index += 1;
    start = index + 1;
  }

  const finalSegment = command.slice(start).trim();
  if (finalSegment) segments.push(finalSegment);
  return segments;
}

function firstShellWord(segment: string): string | undefined {
  let index = 0;
  while (index < segment.length) {
    while (/\s/u.test(segment[index] ?? "")) index += 1;
    if (index >= segment.length || segment[index] === "#") return undefined;

    let word = "";
    let quote = "";
    let escaped = false;
    for (; index < segment.length; index += 1) {
      const character = segment[index]!;
      if (escaped) {
        word += character;
        escaped = false;
        continue;
      }
      if (character === "\\" && quote !== "'") {
        escaped = true;
        continue;
      }
      if (quote) {
        if (character === quote) quote = "";
        else word += character;
        continue;
      }
      if (character === "'" || character === '"') {
        quote = character;
        continue;
      }
      if (/\s/u.test(character)) break;
      word += character;
    }
    if (quote) return undefined;
    if (!/^[A-Za-z_][A-Za-z0-9_]*=/u.test(word)) return word || undefined;
  }
  return undefined;
}
