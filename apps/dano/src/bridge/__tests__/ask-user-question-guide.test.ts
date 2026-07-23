import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { Value } from "typebox/value";
import {
  AskUserQuestionCoordinator,
  askUserQuestionResultSchema,
  normalizeAskUserQuestionCardRequest,
  normalizeAskUserQuestionCardRequestResult,
} from "../ask-user-question.js";
import type {
  AskUserQuestionAnswerInput,
  AskUserQuestionCardRequest,
  AskUserQuestionResult,
} from "../types.js";

const guide = readFileSync(
  new URL(
    "../../../../../docs/skill-generator-ask-user-question-guide.md",
    import.meta.url,
  ),
  "utf8",
);

const requiredCapabilities = [
  "call.single",
  "call.grouped",
  "call.confirmation",
  "text.single-line",
  "text.textarea",
  "field-assist.default-off",
  "field-assist.default-on",
  "field-assist.explicit-on",
  "field-assist.explicit-off",
  "date.date-only",
  "date.date-time-minute",
  "date.default-format",
  "date.result-string",
  "choice.radio",
  "choice.checkbox",
  "choice.select",
  "choice.tree-select",
  "choice.stable-option",
  "choice.default-id",
  "custom.single",
  "custom.multiple",
  "data-source.get",
  "data-source.post",
  "data-source.params",
  "data-source.search",
  "data-source.pagination",
  "data-source.result-path",
  "data-source.total-path",
  "data-source.id-label",
  "data-source.children",
  "data-source.extra-fields",
  "selection.single",
  "selection.multiple",
  "selection.multiple-default",
  "required.true",
  "required.false",
  "default.text",
  "default.date",
  "default.single-choice",
  "default.multiple-choice",
  "optional.cleared",
  "ownership.single-top-level",
  "ownership.grouped-item",
  "ownership.grouped-id-map",
  "result.single",
  "result.grouped",
  "result.form-id",
  "result.multiple",
  "result.date",
  "result.custom",
  "result.cancelled",
  "result.invalid",
  "confirmation.single-form",
  "confirmation.multiple-forms",
  "confirmation.latest-answer",
  "confirmation.return-modify",
  "confirmation.reconfirm",
  "confirmation.authoritative-forms",
  "failure.correct-all-paths",
  "failure.timeout-retry",
  "failure.presentation-stop",
  "failure.validation-stop",
  "failure.cancel-stop",
] as const;

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseTaggedBlocks(): Map<string, unknown> {
  const blocks = new Map<string, unknown>();
  const pattern =
    /<!--\s*example:([A-Z]\d{2})\s+kind:([a-z-]+)\s*-->\s*```json\n([\s\S]*?)\n```/g;
  for (const match of guide.matchAll(pattern)) {
    const key = `${match[1]}:${match[2]}`;
    if (blocks.has(key)) throw new Error(`Duplicate guide block: ${key}`);
    blocks.set(key, JSON.parse(match[3]));
  }
  return blocks;
}

function parseSchema(name: "request" | "result"): JsonRecord {
  const pattern = new RegExp(
    `<!--\\s*schema:${name}\\s*-->\\s*\`\`\`json\\n([\\s\\S]*?)\\n\`\`\``,
  );
  const match = guide.match(pattern);
  if (!match) throw new Error(`Missing ${name} schema block`);
  const schema = JSON.parse(match[1]);
  if (!isRecord(schema)) throw new Error(`${name} schema must be an object`);
  return schema;
}

function expandLocalRefs(value: unknown, root: JsonRecord): unknown {
  if (Array.isArray(value)) return value.map(item => expandLocalRefs(item, root));
  if (!isRecord(value)) return value;
  if (typeof value.$ref === "string") {
    const match = value.$ref.match(/^#\/\$defs\/([^/]+)$/);
    if (!match) throw new Error(`Unsupported schema ref: ${value.$ref}`);
    const definitions = root.$defs;
    if (!isRecord(definitions) || !(match[1] in definitions)) {
      throw new Error(`Missing schema definition: ${match[1]}`);
    }
    return expandLocalRefs(definitions[match[1]], root);
  }
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => key !== "$defs" && key !== "$schema")
      .map(([key, child]) => [key, expandLocalRefs(child, root)]),
  );
}

function block<T>(blocks: Map<string, unknown>, key: string): T {
  if (!blocks.has(key)) throw new Error(`Missing guide block: ${key}`);
  return blocks.get(key) as T;
}

