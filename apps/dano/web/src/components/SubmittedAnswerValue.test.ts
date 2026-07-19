/** @vitest-environment happy-dom */

import { mount, unmount } from "svelte";
import { describe, expect, it } from "vitest";
import SubmittedAnswerValue from "./SubmittedAnswerValue.svelte";

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
});
