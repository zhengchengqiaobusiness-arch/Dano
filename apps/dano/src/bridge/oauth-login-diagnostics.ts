import type { BridgeLoginErrorCode } from "../../types/protocol.js";

export type OAuthLoginStage =
  | "provider_exchange"
  | "credential_encryption"
  | "credential_validation"
  | "session_persistence"
  | "anonymous_transfer"
  | "session_rotation";

// Never log exception text, stacks, URLs, headers, response bodies or identity.
// Both provider and filesystem exceptions can contain credentials or user data.
const PROVIDER_UNAVAILABLE_CODES = new Set([
  "TimeoutError", "AbortError", "OAUTH_TIMEOUT", "OAUTH_ABORT",
  "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "EAI_AGAIN",
  "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_HEADERS_TIMEOUT", "UND_ERR_BODY_TIMEOUT",
  "CERT_HAS_EXPIRED", "UNABLE_TO_VERIFY_LEAF_SIGNATURE", "ERR_TLS_CERT_ALTNAME_INVALID",
]);
const ERROR_CODES = new Set([
  ...PROVIDER_UNAVAILABLE_CODES,
  "provider_identity_invalid",
  "OAUTH_RESPONSE_BODY_ERROR",
  "OAUTH_RESPONSE_IS_NOT_CONFORM",
  "OAUTH_RESPONSE_IS_NOT_JSON",
  "OAUTH_INVALID_RESPONSE",
  "OAUTH_WWW_AUTHENTICATE_CHALLENGE",
  "OAUTH_HTTP_REQUEST_FORBIDDEN",
  "EACCES", "EPERM", "ENOSPC", "ENOENT", "EEXIST", "EIO", "ENOTDIR",
]);
const PROVIDER_ERRORS = new Set([
  "invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
  "unsupported_grant_type", "invalid_scope", "access_denied",
  "server_error", "temporarily_unavailable", "invalid_token",
]);

function describeOAuthLoginFailure(error: unknown) {
  let errorCode = "unclassified";
  let providerError: string | undefined;
  let httpStatus: number | undefined;
  // Bound cause traversal; malformed or cyclic exceptions must not affect login.
  for (let current = error, depth = 0; current && typeof current === "object" && depth < 4; depth++) {
    const record = current as Record<string, unknown>;
    if (errorCode === "unclassified") {
      if (typeof record.code === "string" && ERROR_CODES.has(record.code)) {
        errorCode = record.code;
      } else if (record.name === "TimeoutError" || record.name === "AbortError") {
        errorCode = record.name;
      }
    }
    if (!providerError && typeof record.error === "string" && PROVIDER_ERRORS.has(record.error)) {
      providerError = record.error;
    }
    const status = record.status ?? record.statusCode;
    if (httpStatus === undefined && typeof status === "number" && Number.isInteger(status) && status >= 100 && status <= 599) {
      httpStatus = status;
    }
    current = record.cause;
  }
  return {
    errorCode,
    ...(providerError ? { providerError } : {}),
    ...(httpStatus === undefined ? {} : { httpStatus }),
  };
}

export function classifyOAuthLoginFailure(
  stage: OAuthLoginStage,
  error: unknown,
): BridgeLoginErrorCode {
  // A local failure must not be misattributed to OA, even if a nested exception
  // contains a network or provider error code from rollback/cleanup.
  if (stage === "anonymous_transfer") return "user_data_transfer_failed";
  if (stage === "credential_encryption" || stage === "session_persistence" || stage === "session_rotation") {
    return "login_session_failed";
  }
  const { errorCode, providerError, httpStatus } = describeOAuthLoginFailure(error);
  if (providerError === "invalid_grant") return "authorization_invalid";
  if (
    providerError === "invalid_client" || providerError === "unauthorized_client" ||
    providerError === "invalid_scope" || providerError === "unsupported_grant_type" ||
    errorCode === "OAUTH_HTTP_REQUEST_FORBIDDEN"
  ) return "login_configuration_error";
  if (
    PROVIDER_UNAVAILABLE_CODES.has(errorCode) ||
    providerError === "server_error" || providerError === "temporarily_unavailable" ||
    httpStatus === 429 || (httpStatus !== undefined && httpStatus >= 500)
  ) return "provider_unavailable";
  if (
    errorCode === "provider_identity_invalid" || providerError === "invalid_token" ||
    (stage === "credential_validation" && httpStatus === 401)
  ) return "provider_identity_invalid";
  return "login_failed";
}

export function reportOAuthLoginFailure(
  stage: OAuthLoginStage,
  error: unknown,
  elapsedMs: number,
): void {
  console.warn("OAuth login failed", {
    stage,
    elapsedMs: Math.max(0, Math.round(elapsedMs)),
    ...describeOAuthLoginFailure(error),
  });
}
