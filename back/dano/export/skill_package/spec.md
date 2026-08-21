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
use it. One OA page maps to one Skill. After planning finishes, `SKILL.md` is
an executable handbook: user intent maps to one operation or one combination
route. It is not a capability catalog, a recording log, or a longer prompt.

The body must contain `适用场景`, `不适用场景`, `能力关系`, `操作路由`, `输入`,
`操作步骤`, `工具`, `输出`, `完成标准`, `失败处理`, and `安全边界`. Every item
in `操作步骤` must state a `Done when` condition.

`能力关系` quotes the operator's business description, then the hand-off rules:
read-only requests must not upgrade into writes; confirmed bindings may
auto-fill named fields; without bindings, look up first, stop, and ask the
user. Do not write recording order, stage numbers, or internal ids.

Consumer handbook text (SKILL.md, OPERATIONS intro, INPUT_FORMS intro,
when_to_use, composition, summary) must not contain: `本页面的实际操作流程`,
`能力录制`, `录制结果`, `阶段1`, `本页原子能力`, `按用户意图选择一项`,
`阶段 6` / `阶段6` / `阶段 7` / `阶段7` / `阶段 8` / `阶段8`,
`录制识别顺序`, `FlowSpec`, `fingerprint`, `capability_id`, `x-dano`,
`规划依据`, `原子能力`, `一页面对应一个 Skill`, `原样来自`, `生成器`.
「已确认绑定」may appear as an execution rule.

`references/OPERATIONS.md` must contain an `API chain` section. Every described
chain must name its executor-generated `verification_id`; a chain that exhausted
automatic verification must be marked `unverified` instead.

`references/INPUT_FORMS.md` is an on-demand `ask_user_question` contract.

The packed contract is copied as-is: field identity, option maps, request
templates, sample evidence, defaults, and success rules must not be rewritten
or dropped. `SKILL.md` may add routing and handbook language, but it cannot
invent, rename, or delete those facts. Scripts may still contain PLAN JSON;
the handbook must not tell the Agent to read PLAN, `x-dano`, or
`capability_id`.

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

A planned `SKILL.md` lists every packed operation in `操作路由`. The intent
column uses `when_to_use`, not a title recitation. Combination routes come
from the plan: confirmed bindings may auto-fill; sequences without bindings
still appear, but the next input is collected from the user. Leftover
operations stay as `op_*` routes; they must not become `solo_*` routes.

The model may only rewrite `when_to_use` and examples for those frozen routes.
It must not replace `selected_capability_ids`, route sequences, or bindings.
`操作步骤` is a five-step SOP (choose one route, look-up-then-ask, collect
fields, run the script, verify/report). Each numbered step has `Done when:`.
A real combination route may add one extra step for order and bindings.
