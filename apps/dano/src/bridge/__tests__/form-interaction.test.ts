import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import type { AskUserQuestionConfirmationForm } from "@dano/types/protocol";
import { afterEach, describe, expect, it } from "vitest";
import {
  createFormInteraction,
  interruptAwaitingFormInteractions,
  readFormInteractions,
  transitionFormInteraction,
} from "../form-interaction.js";

const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

function sessionManager(): SessionManager {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dano-form-interaction-"));
  temporaryDirectories.push(directory);
  const manager = SessionManager.create(directory, directory);
  manager.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "confirming" }],
    timestamp: Date.now(),
    provider: "test",
    model: "test",
    api: "test",
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        total: 0,
      },
    },
    stopReason: "toolUse",
  } as any);
  return manager;
}

const forms: AskUserQuestionConfirmationForm[] = [
  {
    formId: "form-a",
    title: "请假申请",
    questions: [{ id: "reason", kind: "text" as const, question: "原因？" }],
    answer: { reason: "家庭事务" },
  },
  {
    formId: "form-b",
    title: "出差申请",
    questions: [
      { id: "destination", kind: "text" as const, question: "目的地？" },
    ],
    answer: { destination: "上海" },
  },
];

describe("Form Interaction", () => {
  it("persists one authoritative awaiting snapshot and terminal transition", () => {
    const manager = sessionManager();
    const awaiting = createFormInteraction(manager, {
      interactionId: "confirm-two",
      assistantTurnId: "assistant-turn-1",
      forms,
    });

    expect(awaiting).toMatchObject({
      interactionId: "confirm-two",
      assistantTurnId: "assistant-turn-1",
      state: "awaiting_confirmation",
      revision: 1,
      forms: [
        { formId: "form-a", revision: 1 },
        { formId: "form-b", revision: 1 },
      ],
    });

    const confirmed = transitionFormInteraction(manager, "confirm-two", {
      type: "confirm",
    });
    expect(confirmed).toMatchObject({
      kind: "transitioned",
      snapshot: {
        state: "confirmed",
        revision: 2,
      },
    });
    expect(readFormInteractions(manager.getBranch()).get("confirm-two")).toMatchObject({
      state: "confirmed",
      revision: 2,
    });

    expect(
      transitionFormInteraction(manager, "confirm-two", { type: "cancel" }),
    ).toMatchObject({
      kind: "already_terminal",
      snapshot: { state: "confirmed", revision: 2 },
    });
  });

  it("atomically interrupts every awaiting interaction after restart", () => {
    const manager = sessionManager();
    createFormInteraction(manager, {
      interactionId: "confirm-two",
      assistantTurnId: "assistant-turn-1",
      forms,
    });

    const sessionFile = manager.getSessionFile();
    expect(sessionFile).toBeTruthy();
    const restartedManager = SessionManager.open(sessionFile!);

    expect(interruptAwaitingFormInteractions(restartedManager)).toHaveLength(1);
    expect(readFormInteractions(restartedManager.getBranch()).get("confirm-two")).toMatchObject({
      state: "interrupted",
      revision: 2,
      forms: [
        { formId: "form-a", revision: 1 },
        { formId: "form-b", revision: 1 },
      ],
    });
  });
});
