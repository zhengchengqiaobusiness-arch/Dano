---
name: business-skill-studio
description: Record a real business web system, capture UI/API evidence, infer query/create/update/review/delete capabilities, validate them, plan safe compositions, and export an evidence-backed Agent Skill. Use when the user wants to learn an existing system from real operations, turn browser/API workflows into reusable skills, verify recorded capabilities, or package a business-system skill.
---

# Business Skill Studio

Use the registered `business_skill_*` tools. Keep the process deterministic.

This host is Windows. The bash tool is disabled. Page recording uses `business_skill_record_start` and `business_browser_control` only. Never use bash, WSL, or filesystem tools to inspect or click the business page. If a shell is unavoidable, use powershell. Do not retry a failed shell/read command.

## Workflow

1. **Record**
   - Call `business_skill_record_start` with the real system URL.
   - Let the user operate the headed browser, or use `business_browser_control` after a `snapshot`.
   - Snapshot returns `formFields` / `todoFields` / `controls` for the active scope (`page` or `dialog`). That checklist is the source of truth for visible fields and buttons. Analyze/export must use those labels and widgets; do not re-guess names from API keys or Vue `prop`.
   - If the user says to run 查询/新增/修改 and fill every field except upload: snapshot the list/search form, call `exercise-form` (or fill every `todoFields` item), then `submit-form` (or click 搜索/查询); open the write form, snapshot again, call `exercise-form` again, then `submit-form`. Do not skip optional or blank fields. Do not treat「请选择」or a required number `0` as already filled. A prompt like「请输入项目名称」next to a select is its own field. Upload/attachment widgets are the only skip. Date picker chrome is not a form field. Do not special-case one business page; sibling inputs, nearby labels, readonly choosers, empty + slots, radios, shadow-root fields, same-origin iframe fields, and other tabs are all fields. Choosers must pick a real option, not a typed sample. `submit-form` is not success unless a form request is seen or the form closes.
   - `exercise-form` must leave required numbers greater than 0 and later date/time fields later than earlier ones, including both ends of a range picker (`rangeIndex`). If a write page computes duration from dates, fix the dates; do not stop because a required number shows `0`.
   - After a write submit: snapshot. If a toast/form error remains or the write dialog/page is still open, repair the invalid fields (or call `exercise-form` / `submit-form` again) until a successful write network response is recorded. Do not stop, analyze, or export while a planned 新增/修改/审核/删除 has no successful write response. `submit-form` only clicks a button in the active scope; when both search and write buttons exist, it prefers 提交/确定.
   - Do not click the page title to close pickers and do not press Escape.
   - If `snapshot.recentUserActions` already contains the user's manual clicks or fills, keep those values; still fill remaining empty fields when complete coverage was requested.
   - Capture actual UI events, field choices, requests, responses, and statuses.
   - Call `business_skill_record_stop`.

2. **Analyze**
   - Call `business_skill_analyze` with the `sessionId` returned by `business_skill_record_stop`. Do not analyze the entire recording history.
   - The analyzer may improve names/descriptions with the model, but it must not invent endpoints, request fields, response fields, candidates, or evidence IDs.

3. **Validate**
   - Call `business_skill_validate`.
   - Only evidence-backed capabilities may become `verified`.
   - Write capabilities require correlated UI evidence plus a successful observed network response.

4. **Review**
   - The generated catalog is intentionally editable:
     `.business-skill-studio/catalog/capabilities.json`
   - Preserve manual business descriptions.
   - Approve automatic cross-capability data flow only with `business_skill_approve_binding`.
   - Configure dynamic form candidates only with `business_skill_set_dynamic_candidates`, using a verified query capability as the source.

5. **Plan**
   - Call `business_skill_plan` for a natural-language goal.
   - Route to a single verified atomic capability when possible.
   - Chain capabilities only through `approved: true` bindings.
   - If multiple targets match, required inputs are missing, a binding is absent, or a result is ambiguous, ask the user.
   - Execute planned operations immediately. Do not ask the user to confirm page actions or writes.

6. **Export**
   - Call `business_skill_export` with the page's Chinese business name, such as `采购订单`.
   - The package has two layers: primary recorded operations (查询/新增/修改/审核/删除) and lookup APIs used only to pick field values. Report them separately. Do not tell the user that lookup APIs, IM, login, or unrecorded edit/delete are exported business capabilities.
   - The exported package is a handbook, not a business spec: `SKILL.md` (router + composition rules) + `references/CAPABILITIES.md` + `references/INPUT_FORMS.md` + `references/OPTIONS.md` + `references/PLAYBOOK.md` + `references/CONTRACT.json` + `scripts/execute.py`. Do not invent endpoints.
   - Report 主能力 and 字段候选接口 separately. Lookup APIs are for field values only.
   - After export, read the generated `SKILL.md` and `INPUT_FORMS.md`. If a field name, enum, lookup, date format, or write path is wrong, fix the generator or evidence mapping and re-export. Do not hand a defect list back to the user.

## Completion rule

A workflow is complete only when every planned step satisfies its contract completion criteria. Do not infer success from prose alone. If the user asked for 新增/修改 and the catalog has no verified create/update, go back to the page, fix the form, and record a successful submit before export. Do not hand that gap back as a finished Skill.
