/** @vitest-environment happy-dom */
import { mount, tick, unmount } from "svelte";
import { afterEach, expect, it, vi } from "vitest";
import ScheduledTasksDialog from "./ScheduledTasksDialog.svelte";

const components: ReturnType<typeof mount>[] = [];
afterEach(async () => {
  for (const component of components.splice(0)) await unmount(component);
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

async function render(open = true, tasksUrl: string | null = "/api/clients/c1/scheduled-tasks") {
  const shell = document.createElement("div");
  shell.className = "app-shell";
  document.body.append(shell);
  const component = mount(ScheduledTasksDialog, {
    target: shell,
    props: {
      open,
      tasksUrl,
      sessionPath: "/sessions/a.jsonl",
      sessionId: "session-a",
    },
  });
  components.push(component);
  await tick();
  return component;
}

it("lets anonymous users open scheduled tasks", async () => {
  const fetch = vi.fn(() =>
    Promise.resolve(Response.json({ tasks: [] })),
  );
  vi.stubGlobal("fetch", fetch);
  await render();
  expect(document.body.textContent).toContain("定时任务");
  expect(document.body.textContent).not.toContain("请先登录");
  await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
});

it("does not load scheduled tasks while the dialog is closed", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  await render(false);
  expect(fetch).not.toHaveBeenCalled();
});

it("offers a custom interval and shows a 10-minute task as every 10 minutes", async () => {
  const fetch = vi.fn((input: RequestInfo) => {
    const url = String(input);
    if (url.endsWith("/runs")) return Promise.resolve(Response.json({ runs: [] }));
    return Promise.resolve(
      Response.json({
        tasks: [
          {
            taskId: "t1",
            taskName: "三国人物",
            enabled: true,
            nextRunAt: "2099-01-01T08:00:00.000Z",
            repeat: "interval",
            intervalMs: 600_000,
            destination: "standalone",
            payload: { userInput: "发一位三国人物" },
          },
        ],
      }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  await render();
  await vi.waitFor(() => expect(document.body.textContent).toContain("新建自动化"));
  await vi.waitFor(() => expect(document.body.textContent).toContain("每 10 分钟"));
  await vi.waitFor(() => expect(document.body.textContent).toContain("已激活"));
  expect(document.body.textContent).not.toContain("模板");
  expect(document.body.textContent).not.toContain("添加");
  const create = [...document.body.querySelectorAll("button")].find((button) =>
    button.textContent?.includes("新建自动化"),
  );
  create?.click();
  await tick();
  await vi.waitFor(() => expect(document.body.textContent).toContain("创建"));
});
