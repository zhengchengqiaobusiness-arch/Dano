# RESTful API Tool Runtime Implementation Plan for Codex

## Goal

Implement a reusable tool that allows the agent to call approved RESTful APIs safely and consistently.

This is not a Postman clone, OpenAPI importer, or API management platform. The target is a minimal, secure, agent-facing REST API call runtime.

## DONE WHEN

- The agent can call RESTful APIs through one unified tool interface.
- The tool supports `GET`, `POST`, `PUT`, `PATCH`, and `DELETE`.
- The tool supports headers, query parameters, JSON body, timeout, and basic authentication configuration.
- The tool only allows requests to configured allowlisted hosts or URL prefixes.
- Secrets are referenced by key/ref and are not exposed directly to the agent.
- Responses are normalized into a stable structure for agent consumption.
- Network, timeout, HTTP, parsing, and security errors are distinguishable.
- Dangerous operations can be blocked or require confirmation according to configuration.
- Request and response metadata are logged for audit/debugging, without leaking secrets.
- Unit tests cover success cases, error cases, security restrictions, and response normalization.
- No lint, test, typecheck, or build errors related to the new tool.

## Non-goals

Do not implement these in the first version:

- Full Postman collection import/export.
- Full OpenAPI parser or automatic client generation.
- API mock server.
- Visual API debugger UI.
- Workflow orchestration engine.
- Browser automation fallback.
- Long-running async job runtime.

## Proposed Tool Interface

Create a single agent-facing tool similar to:

```ts
rest_api_call(input: RestApiCallInput): Promise<RestApiCallResult>
```

### Input Type

```ts
type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

type AuthConfig =
  | { type: 'none' }
  | { type: 'bearer'; tokenRef: string }
  | { type: 'apiKey'; keyName: string; keyValueRef: string; in: 'header' | 'query' }
  | { type: 'basic'; usernameRef: string; passwordRef: string }
  | { type: 'cookie'; cookieRef: string };

type RestApiCallInput = {
  method: HttpMethod;
  url: string;
  headers?: Record<string, string>;
  query?: Record<string, string | number | boolean | null | undefined>;
  body?: unknown;
  auth?: AuthConfig;
  timeoutMs?: number;
  responseType?: 'json' | 'text';
};
```

### Result Type

```ts
type RestApiCallResult = {
  ok: boolean;
  status?: number;
  statusText?: string;
  headers?: Record<string, string>;
  data?: unknown;
  error?: {
    type: 'network' | 'timeout' | 'http' | 'parse' | 'security' | 'validation';
    message: string;
    detail?: unknown;
  };
  meta: {
    requestId: string;
    method: HttpMethod;
    url: string;
    durationMs: number;
  };
};
```

## Required Configuration

Add a runtime config layer for the tool.

Example:

```ts
type RestApiToolConfig = {
  allowedHosts?: string[];
  allowedUrlPrefixes?: string[];
  blockedMethods?: HttpMethod[];
  confirmationRules?: Array<{
    method?: HttpMethod;
    urlPattern: string;
    reason: string;
  }>;
  defaultTimeoutMs: number;
  maxTimeoutMs: number;
  maxResponseBytes: number;
  redactHeaderNames: string[];
};
```

Minimum recommended defaults:

```ts
{
  allowedHosts: [],
  allowedUrlPrefixes: [],
  blockedMethods: [],
  defaultTimeoutMs: 10000,
  maxTimeoutMs: 30000,
  maxResponseBytes: 1024 * 1024,
  redactHeaderNames: ['authorization', 'cookie', 'set-cookie', 'x-api-key']
}
```

## Security Requirements

### 1. URL allowlist

Before making any request, validate the target URL.

Rules:

- Reject invalid URLs.
- Reject non-HTTP(S) protocols.
- Reject URLs not matching `allowedHosts` or `allowedUrlPrefixes`.
- Reject local/private network targets unless explicitly allowed.
- Reject redirects to non-allowlisted targets.

Return:

```ts
{
  ok: false,
  error: {
    type: 'security',
    message: 'URL is not allowed by REST API tool policy'
  }
}
```

### 2. Secret handling

The agent must not pass raw secrets.

Allowed:

```ts
{ type: 'bearer', tokenRef: 'oa-user-token' }
```

Not allowed:

```ts
{ type: 'bearer', token: 'actual-token-value' }
```

Use an internal secret resolver:

```ts
type SecretResolver = {
  getSecret(ref: string): Promise<string>;
};
```

Make sure logs never contain raw secret values.

### 3. Dangerous operation control

Add a policy check before executing mutating or destructive calls.

Examples:

- `DELETE *`
- `POST /approval/submit`
- `POST /payment/*`
- `PATCH /users/*`

For the first version, either:

- block dangerous operations by config, or
- return a structured `requires_confirmation` result if the existing agent runtime supports confirmation.

If no confirmation mechanism exists yet, block by default.

## Implementation Steps

### Step 1: Locate tool/runtime structure

Inspect the existing project structure and identify where agent tools are defined and registered.

Look for patterns such as:

- `tools/`
- `server/tools/`
- `agent/tools/`
- `src/tools/`
- `mcp/`
- `runtime/`
- `function-call/`
- existing tool schemas
- existing tests for tools

Follow the existing style instead of introducing a separate architecture.

### Step 2: Add types

Create or extend type definitions for:

