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
    INPUT_FORMS.md
```

Do not pack `references/generator-guides/`. Those files are generator-internal
and must not enter a consumer Skill.

`SKILL.md` must have YAML frontmatter with non-empty `name` and `description`.
The description must say what the Skill does, when to use it, and when not to
use it. One recorded OA page maps to one Skill. Stage 6/7 yields atomic
capabilities; Stage 8 turns the operator's natural-language composition into
routes. After planning finishes, `SKILL.md` must show those routes, not just a
flat capability list.

The body must contain `适用场景`, `不适用场景`, `能力关系`, `操作路由`, `输入`,
`操作步骤`, `工具`, `输出`, `完成标准`, `失败处理`, and `安全边界`. Every item
in `操作步骤` must state a `Done when` condition.

`能力关系` records recommended order, planned combination routes, confirmed
bindings that may auto-fill, and hand-offs that still need the user. Combination
routes appear only from the Stage 8 plan. Unconfirmed field-name guesses must
not become bindings.

`references/OPERATIONS.md` must contain an `API chain` section. Every described
chain must name its executor-generated `verification_id`; a chain that exhausted
automatic verification must be marked `unverified` instead.

`references/INPUT_FORMS.md` is an on-demand `ask_user_question` contract.

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
routes come from the Stage 8 plan: confirmed bindings may auto-fill; planned
sequences without bindings still appear, but the next input is collected from
the user. Standalone leftover capabilities stay as operations; they must not
become `solo_*` routes.

The model may only rewrite `when_to_use` and examples for those frozen routes.
It must not replace `selected_capability_ids`, route sequences, or bindings.
`SKILL.md` 能力关系 must quote the operator's business description as the
composition contract, and `操作步骤` must include one playbook per planned
route.
