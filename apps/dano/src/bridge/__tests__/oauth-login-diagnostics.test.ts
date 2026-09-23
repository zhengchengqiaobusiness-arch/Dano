import { afterEach, describe, expect, it, vi } from "vitest";
import { classifyOAuthLoginFailure, type OAuthLoginStage, reportOAuthLoginFailure } from "../oauth-login-diagnostics.js";

afterEach(() => vi.restoreAllMocks());

describe("OAuth login diagnostics", () => {
  it("keeps only allowlisted metadata from nested network errors", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const error = new TypeError("fetch failed: https://private.example/?token=secret", {
      cause: Object.assign(new Error("address with private data"), {
        code: "ECONNRESET",
        address: "private.example",
      }),
    });
    reportOAuthLoginFailure("provider_exchange", error, 42.7);
    expect(warn).toHaveBeenCalledExactlyOnceWith("OAuth login failed", {
      stage: "provider_exchange", elapsedMs: 43, errorCode: "ECONNRESET",
    });
  });

  it("drops unknown codes, raw messages, response data and cyclic causes", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const error: Record<string, unknown> = {
      code: "secret-code", error: "secret-provider-value", name: "secret-name",
      message: "secret-message", stack: "secret-stack", status: "secret-status",
      response: { access_token: "secret-token" },
    };
    error.cause = error;
    reportOAuthLoginFailure("provider_exchange", error, 1);
    expect(warn).toHaveBeenCalledExactlyOnceWith("OAuth login failed", {
      stage: "provider_exchange", elapsedMs: 1, errorCode: "unclassified",
    });
  });

  it("extracts HTTP status from a library response cause without its URL or headers", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const error = Object.assign(new Error("private error text"), {
      code: "OAUTH_RESPONSE_IS_NOT_CONFORM",
      cause: new Response("private body", {
        status: 503, headers: { "set-cookie": "private-cookie" },
      }),
    });
    reportOAuthLoginFailure("credential_validation", error, 12);
    expect(warn).toHaveBeenCalledExactlyOnceWith("OAuth login failed", {
      stage: "credential_validation", elapsedMs: 12,
      errorCode: "OAUTH_RESPONSE_IS_NOT_CONFORM", httpStatus: 503,
    });
  });

  it.each([null, "private rejection", 123, undefined])("handles non-Error rejections", error => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    reportOAuthLoginFailure("anonymous_transfer", error, 0);
    expect(warn).toHaveBeenCalledExactlyOnceWith("OAuth login failed", {
      stage: "anonymous_transfer", elapsedMs: 0, errorCode: "unclassified",
    });
  });
});


describe("OAuth login failure classification", () => {
  it.each([
    ["provider_exchange", { error: "invalid_grant" }, "authorization_invalid"],
    ["provider_exchange", { error: "invalid_client" }, "login_configuration_error"],
    ["provider_exchange", { error: "unauthorized_client" }, "login_configuration_error"],
    ["provider_exchange", { error: "invalid_scope" }, "login_configuration_error"],
    ["provider_exchange", { cause: { code: "ECONNRESET" } }, "provider_unavailable"],
    ["provider_exchange", { name: "TimeoutError" }, "provider_unavailable"],
    ["provider_exchange", { cause: new Response(null, { status: 503 }) }, "provider_unavailable"],
    ["credential_validation", { code: "provider_identity_invalid", cause: { status: 503 } }, "provider_unavailable"],
    ["credential_validation", { status: 429 }, "provider_unavailable"],
    ["credential_validation", { status: 401 }, "provider_identity_invalid"],
    ["provider_exchange", { code: "provider_identity_invalid" }, "provider_identity_invalid"],
    ["credential_encryption", { error: "invalid_grant" }, "login_session_failed"],
    ["session_persistence", { code: "ENOSPC" }, "login_session_failed"],
    ["session_rotation", { cause: { code: "ECONNRESET" } }, "login_session_failed"],
    ["anonymous_transfer", { status: 409 }, "user_data_transfer_failed"],
    ["anonymous_transfer", { code: "EIO" }, "user_data_transfer_failed"],
    ["provider_exchange", new Error("private provider response"), "login_failed"],
  ] as const)("classifies %s without exposing raw exception details", (stage, error, expected) => {
    expect(classifyOAuthLoginFailure(stage as OAuthLoginStage, error)).toBe(expected);
  });
});
