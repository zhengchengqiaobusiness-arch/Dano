# Issue #484: OAuth callback diagnostic boundary

## Confirmed defect

The production OAuth adapter combines authorization-code exchange and the first
identity request in `exchangeAuthorizationCode`. Callback diagnostics previously
attributed both failures to `provider_exchange`. A first Bearer identity request
returning HTTP 401 also projected the generic `login_failed` notification rather
than `provider_identity_invalid`.

On 2026-10-08, the real HTTP callback regression command
`pnpm exec vitest run apps/dano/src/__tests__/oauth-login-http.test.ts -t 'attributes first'`
failed in all three initial cases: Bearer GET 401, Bearer GET 503, and introspection
503. Token exchange succeeded in each fixture before identity validation failed.
The fixture provider is synthetic; this reproduces the implementation defect,
not the historical OA production incident.

The adapter now marks identity failures with a typed error boundary. The callback
uses `credential_validation` for those failures and retains the original cause
only for the existing allowlisted metadata extractor. Token exchange failures
remain `provider_exchange`; optional profile enrichment failure remains nonfatal.
Contract errors keep their existing type and code. No authorization retry or
identity, browser-binding, migration, or Login Session isolation check is relaxed.

The regression matrix covers both identity transports with HTTP 401, HTTP 503,
and HTTP 200 containing an invalid identity. It checks the server diagnostic,
browser error projection, absence of partial Login Sessions, and exclusion of
synthetic authorization codes, client secrets, tokens, and profile data from logs.

## Historical incident remains unconfirmed

A read-only inspection of the currently running production `dano-app-1` logs on
2026-10-08 found zero `OAuth login failed` records in the retained logs requested
since 2026-09-22. This does not establish that no failures occurred: earlier
container logs may no longer be retained. No production deployment or login
session was changed during inspection.

The 2026-09-22 incident cannot be uniquely attributed from this evidence. To
complete the remaining issue acceptance, capture a fresh real-provider failure
and its fixed stage, elapsed time, HTTP status, and allowlisted error codes; add
a failing regression for that observed cause and validate its fix. Keep callback
query strings, credentials, Cookies, identity data, response bodies, and original
exception text out of evidence. The current change must not be presented as
proof of the historical incident's cause.
