import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  AskUserQuestionCoordinator,
  isAskUserQuestionConfirmationCall,
  selectAskUserQuestionConfirmationTargets,
} from "../ask-user-question.js";

interface ModelDeviationFixture {
  issue: number;
  capturedArguments: Record<string, unknown>;
  canonicalArguments: Record<string, unknown>;
  expected: {
    targetIds: string[];
    ignoredReasons: string[];
    fallbackAttempted: boolean;
  };
}

interface CompatibilityCase {
  name: string;
  request: Record<string, unknown>;
  expected: {
    targetIds: string[];
    ignoredReasons: string[];
    fallbackAttempted: boolean;
    receivedShape: { formIds: string; formId: string };
  };
}

const fixture = JSON.parse(readFileSync(
  new URL(
    "./fixtures/ask-user-question-model-deviations.json",
    import.meta.url,
  ),
  "utf8",
)) as ModelDeviationFixture;

const availableTargets = new Map([
  ["form-a", { id: "form-a" }],
  ["form-b", { id: "form-b" }],
]);

const compatibilityCases: CompatibilityCase[] = [
  {
    name: "native array",
    request: { confirm: true, formIds: ["form-b", "form-a"] },
    expected: {
      targetIds: ["form-b", "form-a"],
      ignoredReasons: [],
      fallbackAttempted: false,
      receivedShape: { formIds: "array(2)", formId: "omitted" },
    },
  },
  {
    name: "JSON-stringified array",
    request: { confirm: "True", formIds: '["form-b","form-a"]' },
    expected: {
      targetIds: ["form-b", "form-a"],
      ignoredReasons: [],
      fallbackAttempted: false,
      receivedShape: { formIds: "string", formId: "omitted" },
    },
  },
  {
    name: "whitespace-padded JSON array",
    request: { confirm: true, formIds: '  ["form-a", "form-b"]  ' },
    expected: {
      targetIds: ["form-a", "form-b"],
      ignoredReasons: [],
      fallbackAttempted: false,
      receivedShape: { formIds: "string", formId: "omitted" },
    },
  },
  {
    name: "ordinary scalar string",
    request: { confirm: true, formIds: "form-a" },
    expected: {
      targetIds: ["form-a"],
      ignoredReasons: [],
      fallbackAttempted: false,
      receivedShape: { formIds: "string", formId: "omitted" },
    },
  },
  {
    name: "formId alias",
    request: { confirm: true, formId: '["form-b","form-a"]' },
    expected: {
      targetIds: ["form-b", "form-a"],
      ignoredReasons: [],
      fallbackAttempted: false,
      receivedShape: { formIds: "omitted", formId: "string" },
    },
  },
  {
    name: "canonical and alias fields together",
    request: {
      confirm: true,
      formIds: ["form-b", "form-a"],
      formId: '["form-a","form-b"]',
    },
    expected: {
      targetIds: ["form-b", "form-a"],
      ignoredReasons: [],
      fallbackAttempted: false,
      receivedShape: { formIds: "array(2)", formId: "string" },
    },
  },
  {
    name: "duplicate targets",
    request: { confirm: true, formIds: ["form-b", "form-a", "form-b"] },
    expected: {
      targetIds: ["form-b", "form-a"],
      ignoredReasons: [],
      fallbackAttempted: false,
      receivedShape: { formIds: "array(3)", formId: "omitted" },
    },
  },
  {
    name: "empty items",
    request: { confirm: true, formIds: ["", " ", null, "form-a"] },
    expected: {
      targetIds: ["form-a"],
      ignoredReasons: ["malformed_form_id"],
      fallbackAttempted: false,
      receivedShape: { formIds: "array(4)", formId: "omitted" },
    },
  },
  {
    name: "non-string items",
    request: { confirm: true, formIds: [7, false, "form-a"] },
    expected: {
      targetIds: ["form-a"],
      ignoredReasons: ["malformed_form_id"],
      fallbackAttempted: false,
      receivedShape: { formIds: "array(3)", formId: "omitted" },
    },
  },
  {
    name: "unknown target",
    request: { confirm: true, formIds: ["missing", "form-a"] },
    expected: {
      targetIds: ["form-a"],
      ignoredReasons: ["unavailable_form_id"],
      fallbackAttempted: false,
      receivedShape: { formIds: "array(2)", formId: "omitted" },
    },
  },
  {
    name: "partial-valid JSON collection",
    request: { confirm: "True", formIds: '["missing", "form-b"]' },
    expected: {
      targetIds: ["form-b"],
      ignoredReasons: ["unavailable_form_id"],
      fallbackAttempted: false,
      receivedShape: { formIds: "string", formId: "omitted" },
    },
  },
  {
    name: "all-invalid collection",
    request: { confirm: true, formIds: ["missing", null] },
    expected: {
      targetIds: ["form-b"],
      ignoredReasons: ["unavailable_form_id", "malformed_form_id"],
      fallbackAttempted: true,
      receivedShape: { formIds: "array(2)", formId: "omitted" },
    },
  },
  {
    name: "cross-Assistant-Turn target",
    request: { confirm: true, formIds: ["other-turn-form", "form-a"] },
    expected: {
      targetIds: ["form-a"],
      ignoredReasons: ["unavailable_form_id"],
      fallbackAttempted: false,
      receivedShape: { formIds: "array(2)", formId: "omitted" },
    },
  },
];

