# Security model

## Default redaction

The recorder redacts:
- `authorization`, `proxy-authorization`, `cookie`, `set-cookie`;
- keys containing password/passwd/secret/token/api-key/access-key/refresh-token/session;
- password inputs and common password autocomplete fields.

Response bodies are truncated to `BSS_MAX_RESPONSE_BYTES`.

## Credentials

Exported contracts never contain captured credentials.
Runtime authentication is injected with:

```bash
export SKILL_AUTH_HEADERS='{"Authorization":"Bearer ..."}'
```

Prefer short-lived test credentials and a non-production tenant while learning a system.

## Write safety

`create`, `update`, `review`, and `delete` are treated as side-effecting operations.
- Pi extension execution prompts the user with `ctx.ui.confirm`.
- Exported `execute.mjs` requires `--confirm-write`.
- Planning alone never counts as confirmation.

## Publication gate

A capability is exportable only when `validation.status` is `verified`.
Unknown operations or capabilities without a successful recorded response remain candidates.
