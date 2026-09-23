/** @vitest-environment happy-dom */
import { afterEach, expect, it, vi } from "vitest";
import { exportAllMemories, startGovernance } from "./memoryGovernance";

afterEach(() => vi.unstubAllGlobals());

function item(index: number) {
  return { uri: `viking://user/test/memories/${index}.md`, content: `fact-${index}`,
    sources: [], revisions: [] };
}

it("exports every page from the server rather than only the displayed first page", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json({ items: [item(1)], nextCursor: "next" }))
    .mockResolvedValueOnce(Response.json({ items: [item(2)] }));
  vi.stubGlobal("fetch", fetch);
  const result = await exportAllMemories("/memory/export", new AbortController().signal);
  expect(result.items.map(memory => memory.content)).toEqual(["fact-1", "fact-2"]);
  expect(result.nextCursor).toBeUndefined();
  expect(new URL(fetch.mock.calls[1]![0]).searchParams.get("cursor")).toBe("next");
});

it("fails instead of producing a partial export when pagination fails or loops", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json({ items: [item(1)], nextCursor: "next" }))
    .mockResolvedValueOnce(Response.json({ error: "MEMORY_EXPORT_CHANGED" }, { status: 409 }));
  vi.stubGlobal("fetch", fetch);
  await expect(exportAllMemories("/memory/export", new AbortController().signal)).rejects.toThrow();
  fetch.mockReset().mockImplementation(async () => Response.json({ items: [item(1)], nextCursor: "next" }));
  await expect(exportAllMemories("/memory/export", new AbortController().signal)).rejects.toThrow("MEMORY_EXPORT_UNAVAILABLE");
});

it("keeps the ambiguous-target code for management guidance without exposing arbitrary errors", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json({ error: "MEMORY_TARGET_AMBIGUOUS" }, { status: 400 }))
    .mockResolvedValueOnce(Response.json({ error: "PRIVATE_DETAIL" }, { status: 400 }));
  vi.stubGlobal("fetch", fetch);
  const action = { action: "forget" as const, memoryUri: "viking://user/test/memories/fact.md", selectedText: "fact" };
  await expect(startGovernance("/memory/governance", new AbortController().signal, action))
    .rejects.toThrow("MEMORY_TARGET_AMBIGUOUS");
  await expect(startGovernance("/memory/governance", new AbortController().signal, action))
    .rejects.toThrow("MEMORY_GOVERNANCE_UNAVAILABLE");
});
