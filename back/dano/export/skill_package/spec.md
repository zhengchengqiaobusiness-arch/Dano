# Self-contained skill package specification

A package is a directory that runs against the recorded business API without a
Dano runtime or an LLM. It follows Agent Skill progressive disclosure:
`SKILL.md` is the only always-read entry; `scripts/` and `references/` load on demand.

This file is the **internal generator spec**. It records what Dano actually
referenced and how those sources constrain rendering. Those references, commit
hashes, and author notes must not be copied into a consumer Skill.

## External references used by the generator

Priority is fixed. Later sources fill gaps; they do not override earlier ones
on handbook shape, routing, or disclosure.

1. Alibaba Cloud AIOps Skills
   - Repository: https://github.com/aliyun/alibabacloud-aiops-skills/tree/master/skills
   - Pinned commit: `a2497242c16a61b9396f809a22b468f0f1cd8cf9`
   - Representative samples read at that commit:
     - `skills/aiml/agentloop/alibabacloud-agentloop-evaluation/SKILL.md`
     - `skills/aiml/agentloop/alibabacloud-agentloop-dataset/SKILL.md`
     - `skills/analyticscomputing/datahub/alibabacloud-datahub-manage/SKILL.md`
     - `skills/aiml/learn/alibabacloud-pai-workspace-manage/SKILL.md`
     - `skills/aiml/sfm/alibabacloud-bailian-voice-creator/SKILL.md`
   - Adopt: description as the routing entry; choose one workflow before
     running it; ordered executable steps; preview → confirm → execute for
     writes; success and failure checks; conditional references.
   - Do not copy product-only chapters: RAM policy, Alibaba CLI install,
     Session ID / User-Agent injection, refresh compatibility, cloud cleanup,
     or account/region parameters that this executor does not use.

2. Matt Pocock `writing-great-skills`
   - User-specified entry: https://www.skills.sh/mattpocock/skills/writing-great-skills
   - Pinned historical commit: `ed37663cc5fbef691ddfecd080dff42f7e7e350d`
   - Upstream later renamed the directory to `writing-for-agents`. Keep the
     historical name as the baseline and treat the new name as an upgrade hint.
   - Adopt: predictable process, not identical wording; one checkable done
     condition per step; one fact one source; inline common rules and disclose
     branch files only with a “when to read which file” pointer; omit
     `disable-model-invocation: false`; do not invent empty split files.

3. WeChat article
   - URL: https://mp.weixin.qq.com/s/upEf0dCi3qwvpwLkRIwyWA
   - Title: `我反问面试官：“你一般怎么写skill”...`
   - Published: 2026-05-22
   - Read for this project: 2026-08-21
   - Adopt as principles, not a template: a Skill is a reusable operating
     module; it must answer when to use / not use, inputs, steps, tools,
     outputs, done, missing information, tool failure, and safety; metadata
     selects, the handbook routes, details load on demand; evaluate hit rate,
     miss rate, route match, tool success, human correction, and context cost.

Conflict order: safety and permissions; the current user request; Alibaba
handbook shape; writing-great-skills disclosure; WeChat evaluation; local
early behavior that is still valid.

## Generation-time vs consumer-time

| Period | May read | Must not appear in the finished package |
|---|---|---|
| Generation | this spec, `doc/skill-generator-ask-user-question-guide.md`, recording pipeline docs, FlowSpec, verification evidence | those paths, stage numbers, generator notes |
| Consumption | `SKILL.md`, selected route/form/option/capability files, public scripts | `generator-guides`, raw request chains, sample people/orders, Python internals |

Machine audit facts that users do not need to read stay in `CONTRACT.json`.

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
    CAPABILITIES.md
    OPTIONS.md
    INPUT_FORMS.md
    routes/
      <route-id>.md          # one file per combination route
    forms/
      <capability>.md        # only when that form is actually long
