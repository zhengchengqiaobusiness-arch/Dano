import { createInterface } from "node:readline";
import { extractEnumCandidates } from "./ast_enum_extractor.mjs";

// JSON-lines protocol keeps the worker isolated and stateless.  Source is
// parsed as text only; it is never evaluated, imported, or written to disk.
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });

for await (const line of lines) {
  if (!line.trim()) continue;
  let id = null;
  try {
    const request = JSON.parse(line);
    id = request.id ?? null;
    const result = extractEnumCandidates(request.source ?? "");
    process.stdout.write(`${JSON.stringify({ id, ...result })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      id,
      status: "error",
      candidates: [],
      error: error instanceof Error ? error.message : String(error),
    })}\n`);
  }
}
