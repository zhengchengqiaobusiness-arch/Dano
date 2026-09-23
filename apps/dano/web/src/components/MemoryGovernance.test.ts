/** @vitest-environment happy-dom */
import { mount, tick, unmount } from "svelte";
import { afterEach, expect, it, vi } from "vitest";
import MemoryGovernance from "./MemoryGovernance.svelte";

let component: ReturnType<typeof mount> | undefined;
afterEach(async () => {
  if (component) await unmount(component);
  component = undefined; document.body.replaceChildren(); vi.unstubAllGlobals();
});

it("keeps governance actions disabled while a complete export is in progress", async () => {
  let exportCalls = 0;
  let resolveExport!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url === "/memory/governance") return Promise.resolve(Response.json({ pending: null }));
    if (url.includes("/memory/export")) {
      exportCalls++;
      if (exportCalls === 1) return Promise.resolve(Response.json({ items: [{
        uri: "viking://user/test/memories/fact.md", content: "test fact", sources: [], revisions: [],
      }] }));
      return new Promise<Response>(resolve => { resolveExport = resolve; });
    }
    throw new Error("Unexpected request");
  }));
  const shell = document.createElement("div"); shell.className = "app-shell"; document.body.append(shell);
  component = mount(MemoryGovernance, { target: shell,
    props: { url: "/memory/governance", exportUrl: "/memory/export" } });
  const button = (label: string) => [...document.querySelectorAll("button")]
    .find(node => node.textContent?.includes(label))!;
  await vi.waitFor(() => expect(button("下载全部记忆 JSON")).toBeTruthy());
  button("管理这条记忆").click(); await tick();
  const selection = document.querySelector<HTMLTextAreaElement>("#selected-memory-text")!;
  selection.value = "test fact"; selection.dispatchEvent(new Event("input", { bubbles: true }));
  await tick();
  button("下载全部记忆 JSON").click();
  await vi.waitFor(() => expect(exportCalls).toBe(2));
  expect(button("遗忘所选内容").disabled).toBe(true);
  expect(button("清空全部长期记忆").disabled).toBe(true);
  await unmount(component); component = undefined;
  resolveExport(Response.json({ items: [] }));
});
