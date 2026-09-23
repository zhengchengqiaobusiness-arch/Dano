import { describe, expect, it, vi } from "vitest";
import { memoryWriterClassifier } from "../memory-writer-classifier.js";

function fixture(output: string) {
  const complete = vi.fn(async () => output);
  const selector = { maxInputBytes: 8192, maxFacts: 5, timeoutMs: 1000,
    sensitiveValues: vi.fn(async () => ["synthetic-secret-token"]), complete };
  const classify = memoryWriterClassifier(selector);
  const input = { selectedText: "我喜欢茉莉花茶", candidateText: "我的偏好是茉莉茶", scope: null };
  return { classify, complete, input };
}

describe("memory writer classification", () => {
  it("accepts only a strict model decision", async () => {
    const target = fixture('{"decision":"target"}');
    expect(await target.classify(target.input)).toBe("target");
    const unrelated = fixture('{"decision":"unrelated"}');
    expect(await unrelated.classify(unrelated.input)).toBe("unrelated");
    const ambiguous = fixture('{"decision":"unrelated","reason":"extra"}');
    expect(await ambiguous.classify(ambiguous.input)).toBe("uncertain");
  });

  it("does not send secrets, oversized inputs, or failed model responses", async () => {
    const secret = fixture('{"decision":"unrelated"}');
    expect(await secret.classify({ ...secret.input, candidateText: "synthetic-secret-token" })).toBe("uncertain");
    expect(secret.complete).not.toHaveBeenCalled();
    const oversized = fixture('{"decision":"unrelated"}');
    expect(await oversized.classify({ ...oversized.input, candidateText: "x".repeat(9000) })).toBe("uncertain");
    expect(oversized.complete).not.toHaveBeenCalled();
    const malformed = fixture("unrelated");
    expect(await malformed.classify(malformed.input)).toBe("uncertain");
  });
});
