/** @vitest-environment happy-dom */
import { mount, tick, unmount } from "svelte";
import { afterEach, expect, it, vi } from "vitest";
import MemoryContent from "./MemoryContent.svelte";
const components: ReturnType<typeof mount>[] = [];
afterEach(async () => {
  for (const component of components.splice(0)) await unmount(component);
  document.body.replaceChildren(); vi.unstubAllGlobals();
});
async function render() {
  const component = mount(MemoryContent, { target: document.body, props: { url: "/memory/operations", operationId: "receipt" } });
  components.push(component); await tick(); return component;
}
const button = (text: string) => [...document.querySelectorAll("button")].find(node => node.textContent?.includes(text))!;
it("shows extracted text without interpreting HTML and pages through receipt-bound content", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json({ operationId: "receipt", index: 0, total: 2,
    text: '<img src="x" onerror="alert(1)">quoted fact' }))
    .mockResolvedValueOnce(Response.json({ operationId: "receipt", index: 1, total: 2, text: "second fact" }));
  vi.stubGlobal("fetch", fetch); await render();
  await vi.waitFor(() => expect(document.body.textContent).toContain("quoted fact"));
  expect(document.querySelector("img")).toBeNull();
  expect(button("上一条").disabled).toBe(true);
  button("下一条").click();
  await vi.waitFor(() => expect(document.body.textContent).toContain("second fact"));
  expect(document.body.textContent).not.toContain("quoted fact");
  expect(button("下一条").disabled).toBe(true);
  expect(fetch.mock.calls[1]![0]).toBe("/memory/operations/receipt/content/1");
  expect(fetch.mock.calls.every(call => call[1].cache === "no-store" && call[1].method === undefined)).toBe(true);
});
it("rejects an incorrectly bound receipt without displaying its content", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ operationId: "other", index: 0, total: 1, text: "PRIVATE_BODY" })));
  await render();
  await vi.waitFor(() => expect(document.body.textContent).toContain("内容不可用"));
  expect(document.body.textContent).not.toContain("PRIVATE_BODY");
});
it("aborts content reads when hidden and ignores a late response", async () => {
  let signal: AbortSignal | undefined;
  let finish!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn((_url, options) => { signal = options.signal; return new Promise(resolve => { finish = resolve; }); }));
  const component = await render();
  await unmount(component); components.splice(components.indexOf(component), 1);
  expect(signal!.aborted).toBe(true);
  finish(Response.json({ operationId: "receipt", index: 0, total: 1, text: "late fact" }));
  await tick();
  expect(document.body.textContent).not.toContain("late fact");
});
