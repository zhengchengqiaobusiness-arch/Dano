/** @vitest-environment happy-dom */

import { mount, unmount } from "svelte";
import { describe, expect, it } from "vitest";
import SubmittedAnswerValue from "./SubmittedAnswerValue.svelte";
import submittedAnswerValueSource from "./SubmittedAnswerValue.svelte?raw";

describe("SubmittedAnswerValue", () => {
  it("reuses the editable form control styles", () => {
    const target = document.createElement("div");
    const component = mount(SubmittedAnswerValue, {
      target,
      props: { value: "上海" },
    });

    try {
      expect(
        target.querySelector(".submitted-field-value")?.classList.contains("question-input"),
      ).toBe(true);
    } finally {
      unmount(component);
    }
  });

  it("renders the confirmation value as a disabled single-line control", () => {
    const containerRule = submittedAnswerValueSource.match(
      /\.submitted-field-value \{([\s\S]*?)\n  \}/,
    )?.[1];
    const textRule = submittedAnswerValueSource.match(
      /\.submitted-field-value-text \{([\s\S]*?)\n  \}/,
    )?.[1];

    expect(containerRule).toContain("display: flex;");
    expect(containerRule).toContain("align-items: center;");
    expect(containerRule).toContain("background: var(--panel-2);");
    expect(containerRule).toContain("color: var(--text-muted);");
    expect(textRule).toContain("min-width: 0;");
    expect(textRule).toContain("overflow: hidden;");
    expect(textRule).toContain("text-overflow: ellipsis;");
  });
});