```

Do not pack `references/generator-guides/`. Those files are generator-internal
and must not enter a consumer Skill.

New packages must not generate `references/OPERATIONS.md`. Capability index,
option rules, input rules, and route methods now live in the files above.
A validator may still accept a legacy package that only has `OPERATIONS.md`.
A new package must not emit both layouts.

`SKILL.md` must have YAML frontmatter with non-empty `name` and `description`
only by default. Do not emit `version`, `compatibility`, or
`disable-model-invocation: false`. The description must say what the Skill
does, which distinct user requests trigger it, and the key boundary. After
planning finishes, `SKILL.md` is a workflow-selection handbook: user intent
maps to one atomic route or one combination route. It is not a capability
catalog, a recording log, or a longer prompt.

The body must contain `适用场景`, `不适用场景`, `选择工作流`, `组合与交接规则`,
`执行协议`, `成功、失败与停止`, and `按需读取资源`. Add auth, runtime, or
cleanup only when the running contract needs them. Every item in `执行协议`
must state a `Done when` condition.

`选择工作流` is a table of mutually exclusive routes. Combination details
link directly to `references/routes/<route-id>.md`. Atomic routes must not
tell the Agent to load combination files.

`组合与交接规则` names exactly three modes:

1. Atomic: one packed operation, collect only that operation's missing inputs.
2. Confirmed binding: only contract-listed bindings auto-fill; empty, ambiguous,
   type, or cardinality mismatch stops auto-chaining.
3. Human handoff: without a confirmed binding, finish the earlier step, show
   candidates or ask for the next required input, and resume only after a
   valid user choice.

Consumer handbook text (SKILL.md, CAPABILITIES, OPTIONS, INPUT_FORMS,
route files, when_to_use, composition, summary) must not contain:
`本页面的实际操作流程`, `能力录制`, `录制结果`, `阶段1`, `本页原子能力`,
`按用户意图选择一项`, `阶段 6` / `阶段6` / `阶段 7` / `阶段7` /
`阶段 8` / `阶段8`, `录制识别顺序`, `FlowSpec`, `fingerprint`,
`capability_id`, `x-dano`, `规划依据`, `原子能力`, `一页面对应一个 Skill`,
`原样来自`, `生成器`.
「已确认绑定」may appear as an execution rule.

Do not require an `API chain` section in consumer Markdown. Verification
evidence stays in `CONTRACT.json` or executor-owned FlowSpec fields.

`references/CAPABILITIES.md` is a business capability index: when to use,
read/write, input/output overview, done check, main risk. It must not copy
full field tables or discovery history.

`references/OPTIONS.md` explains how candidates are obtained at runtime.
Recorded sample options are not runtime defaults. Multiple results must not
silently pick the first item.

`references/INPUT_FORMS.md` is an on-demand `ask_user_question` contract.
Keep the generator-guide behaviors: group current-step fields, stable question
ids, correct controls, no re-ask for fixed/system/bound values, confirm writes,
runtime defaults instead of historical samples. If the file exceeds a
reasonable read, keep the index here and split only the long capability into
`references/forms/<capability>.md` with a direct conditional pointer.

The packed contract is copied as-is: field identity, option maps, request
templates, sample evidence, defaults, and success rules must not be rewritten
or dropped. Stage 8 consumes upstream capability schemas; it must not invent
fields, required lists, or derived-from-query marks from route steps.
If compilation emptied a capability `input_schema`, restore the FlowSpec
capability schema that the planner already used. Do not add fields that
exist only on route steps.
Route `required_user_inputs` and `input_sources[user]` must be a subset of
that same capability schema. `INPUT_FORMS.md`, `CONTRACT.json`, scripts'
PLAN, and route files must project the same caller fields. If the schema has
no caller fields, say so and collect “按已有契约收集”; do not write
`` `id` 由用户提供 ``.
`SKILL.md` description is the routing trigger. The 适用场景 body must not
repeat that identity paragraph. Scripts may still contain PLAN JSON;
the handbook must not tell the Agent to read PLAN, `x-dano`, or
`capability_id`. Route files may name business fields such as `id` only when
the capability schema has them, and they must not expose internal step keys
or `field←source` markers. A consumer package must not contain `__pycache__`
or `.pyc` files.

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
- `routes` with `steps`, `input_sources`, `bindings`, `checkpoints`,
  `composition_mode`, `done_when`, and `failure_behavior`
- `unused_capabilities`
- `source_flow_fingerprint`
- `intent_branches` when the compiler produced them; keep the compiled
  natural-language branches so auditors can map user wording to routes
  and detect dropped combinations

Public scripts and `SKILL.md` may only name selected capabilities. Unused capabilities stay in the original FlowSpec but must not appear as packed scripts. Packages without these fields remain valid single-capability exports.

The model may only rewrite `when_to_use` and examples for those frozen routes.
It must not replace `selected_capability_ids`, route sequences, steps,
bindings, or checkpoints.

## Quality checks that do not depend on business-API discovery

These are Skill-handbook metrics, not interface-correctness metrics:

- Distinct described branches become a route or a clarification; silent drops = 0.
- Illegal auto-bindings = 0.
- Writes missing confirmation = 0.
- Cases that should stop but continue = 0.
- Every documented step has a decidable done condition.
- Loading a combination route file for a single atomic request = 0.

Runnable checks live in `back/tests/test_skill_generation_quality.py`.
They measure Skill hit/miss from the user's original utterance, complete
route match, silent branch drops, silent sequence drops, unnecessary asks,
should-stop-but-continued, tool-invocation readiness, human-correction
surface, and on-demand context load. They do not score whether a business
API field was discovered correctly.

Live tool-call success rate and live human-correction rate need runtime
telemetry. Generation-time checks only prove the handbook exposes the
measurement points: every step has a capability and a done condition,
writes confirm, and unbound write combinations stop at a checkpoint.

`route_id` must be generated from the full capability sequence and stay
globally unique. Package validation rejects duplicate IDs before file-set
comparison, so two combination contracts cannot silently share one file.
