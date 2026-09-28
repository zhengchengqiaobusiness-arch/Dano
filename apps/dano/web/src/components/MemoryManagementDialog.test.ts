/** @vitest-environment happy-dom */
import { mount, tick, unmount } from "svelte";
import { afterEach, expect, it, vi } from "vitest";
import MemoryManagementDialog from "./MemoryManagementDialog.svelte";

const components: ReturnType<typeof mount>[] = [];
afterEach(async () => {
  for (const component of components.splice(0)) await unmount(component);
  document.body.replaceChildren(); vi.unstubAllGlobals();
});
async function render(authenticated = true, open = true) {
  const shell = document.createElement("div"); shell.className = "app-shell"; document.body.append(shell);
  const component = mount(MemoryManagementDialog, { target: shell, props: {
    open, authenticated, operationsUrl: "/memory/operations", governanceUrl: "/memory/governance", exportUrl: "/memory/export",
  } });
  components.push(component); await tick(); return component;
}

it("does not load memory for anonymous users", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await render(false);
  expect(document.body.textContent).toContain("请先登录");
  expect(fetch).not.toHaveBeenCalled();
});

it("offers memory management without settings requests or manual configuration controls", async () => {
  const fetch = vi.fn((url: string, _options?: RequestInit) => Promise.resolve(Response.json(url === "/memory/governance"
    ? { pending: null } : url === "/memory/operations" ? { items: [], nextCursor: null } : { items: [] })));
  vi.stubGlobal("fetch", fetch);
  await render();
  await vi.waitFor(() => expect(document.body.textContent).toContain("下载全部记忆 JSON"));
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("记忆管理");
  expect(document.body.textContent).toContain("保存记录");
  const labels = [...document.querySelectorAll("button")].map(button => button.textContent?.trim());
  expect(labels).toContain("清空全部长期记忆");
  for (const label of ["启用长期记忆", "暂停长期记忆", "启用自动采集", "关闭自动采集", "单独同意"]) {
    expect(labels.some(text => text?.includes(label))).toBe(false);
  }
  expect(fetch.mock.calls.every(([url, ...rest]) => !url.includes("settings")
    && !(rest[0] as RequestInit | undefined)?.method)).toBe(true);
});

it("does not load management data while the dialog is closed", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await render(true, false);
  expect(fetch).not.toHaveBeenCalled();
});
