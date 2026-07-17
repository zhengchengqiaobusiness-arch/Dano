import { describe, expect, it } from "vitest";
import {
  DANO_LLM_AUTHENTICATION_ERROR,
  DANO_LLM_INCOMPLETE_ERROR,
  DANO_LLM_NETWORK_ERROR,
  DANO_LLM_QUOTA_ERROR,
  DANO_LLM_RATE_LIMIT_ERROR,
  DANO_LLM_SERVICE_ERROR,
  DANO_LLM_TIMEOUT_ERROR,
  DANO_LLM_UNKNOWN_ERROR,
  normalizeLlmErrorMessage,
} from "../llm-error.js";

function assistantError(errorMessage: string, content: unknown[] = []) {
  return {
    role: "assistant",
    stopReason: "error",
    errorMessage,
    content,
  };
}

describe("normalizeLlmErrorMessage", () => {
  it.each([
    ["Request timed out.", DANO_LLM_TIMEOUT_ERROR],
    ["401 Incorrect API key secret-value", DANO_LLM_AUTHENTICATION_ERROR],
    ["429 rate limit exceeded", DANO_LLM_RATE_LIMIT_ERROR],
    ["insufficient_quota: available balance 0", DANO_LLM_QUOTA_ERROR],
    ["503 Service Unavailable", DANO_LLM_SERVICE_ERROR],
    ["fetch failed: socket hang up", DANO_LLM_NETWORK_ERROR],
    ["provider leaked Authorization: Bearer secret", DANO_LLM_UNKNOWN_ERROR],
  ])("classifies and sanitizes %s", (raw, expected) => {
    expect(normalizeLlmErrorMessage(assistantError(raw))).toBe(expected);
  });

  it("marks a failed partial response as incomplete", () => {
    expect(
      normalizeLlmErrorMessage(
        assistantError("terminated", [
          { type: "text", text: "Already received" },
        ]),
      ),
    ).toBe(DANO_LLM_INCOMPLETE_ERROR);
  });

  it("leaves non-error messages unchanged", () => {
    expect(
      normalizeLlmErrorMessage({
        role: "assistant",
        stopReason: "stop",
        errorMessage: undefined,
        content: [{ type: "text", text: "Done" }],
      }),
    ).toBeUndefined();
  });
});
