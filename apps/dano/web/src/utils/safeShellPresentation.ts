import { parse } from "unbash";
import type { Command, Node, WordPart } from "unbash";

export type SafeBashCommandSummary =
  | { kind: "commands"; executableNames: string[] }
  | { kind: "script" };

export function safeBashCommandSummary(command: string): SafeBashCommandSummary {
  const script = parse(command);
  if (script.errors?.length || !script.commands.length) return { kind: "script" };

  const executableNames: string[] = [];
  for (const statement of script.commands) {
    if (statement.redirects.length || !collectExecutableNames(statement.command, executableNames)) {
      return { kind: "script" };
    }
  }

  return executableNames.length
    ? { kind: "commands", executableNames }
    : { kind: "script" };
}

function collectExecutableNames(node: Node, names: string[]): boolean {
  if (node.type === "Command") {
    const name = staticExecutableName(node);
    if (!name) return false;
    names.push(name);
    return true;
  }

  if (node.type === "Pipeline" || node.type === "AndOr") {
    return node.commands.every(command => collectExecutableNames(command, names));
  }

  return false;
}

function staticExecutableName(command: Command): string | undefined {
  if (command.redirects.length || !command.name || !isStaticWord(command.name.parts)) {
    return undefined;
  }

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
