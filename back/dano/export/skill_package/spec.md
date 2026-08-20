# Self-contained skill package specification

A package is a directory that runs against the recorded business API without a
Dano runtime or an LLM. It follows Agent Skill progressive disclosure:
`SKILL.md` is the only always-read entry; `scripts/` and `references/` load on demand.

## Required layout

```text
<package>/
  SKILL.md
  scripts/
    client.py
    <capability>.py
    verify_<capability>.py   # only when the write operation requires verify
    format_list.py
    wire_format.py
  references/
    CONTRACT.json
    OPERATIONS.md
```

Do not pack `references/generator-guides/`. Those files are generator-internal
and must not enter a consumer Skill.

`SKILL.md` must have YAML frontmatter with non-empty `name` and `description`.
The description must say what the Skill does, when to use it, and when not to
use it. The body must contain `适用场景`, `不适用场景`, `操作路由`, `操作步骤`,
`失败处理`, and `安全边界`. Every item in `操作步骤` must state a `Done when`
condition.

`references/OPERATIONS.md` must contain an `API chain` section. Every described
chain must name its executor-generated `verification_id`; a chain that exhausted
automatic verification must be marked `unverified` instead.

Stage 8 packs the Stage 6/7 capability contract as-is: field identity, option
maps, request templates, sample evidence, defaults, and success rules must not
be rewritten or dropped. `SKILL.md` may add routing and handbook language, but
it cannot invent, rename, or delete those facts.

## Script contract

`client.py` owns base URL, authentication assembly, HTTP JSON transport, success
rules, and settle waits. Each capability has one command script. Write
capabilities that require read-back also get `verify_<capability>.py`. Every
Python script must accept `--help`, must not require Dano at runtime, and must
print machine-readable JSON for operational results. Runtime dependencies are
limited to Python and httpx.

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

A planned `SKILL.md` lists every packed operation in `操作路由`. Combination
routes appear only when a confirmed binding exists. Standalone leftover
capabilities stay as operations; they must not become `solo_*` routes.