- `RestApiCallInput`
- `RestApiCallResult`
- `RestApiToolConfig`
- `AuthConfig`
- `SecretResolver`

Prefer strict TypeScript types if this is a TypeScript project.

### Step 3: Add input validation

Validate:

- `method` is supported.
- `url` is valid.
- `headers` are strings.
- `query` values are primitive values.
- `timeoutMs` does not exceed configured max.
- `auth` shape is valid.
- `body` is not sent with `GET` unless existing conventions allow it.

Return `validation` errors instead of throwing unhandled exceptions.

### Step 4: Add URL policy validation

Implement a reusable policy validator.

Suggested function:

```ts
function validateRestApiUrlPolicy(url: string, config: RestApiToolConfig): PolicyValidationResult
```

Cover:

- protocol
- host allowlist
- URL prefix allowlist
- private/local address restrictions
- redirect target validation if redirects are enabled

### Step 5: Add auth application

Implement:

```ts
async function applyAuth(
  request: NormalizedRequest,
  auth: AuthConfig | undefined,
  secretResolver: SecretResolver
): Promise<NormalizedRequest>
```

Support first version:

- `none`
- `bearer`
- `apiKey` in header
- `apiKey` in query
- `basic`
- `cookie`

### Step 6: Execute HTTP request

Use the project standard HTTP client if one already exists.

If none exists, use native `fetch` if available in the runtime.

Requirements:

- Apply timeout using `AbortController` or equivalent.
- Serialize query params safely.
- Serialize JSON body when `body` is provided.
- Set `content-type: application/json` when sending JSON unless explicitly provided.
- Avoid following redirects blindly unless redirect targets are revalidated.
- Enforce `maxResponseBytes` where feasible.

### Step 7: Normalize response

Return stable response shape.

Behavior:

- `2xx`: `ok: true`
- non-`2xx`: `ok: false`, `error.type: 'http'`, still include parsed response body in `data` when safe
- JSON response: parse into `data`
- text response: return text
- invalid JSON when expected: `error.type: 'parse'`
- timeout: `error.type: 'timeout'`
- network failure: `error.type: 'network'`

### Step 8: Redact logs

Add audit/debug logging with redaction.

Log fields:

- request id
- user id if available
- method
- redacted URL
- status
- duration
- error type
- timestamp

Do not log:

- Authorization header value
- Cookie value
- API key value
- raw request body if it may include sensitive data

### Step 9: Register the tool

Expose the tool to the agent runtime with a clear description.

Tool description should tell the agent:

- Use this tool only for approved RESTful API calls.
- Do not include raw secrets.
- Use `tokenRef` or other secret refs for authentication.
- Prefer JSON request/response.
- Check `ok` and `error.type` before assuming success.

### Step 10: Add tests

Minimum tests:

1. Successful `GET` with query params.
2. Successful `POST` with JSON body.
3. Bearer token is resolved and applied.
4. API key in header is resolved and applied.
5. Disallowed host is rejected.
6. Invalid URL is rejected.
7. Timeout returns `timeout` error.
8. Non-2xx response returns `ok: false` and `error.type: 'http'`.
9. Invalid JSON response returns `parse` error when JSON is expected.
10. Logs redact sensitive headers.
11. `DELETE` or configured dangerous endpoint is blocked when policy requires it.

### Step 11: Add example usage

Add one or more examples in docs or tests.

Example:

```ts
await restApiCall({
  method: 'GET',
  url: 'https://oa.example.com/api/leave/types',
  auth: { type: 'bearer', tokenRef: 'oa-user-token' }
});
```

Example:

```ts
await restApiCall({
  method: 'POST',
  url: 'https://oa.example.com/api/leave/apply',
  auth: { type: 'bearer', tokenRef: 'oa-user-token' },
  body: {
    type: 'annual_leave',
    startDate: '2026-07-01',
    endDate: '2026-07-02',
    reason: 'personal'
  }
});
```

## Suggested File Organization

Adapt to the existing codebase. If there is no existing convention, use something like:

```text
src/tools/rest-api/
  index.ts
  types.ts
  config.ts
  policy.ts
  auth.ts
  execute.ts
  redact.ts
  rest-api-call.ts
  __tests__/
    rest-api-call.spec.ts
    policy.spec.ts
    auth.spec.ts
    redact.spec.ts
```

## Validation Commands

Run the project-appropriate commands. Prefer existing package scripts.

Examples:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

If the project uses pnpm/yarn/bun, use the existing package manager.

## Output Required From Codex

At the end, report:

- Changed files
- Why each file was changed
- Tool interface summary
- Security controls implemented
- Test results
- Build/lint/typecheck results
- Remaining risks
- Whether follow-up work is needed

## Remaining Risks To Watch

- Existing agent runtime may already have a different confirmation mechanism.
- Existing secret management may require adapter work.
- Some enterprise OA APIs may require cookie/session authentication rather than token auth.
- File upload and multipart form support may be needed later.
- Streaming response support is intentionally out of scope for the first version.
- If APIs are discovered from OpenAPI specs later, this tool should remain the execution layer, not the spec parser.

## Recommended First Demo Scope

Use three demo endpoints only:

```text
GET  /api/leave/types
POST /api/leave/apply
GET  /api/leave/status
```

This is enough to prove that the agent can:

1. Read available leave types.
2. Submit a leave request.
3. Query the request result.

Keep the implementation focused on safe, reliable API execution.