function select(request: unknown) {
  const selection = selectAskUserQuestionConfirmationTargets(
    request,
    availableTargets,
  );
  return {
    targetIds: selection.targets.map(target => target.id),
    ignoredReasons: selection.ignoredReasons,
    fallbackAttempted: selection.fallbackAttempted,
    receivedShape: selection.receivedShape,
  };
}

async function canonicalProjection(request: Record<string, unknown>) {
  const coordinator = new AskUserQuestionCoordinator();
  const controller = new AbortController();
  for (const formId of ["form-a", "form-b"]) {
    const submitted = coordinator.wait(
      formId,
      {
        title: formId,
        questions: [{ id: "value", question: "值？", default: formId }],
      },
      controller.signal,
    );
    coordinator.present(formId);
    coordinator.answer(formId, {
      cancelled: false,
      answer: { value: formId },
    });
    await submitted;
  }
  const confirmation = coordinator.wait(
    "confirm-fixture",
    request as never,
    controller.signal,
  );
  const projection = coordinator.cardRequest("confirm-fixture");
  coordinator.answer("confirm-fixture", { cancelled: true });
  await confirmation;
  return projection;
}

describe("ask_user_question confirmation compatibility matrix", () => {
  it.each(compatibilityCases)("projects $name", ({ request, expected }) => {
    expect(select(request)).toEqual(expected);
  });

  it("keeps native and safely JSON-stringified collections metamorphically equivalent", () => {
    const native = select({
      confirm: true,
      formIds: ["form-b", "form-a", "form-b", "missing", null],
    });
    const jsonString = select({
      confirm: "True",
      formIds: JSON.stringify([
        "form-b",
        "form-a",
        "form-b",
        "missing",
        null,
      ]),
    });

    expect({ ...jsonString, receivedShape: native.receivedShape }).toEqual(native);
  });

  it.each([
    { confirm: true, formIds: ["form-a", "form-b"] },
    { confirm: "True", formIds: ["form-a", "form-b"] },
    { confirm: 1, formIds: '["form-a", "form-b"]' },
    { confirm: "yes", formIds: '["form-a", "form-b"]' },
  ])("crosses boolean-compatible confirm with target collections: %#", request => {
    expect(isAskUserQuestionConfirmationCall(request)).toBe(true);
    expect(select(request).targetIds).toEqual(["form-a", "form-b"]);
  });

  it("captures the sanitized #312 model deviation and its canonical projection", async () => {
    expect(fixture.issue).toBe(312);
    expect(select(fixture.capturedArguments)).toMatchObject(fixture.expected);
    expect(select(fixture.canonicalArguments)).toMatchObject(fixture.expected);
    await expect(canonicalProjection(fixture.capturedArguments)).resolves.toEqual(
      await canonicalProjection(fixture.canonicalArguments),
    );
  });
});
