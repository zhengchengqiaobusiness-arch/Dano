# Self-contained skill package specification

A package is a directory that runs against the recorded business API without a
Dano runtime or an LLM.

## Required layout

```text
<package>/
  SKILL.md
  reference.md
  scripts/
    client.py
    <capability>.py
    verify_<capability>.py
```

`SKILL.md` must have YAML frontmatter with non-empty `name` and `description`.
Its body must contain the sections `Transport`, `Preconditions`, `Steps`,
`Branch exit`, and `Pitfalls`. Every item in `Steps` must state a `Done when`
condition.

`reference.md` must contain an `API chain` section. Every described chain must
name its executor-generated `verification_id`; a chain that exhausted automatic
verification must be marked `unverified` instead.

## Script contract

`client.py` owns base URL, authentication assembly, HTTP JSON transport, success
rules, and settle waits. Each capability has one command script and one matching
`verify_<capability>.py` read-back script. Every Python script must accept
`--help`, must not require Dano at runtime, and must print machine-readable JSON
for operational results. Runtime dependencies are limited to Python and httpx.

Credentials come from environment variables or the documented local session
cache. A package must never contain a recorded token, cookie, password, session,
or other plaintext credential.

## Optional stage-8 planning fields

When a recording result is exported through manual Skill planning, `references/CONTRACT.json` also includes:

- `planning_mode`: `dynamic` or `fixed`
- `selected_capability_ids`
- `routes`
- `bindings`
- `unused_capabilities`
- `source_flow_fingerprint`

Public scripts and `SKILL.md` may only name selected capabilities. Unused capabilities stay in the original FlowSpec but must not appear as packed scripts. Packages without these fields remain valid single-capability exports.

A planned `SKILL.md` still requires `Transport`, `Preconditions`, `Steps`, `Branch exit`, `Pitfalls`, and a `Done when` for every step. It additionally describes business purpose, when to use, routing, bindings, confirmation, verification, failure handling, and one example per valid route.
