import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import type { AskUserQuestionConfirmationForm } from "@dano/types/protocol";
import { afterEach, describe, expect, it } from "vitest";
import {
  createFormInteraction,
  interruptOpenFormInteractions,
  projectFormInteractionsInMessage,
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

    expect(interruptOpenFormInteractions(restartedManager)).toHaveLength(1);
    expect(readFormInteractions(restartedManager.getBranch()).get("confirm-two")).toMatchObject({
      state: "interrupted",
      revision: 2,
      forms: [
        { formId: "form-a", revision: 1 },
        { formId: "form-b", revision: 1 },
      ],
    });
  });

  it("projects editable revisions and preserves unchanged form answers on submit", () => {
    const manager = sessionManager();
    createFormInteraction(manager, {
      interactionId: "confirm-two",
      assistantTurnId: "assistant-turn-1",
      forms,
    });

    const revising = transitionFormInteraction(manager, "confirm-two", {
      type: "return_modify",
    });
    expect(revising).toMatchObject({
      kind: "transitioned",
      snapshot: {
        state: "revising",
        revision: 2,
        forms: [
          { formId: "form-a", revision: 2, answer: { reason: "家庭事务" } },
          { formId: "form-b", revision: 2, answer: { destination: "上海" } },
        ],
      },
    });

    const sessionFile = manager.getSessionFile();
    expect(sessionFile).toBeTruthy();
    const reloaded = SessionManager.open(sessionFile!);
    expect(readFormInteractions(reloaded.getBranch()).get("confirm-two"))
      .toMatchObject({
        state: "revising",
        revision: 2,
        forms: [
          { formId: "form-a", revision: 2, answer: { reason: "家庭事务" } },
          { formId: "form-b", revision: 2, answer: { destination: "上海" } },
        ],
      });

    const submitted = transitionFormInteraction(reloaded, "confirm-two", {
      type: "submit_revision",
      forms: [{ ...forms[0], answer: { reason: "照顾家人" } }],
    });
    expect(submitted).toMatchObject({
      kind: "transitioned",
      snapshot: {
        state: "awaiting_confirmation",
        revision: 3,
        forms: [
          { formId: "form-a", revision: 2, answer: { reason: "照顾家人" } },
          { formId: "form-b", revision: 2, answer: { destination: "上海" } },
        ],
      },
    });
  });

  it("discards a draft revision and returns to confirmation", () => {
    const manager = sessionManager();
    createFormInteraction(manager, {
      interactionId: "confirm-two",
      assistantTurnId: "assistant-turn-1",
      forms,
    });
    transitionFormInteraction(manager, "confirm-two", { type: "return_modify" });

    expect(transitionFormInteraction(manager, "confirm-two", {
      type: "cancel_revision",
    })).toMatchObject({
      kind: "transitioned",
      snapshot: {
        state: "awaiting_confirmation",
        revision: 3,
        forms: [
          { formId: "form-a", answer: { reason: "家庭事务" } },
          { formId: "form-b", answer: { destination: "上海" } },
        ],
      },
    });
    expect(
      transitionFormInteraction(manager, "confirm-two", { type: "cancel" }),
    ).toMatchObject({
      kind: "transitioned",
      snapshot: { state: "cancelled", revision: 4 },
    });
  });

  it("interrupts a persisted revising interaction on process restart", () => {
    const manager = sessionManager();
    createFormInteraction(manager, {
      interactionId: "confirm-two",
      assistantTurnId: "assistant-turn-1",
      forms,
    });
    transitionFormInteraction(manager, "confirm-two", { type: "return_modify" });
    const sessionFile = manager.getSessionFile();
    expect(sessionFile).toBeTruthy();
    const restartedManager = SessionManager.open(sessionFile!);

    expect(interruptOpenFormInteractions(restartedManager)).toHaveLength(1);
    expect(readFormInteractions(restartedManager.getBranch()).get("confirm-two"))
      .toMatchObject({
        state: "interrupted",
        revision: 3,
        forms: [
          { formId: "form-a", revision: 2 },
          { formId: "form-b", revision: 2 },
        ],
      });
  });

  it("reconstructs a read-only confirmation card from the persisted interaction", () => {
    const manager = sessionManager();
    createFormInteraction(manager, {
      interactionId: "confirm-two",
      assistantTurnId: "assistant-turn-1",
      forms,
    });
    interruptOpenFormInteractions(manager);
    const projected = projectFormInteractionsInMessage(
      {
        id: "assistant-turn-1",
        role: "assistant",
        content: [{
          type: "toolCall",
          id: "confirm-two",
          name: "ask_user_question",
          arguments: { confirm: true, formIds: ["form-a", "form-b"] },
        }],
      },
      readFormInteractions(manager.getBranch()),
    );
    const block = projected.content?.[0];

    expect(typeof block === "string" ? null : block).toMatchObject({
      formInteraction: {
        state: "interrupted",
        allowedActions: [],
      },
      questionRequest: {
        batch: false,
        kind: "confirm",
        title: "确认 2 份表单",
        forms: [
          { formId: "form-a", answer: { reason: "家庭事务" } },
          { formId: "form-b", answer: { destination: "上海" } },
        ],
      },
    });
  });
});
