/** @vitest-environment happy-dom */
import { mount, tick, unmount } from "svelte";
import { afterEach, expect, it, vi } from "vitest";
import MemorySettingsDialog from "./MemorySettingsDialog.svelte";
const status = { enabled: false, automaticCollection: false, effectiveAt: "2026-09-18T00:00:00Z", policyVersion: "v1", revision: 1 };
const components: ReturnType<typeof mount>[] = [];
afterEach(async () => {
  for (const component of components.splice(0)) await unmount(component);
  document.body.replaceChildren(); vi.unstubAllGlobals();
});
async function render(authenticated = true) {
  const shell = document.createElement("div"); shell.className = "app-shell"; document.body.append(shell);
  const component = mount(MemorySettingsDialog, { target: shell, props: {
    open: true, authenticated, url: "/api/clients/test/memory/settings",
  } });
  components.push(component); await tick(); return { shell, component };
}
function button(text: string) {
  return [...document.querySelectorAll("button")].find(node => node.textContent?.includes(text))!;
}
it("does not request settings for anonymous users", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await render(false);
  expect(document.body.textContent).toContain("请先登录");
  expect(fetch).not.toHaveBeenCalled();
});
it("requires explicit enable and displays only confirmed server state", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(status))
    .mockResolvedValueOnce(Response.json({ ...status, enabled: true, revision: 2 }))
    .mockResolvedValueOnce(Response.json({ ...status, revision: 3 }));
  vi.stubGlobal("fetch", fetch);
  await render();
  await vi.waitFor(() => expect(button("同意并启用")).toBeDefined());
  expect(fetch).toHaveBeenCalledTimes(1);
  button("同意并启用").click();
  await vi.waitFor(() => expect(button("暂停长期记忆")).toBeDefined());
  expect(fetch.mock.calls[1]?.[1]).toMatchObject({ method: "PUT", body: '{"enabled":true}' });
  button("暂停长期记忆").click();
  await vi.waitFor(() => expect(button("同意并启用")).toBeDefined());
  expect(fetch.mock.calls[2]?.[1]).toMatchObject({ method: "PUT", body: '{"enabled":false}' });
});
it("does not retry or claim success when a mutation response is lost", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(status)).mockRejectedValueOnce(new Error("private network detail"));
  vi.stubGlobal("fetch", fetch);
  await render();
  await vi.waitFor(() => expect(button("同意并启用")).toBeDefined());
  button("同意并启用").click();
  await vi.waitFor(() => expect(button("刷新状态")).toBeDefined());
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(button("同意并启用")).toBeUndefined();
  expect(document.body.textContent).not.toContain("private network detail");
});
it("aborts an unfinished request when the dialog is destroyed", async () => {
  let signal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn((_url, options) => { signal = options.signal; return new Promise(() => {}); }));
  const { component } = await render();
  await vi.waitFor(() => expect(signal).toBeDefined());
  await unmount(component); components.splice(components.indexOf(component), 1);
  expect(signal!.aborted).toBe(true);
});
