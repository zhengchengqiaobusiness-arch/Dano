import { parse } from "unbash";
import type { Command, WordPart } from "unbash";

export type SafeBashCommandSummary =
  | { kind: "commands"; executableNames: string[] }
  | { kind: "script" };

export function safeBashCommandSummary(command: string): SafeBashCommandSummary {
  const script = parse(command);
  if (script.errors?.length) return { kind: "script" };

  const executableNames: string[] = [];
  collectExecutableNames(script, executableNames);
  return executableNames.length
    ? { kind: "commands", executableNames }
    : { kind: "script" };
}

function collectExecutableNames(value: unknown, names: string[]): void {
  if (Array.isArray(value)) {
    for (const item of value) collectExecutableNames(item, names);
    return;
  }
  if (!value || typeof value !== "object") return;

  const node = value as Record<string, unknown>;
  if (node.type === "Command") {
    const name = staticExecutableName(node as unknown as Command);
    if (name) names.push(name);
  }
  for (const child of Object.values(node)) collectExecutableNames(child, names);
}

function staticExecutableName(command: Command): string | undefined {
  if (!command.name || !isStaticWord(command.name.parts)) return undefined;

  const name = command.name.value.replace(/^.*[\\/]/u, "");
  return /^[\w.+-]+$/u.test(name) ? name : undefined;
}

function isStaticWord(parts: WordPart[] | undefined): boolean {
  if (!parts) return true;

  return parts.every(part => {
    if (
      part.type === "Literal" ||
      part.type === "SingleQuoted" ||
      part.type === "AnsiCQuoted"
    ) return true;

    return part.type === "DoubleQuoted" &&
      part.parts.every(child => child.type === "Literal");
  });
}
