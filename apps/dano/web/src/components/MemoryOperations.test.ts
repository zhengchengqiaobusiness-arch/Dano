/** @vitest-environment happy-dom */
import { mount, tick, unmount } from "svelte";
import { createSubscriber } from "svelte/reactivity";
import { afterEach, expect, it, vi } from "vitest";
import MemoryOperations from "./MemoryOperations.svelte";
const components: ReturnType<typeof mount>[] = [];
afterEach(async () => {
  for (const component of components.splice(0)) await unmount(component);
  document.body.replaceChildren(); vi.unstubAllGlobals();
});
function record(id: string, phase: string) {
  return { id, phase, createdAt: "2026-09-18T00:00:00Z", updatedAt: "2026-09-18T00:00:01Z",
    source: { sessionId: `session-${id}`, entryId: "entry", branchId: "branch" } };
}
async function render() {
  const component = mount(MemoryOperations, { target: document.body, props: { url: "/memory/operations" } });
  components.push(component); await tick(); return component;
}
const button = (text: string) => [...document.querySelectorAll("button")].find(node => node.textContent?.includes(text))!;
it("distinguishes unresolved delivery from ready and loads older receipts without mutation", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json({ items: [record("a", "commit_unknown")], nextCursor: "next" }))
    .mockResolvedValueOnce(Response.json({ items: [record("b", "ready")], nextCursor: null }));
  vi.stubGlobal("fetch", fetch); await render();
  await vi.waitFor(() => expect(document.body.textContent).toContain("结果待核实"));
  expect(document.querySelector("li")!.textContent).not.toContain("已记住");
  button("加载更早").click();
  await vi.waitFor(() => expect(document.querySelectorAll("li")).toHaveLength(2));
  expect(document.body.textContent).toContain("session-b");
  expect(fetch.mock.calls[1]![0]).toBe("/memory/operations?cursor=next");
  expect(fetch.mock.calls.every(call => call[1].method === undefined)).toBe(true);
});
it("clears stale records on refresh and renders only a generic failure", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json({ items: [record("a", "processing")], nextCursor: null }))
    .mockRejectedValueOnce(new Error("PRIVATE_SERVICE_ERROR"));
  vi.stubGlobal("fetch", fetch); await render();
  await vi.waitFor(() => expect(document.querySelectorAll("li")).toHaveLength(1));
  button("刷新状态").click();
  await vi.waitFor(() => expect(document.body.textContent).toContain("无法读取最新记录"));
  expect(document.querySelectorAll("li")).toHaveLength(0);
  expect(document.body.textContent).not.toContain("PRIVATE_SERVICE_ERROR");
});
it("aborts and ignores late receipts after its owner view is removed", async () => {
  let signal: AbortSignal | undefined;
  let finish!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((_url, options) => {
    signal = options.signal; return new Promise<Response>(resolve => { finish = resolve; });
  }));
  const component = await render();
  await unmount(component); components.splice(components.indexOf(component), 1);
  expect(signal!.aborted).toBe(true);
  finish(Response.json({ items: [record("old-owner", "ready")], nextCursor: null }));
  await tick();
  expect(document.body.textContent).not.toContain("old-owner");
});

it("discards the previous owner's delayed response when the bound client changes", async () => {
  let currentUrl = "/alice/operations";
  let changed!: () => void;
  const subscribe = createSubscriber(update => { changed = update; });
  let oldSignal: AbortSignal | undefined;
  let finish!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((url, options) => {
    if (url === "/alice/operations") {
      oldSignal = options.signal; return new Promise<Response>(resolve => { finish = resolve; });
    }
    return Promise.resolve(Response.json({ items: [record("bob", "processing")], nextCursor: null }));
  }));
  const component = mount(MemoryOperations, { target: document.body, props: {
    get url() { subscribe(); return currentUrl; },
  } });
  components.push(component); await tick();
  currentUrl = "/bob/operations"; changed(); await tick();
  expect(oldSignal!.aborted).toBe(true);
  await vi.waitFor(() => expect(document.body.textContent).toContain("session-bob"));
  finish(Response.json({ items: [record("alice", "ready")], nextCursor: null }));
  await tick();
  expect(document.body.textContent).not.toContain("session-alice");
});