async function submitGroupedExample(
  coordinator: AskUserQuestionCoordinator,
  signal: AbortSignal,
  blocks: Map<string, unknown>,
  exampleId: "E09" | "E10",
): Promise<void> {
  const result = block<Extract<AskUserQuestionResult, { status: "answered" }>>(
    blocks,
    `${exampleId}:result`,
  );
  if (!result.formId || !isRecord(result.answer)) {
    throw new Error(`${exampleId} must define a grouped answered result`);
  }
  const pending = coordinator.wait(
    result.formId,
    block<JsonRecord>(blocks, `${exampleId}:request`),
    signal,
  );
  coordinator.present(result.formId);
  coordinator.answer(result.formId, {
    cancelled: false,
    answer: result.answer as Record<string, AskUserQuestionAnswerInput>,
  });
  await expect(pending).resolves.toEqual(result);
}

describe("backend Skill-generator ask_user_question guide", () => {
  it("keeps every JSON fenced block parseable", () => {
    const jsonBlocks = [...guide.matchAll(/```json\n([\s\S]*?)\n```/g)];
    expect(jsonBlocks.length).toBeGreaterThan(0);
    for (const [index, match] of jsonBlocks.entries()) {
      expect(() => JSON.parse(match[1]), `JSON block ${index + 1}`).not.toThrow();
    }
  });

  it("keeps every positive request inside the strict canonical schema", () => {
    const blocks = parseTaggedBlocks();
    const requestSchema = parseSchema("request");
    const expandedSchema = expandLocalRefs(requestSchema, requestSchema);
    const requestEntries = [...blocks.entries()].filter(([key]) =>
      key.endsWith(":request") || key.endsWith(":confirmation-request")
    );
    expect(requestEntries.length).toBeGreaterThan(0);
    for (const [key, request] of requestEntries) {
      expect(Value.Check(expandedSchema as never, request), key).toBe(true);
    }
  });

  it.each([
    ["unknown field", { question: "标题？", default: "季度总结", prompt: "标题？" }],
    ["text-only field on date", {
      question: "日期？",
      inputType: "date",
      fieldAssist: true,
      dateFormat: "yyyy-MM-dd",
      default: "2021-06-18",
    }],
    ["date without dateFormat", {
      question: "日期？",
      inputType: "date",
      default: "2021-06-18",
    }],
    ["choice without source", {
      question: "部门？",
      inputType: "select",
      default: "dep-sales",
    }],
    ["grouped top-level field config", {
      title: "表单",
      required: true,
      questions: [{ id: "name", question: "姓名？", default: "张三" }],
    }],
    ["questions object", {
      title: "表单",
      questions: { id: "name", question: "姓名？", default: "张三" },
    }],
    ["confirmation mixed with question", {
      confirm: true,
      formIds: ["submitted-form-call"],
      question: "确认？",
    }],
  ])("rejects non-canonical schema shape: %s", (_name, request) => {
    const requestSchema = parseSchema("request");
    const expandedSchema = expandLocalRefs(requestSchema, requestSchema);
    expect(Value.Check(expandedSchema as never, request)).toBe(false);
  });

  it("projects every ordinary canonical request to its documented Card Request", () => {
    const blocks = parseTaggedBlocks();
    const requests = [...blocks.entries()].filter(([key]) => key.endsWith(":request"));
    for (const [key, request] of requests) {
      const exampleId = key.slice(0, 3);
      const expected = block<AskUserQuestionCardRequest>(blocks, `${exampleId}:card`);
      expect(
        normalizeAskUserQuestionCardRequest(request, { requireDefault: true }),
        exampleId,
      ).toEqual(expected);
    }
  });

  it("executes single-form revision and confirmation with the latest answer", async () => {
    const blocks = parseTaggedBlocks();
    const coordinator = new AskUserQuestionCoordinator();
    const controller = new AbortController();
    await submitGroupedExample(coordinator, controller.signal, blocks, "E09");

    const confirmation = coordinator.wait(
      "leave-confirmation-call",
      block<JsonRecord>(blocks, "E11:confirmation-request"),
      controller.signal,
    );
    expect(coordinator.cardRequest("leave-confirmation-call")).toEqual(
      block(blocks, "E11:confirmation-card"),
    );
    const sourceResult = block<Extract<AskUserQuestionResult, { status: "answered" }>>(
      blocks,
      "E09:result",
    );
    if (!sourceResult.formId) throw new Error("E09 must return formId");
    coordinator.submitConfirmationRevision("leave-confirmation-call", {
      [sourceResult.formId]: block<Record<string, AskUserQuestionAnswerInput>>(
        blocks,
        "E11:revision-answer",
      ),
    });
    expect(coordinator.cardRequest("leave-confirmation-call")).toEqual(
      block(blocks, "E11:revision-card"),
    );
    coordinator.answer("leave-confirmation-call", {
      cancelled: false,
      answer: true,
    });
    await expect(confirmation).resolves.toEqual(block(blocks, "E11:result"));
  });

  it("executes an atomic confirmation for multiple submitted forms", async () => {
    const blocks = parseTaggedBlocks();
    const coordinator = new AskUserQuestionCoordinator();
    const controller = new AbortController();
    await submitGroupedExample(coordinator, controller.signal, blocks, "E09");
    await submitGroupedExample(coordinator, controller.signal, blocks, "E10");

    const confirmation = coordinator.wait(
      "multi-form-confirmation-call",
      block<JsonRecord>(blocks, "E12:confirmation-request"),
      controller.signal,
    );
    expect(coordinator.cardRequest("multi-form-confirmation-call")).toEqual(
      block(blocks, "E12:confirmation-card"),
    );
    coordinator.answer("multi-form-confirmation-call", {
      cancelled: false,
      answer: true,
    });
    await expect(confirmation).resolves.toEqual(block(blocks, "E12:result"));
  });

  it("keeps the documented invalid call and one replacement call executable", () => {
    const blocks = parseTaggedBlocks();
    expect(
      normalizeAskUserQuestionCardRequestResult(
        block(blocks, "E13:invalid-request"),
        { requireDefault: true },
      ),
    ).toEqual({ error: block(blocks, "E13:failure") });
    expect(
      normalizeAskUserQuestionCardRequest(
        block(blocks, "E13:request"),
        { requireDefault: true },
      ),
    ).toEqual(block(blocks, "E13:card"));
  });

  it("keeps every documented result and failure aligned with the runtime schema", () => {
    const blocks = parseTaggedBlocks();
    const resultSchema = parseSchema("result");
    const expandedResultSchema = expandLocalRefs(resultSchema, resultSchema);
    const resultEntries = [...blocks.entries()].filter(([key]) =>
      key.endsWith(":result") || key.endsWith(":failure")
    );
    expect(resultEntries.length).toBeGreaterThan(0);
    for (const [key, result] of resultEntries) {
      expect(Value.Check(expandedResultSchema as never, result), key).toBe(true);
      expect(Value.Check(askUserQuestionResultSchema, result), key).toBe(true);
    }
    expect(block<JsonRecord>(blocks, "E14:failure")).toMatchObject({
      error: { code: "question_presentation_timeout", retryable: true },
    });
    expect(block<JsonRecord>(blocks, "E15:failure")).toMatchObject({
      error: { code: "question_presentation_failed", retryable: false },
    });
    expect(block<JsonRecord>(blocks, "E16:failure")).toMatchObject({
      error: { code: "question_validation_failed", retryable: false },
    });
    expect(block<JsonRecord>(blocks, "E17:result")).toEqual({ status: "cancelled" });
  });

  it("links every required capability to at least one executable example", () => {
    const blocks = parseTaggedBlocks();
    const exampleIds = new Set([...blocks.keys()].map(key => key.slice(0, 3)));
    const matrix = guide.match(
      /## 能力覆盖矩阵\n([\s\S]*?)(?=\n## |\s*$)/,
    )?.[1];
    if (!matrix) throw new Error("Missing capability coverage matrix");
    const rows = new Map<string, string[]>();
    for (const line of matrix.split("\n")) {
      if (!line.startsWith("| `")) continue;
      const cells = line.split("|").map(cell => cell.trim());
      const key = cells[1]?.match(/^`([^`]+)`$/)?.[1];
      if (!key) continue;
      rows.set(key, cells[3]?.match(/E\d{2}/g) ?? []);
    }
    expect([...rows.keys()].sort()).toEqual([...requiredCapabilities].sort());
    for (const [capability, examples] of rows) {
      expect(examples.length, capability).toBeGreaterThan(0);
      for (const example of examples) {
        expect(exampleIds.has(example), `${capability} -> ${example}`).toBe(true);
      }
    }
  });
});
