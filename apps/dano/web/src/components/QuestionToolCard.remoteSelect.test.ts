// @vitest-environment happy-dom

import type { AskUserQuestionCardRequest, RpcResponse } from "@dano/types/protocol";
import { mount, tick, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ToolContentBlock } from "../utils/transcript";
import QuestionToolCard from "./QuestionToolCard.svelte";

const questionRequest: AskUserQuestionCardRequest = {
  batch: false,
  id: "city",
  kind: "select",
  question: "请选择所在城市：",
  options: [],
  dataSource: {
    type: "api",
    endpoint: "/api/cities",
    searchParam: "query",
    idField: "id",
    labelField: "name",
  },
};

const block: ToolContentBlock = {
  kind: "tool",
  toolName: "ask_user_question",
  toolCallId: "call-city",
  toolArgs: {},
  questionRequest,
  argumentsText: "",
  toolStatus: "pending",
};

let mountedCard: Record<string, unknown> | undefined;

function successfulResponse(): Promise<RpcResponse> {
  return Promise.resolve({
    type: "response",
    command: "present_question",
    success: true,
    data: null,
  });
}

function mountCard(onRespond = vi.fn(successfulResponse)) {
  document.body.innerHTML = '<div class="app-shell"><div id="root"></div></div>';
  const target = document.querySelector<HTMLElement>("#root");
  if (!target) throw new Error("missing test root");
  mountedCard = mount(QuestionToolCard, {
    target,
    props: {
      block,
      active: true,
      onPresent: successfulResponse,
      onRespond,
      onRevise: successfulResponse,
      onSubmitRevision: successfulResponse,
    },
  });
  return onRespond;
}

async function advance(ms: number) {
  await vi.advanceTimersByTimeAsync(ms);
  await tick();
}

function comboboxTrigger(): HTMLButtonElement {
  const trigger = document.querySelector<HTMLButtonElement>('button[role="combobox"]');
  if (!trigger) throw new Error("missing combobox trigger");
  return trigger;
}

function commandOption(label: string): HTMLElement {
  const option = Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))
    .find(candidate => candidate.textContent?.trim() === label);
  if (!option) throw new Error(`missing option: ${label}`);
  return option;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(async () => {
  if (mountedCard) await unmount(mountedCard);
  mountedCard = undefined;
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("QuestionToolCard remote select", () => {
  it("shows a friendly error inside the combobox instead of the HTTP status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
    mountCard();

    await advance(401);
    comboboxTrigger().click();
    await tick();

    expect(document.querySelector('[role="alert"]')?.textContent).toContain("选项加载失败");
    expect(document.body.textContent).not.toContain("HTTP 404");
  });

  it("searches, selects by keyboard, clears, and submits the remote option id", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = new URL(String(input), window.location.origin);
      const rows = url.searchParams.get("query") === "上"
        ? [{ id: 310000, name: "上海市" }]
        : [
            { id: 110000, name: "北京市" },
            { id: 310000, name: "上海市" },
          ];
      return new Response(JSON.stringify(rows), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onRespond = mountCard();

    await advance(401);
    comboboxTrigger().click();
    await tick();
    const search = document.querySelector<HTMLInputElement>('input[placeholder="搜索..."]');
    if (!search) throw new Error("missing search input");
    search.value = "上";
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await advance(301);
    search.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await tick();

    expect(comboboxTrigger().textContent).toContain("上海市");
    comboboxTrigger().click();
    await tick();
    expect(commandOption("上海市").hasAttribute("data-committed-selected")).toBe(true);
    commandOption("清除选择").click();
    await tick();
    expect(comboboxTrigger().textContent).toContain("请选择...");

    comboboxTrigger().click();
    await advance(1);
    commandOption("上海市").click();
    await tick();
    document.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
    await tick();

    expect(onRespond).toHaveBeenCalledWith("call-city", {
      cancelled: false,
      answer: 310000,
    });
  });
});
