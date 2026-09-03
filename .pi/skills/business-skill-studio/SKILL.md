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
   - If the user says to run 查询/新增/修改 and fill every field except upload: snapshot, call `exercise-form` once, and if `todoCount > 0` call it once more, then `submit-form`. Do not click or fill fields one by one after a failure. Do not skip optional or blank fields. Do not treat「请选择」or a required number `0` as already filled. A prompt like「请输入项目名称」next to a select is its own field. An empty avatar/plus well in a process rail is its own field; the node title is not a filled value. Upload/attachment widgets are the only skip. Locate a field by its own snapshot selector; never the first input in the dialog. Choosers must pick a real option, not a typed sample. `submit-form` is not success unless a form request is seen or the form closes. If `submit-form.ok` is false, stop.
   - `exercise-form` must leave required numbers greater than 0 and later date/time fields later than earlier ones, including both ends of a range picker (`rangeIndex`). If a write page computes duration from dates, fix the dates; do not stop because a required number shows `0`.
   - After a write submit: snapshot. If a toast/form error remains or the write dialog/page is still open, repair the invalid fields (or call `exercise-form` / `submit-form` again) until a successful write network response is recorded. Do not stop, analyze, or export while a planned 新增/修改/审核/删除 has no successful write response. `submit-form` only clicks a button in the active scope; when both search and write buttons exist, it prefers 提交/确定.
   - Do not click the page title to close pickers and do not press Escape.
   - If `snapshot.recentUserActions` already contains the user's manual clicks or fills, keep those values; still fill remaining empty fields when complete coverage was requested.
   - Capture actual UI events, field choices, requests, responses, and statuses.
   - Call `business_skill_record_stop`.

2. **Analyze**
   - Call `business_skill_analyze` with the `sessionId` returned by `business_skill_record_stop`. Do not analyze the entire recording history.
   - The analyzer may improve names/descriptions with the model, but it must not invent endpoints, request fields, response fields, candidates, or evidence IDs.
   - Report 主能力 and 字段候选 separately. 主能力 is only this page's 查询/新建/修改/审核/删除. User/product pickers, stock lookups, IM, and login are not 主能力.
   - For each write field the caller will not type, test origin hypotheses against the recording. Write a rule only when exactly one hypothesis uniquely explains the value. Never freeze a recorded business sample.

3. **Review**
   - Call `business_skill_validate`. This is the gate. Inference is not assumed correct.
   - **Pass** only when the tool returns `审核通过`: every 主能力 is `verified`; every write non-caller field has a unique origin rule; computed formulas do not use IDs, enums, or timestamps as operands; picker/user-select fields expose a recorded query instead of a frozen page enum; every key in the successful write request has a field or an assemble rule; and every lookup used by `from:` / candidates is usable. Then export is allowed.
   - **Fail** when it returns `审核未通过`. Do not export. Do not treat “some checks passed” as passed. A write Skill that freezes a user picker as a static enum, uses `computed:day - type`, or drops `startUserSelectAssignees` is not passed.
   - Read `下一步` and go back to that stage:
     - `回到页面补录`：missing request, failed submit, missing UI fill, or no page operation. Resume recording, fix the page, `record_stop`, then analyze and validate again.
     - `补证据后重新分析再验证`：a field/query/formula was not uniquely explained. Record the missing lookup or calculation if needed, then `analyze` the same session and validate again. Do not freeze the sample.
     - `需要人工改目录或平台后再验证`：platform limitation or catalog edit. Stop and tell the user what is blocked.
   - The catalog is still editable at `.business-skill-studio/catalog/capabilities.json`. Preserve manual descriptions. Approve cross-capability flow only with `business_skill_approve_binding`. Set dynamic candidates only with `business_skill_set_dynamic_candidates`.

4. **Plan**
   - Call `business_skill_plan` for a natural-language goal.
   - Route to a single verified atomic capability when possible.
   - Chain capabilities only through `approved: true` bindings.
   - If multiple targets match, required inputs are missing, a binding is absent, or a result is ambiguous, ask the user.
   - Execute planned operations immediately. Do not ask the user to confirm page actions or writes.

5. **Export**
   - Export only after Review returned `审核通过`. If it did not, go back; do not export a blocked catalog.
   - Call `business_skill_export` with the page's Chinese business name, such as `采购订单`.
   - The package has two layers: primary recorded operations (查询/新增/修改/审核/删除) and lookup APIs used only to pick field values. Report them separately. Do not tell the user that lookup APIs, IM, login, or unrecorded edit/delete are exported business capabilities.
   - The exported package is a handbook, not a business spec: `SKILL.md` (router + composition rules) + `references/CAPABILITIES.md` + `references/INPUT_FORMS.md` + `references/OPTIONS.md` + `references/PLAYBOOK.md` + `references/CONTRACT.json` + `scripts/execute.py`. Do not invent endpoints.
   - Report 主能力 and 字段候选接口 separately. Lookup APIs are for field values only.
   - For every write-capability field the caller will not type, infer an evidence-backed origin rule from the recording: whatever uniquely explains the value (another recorded query, request-side calculation, copy of another field, true system default, session, generated id, and so on). Write that rule into the contract and handbook. Do not freeze a recorded business sample as a fixed value. Do not export a write Skill that still has unexplained request keys. Caller fields still must not freeze recorded samples.
   - After export, read the generated `SKILL.md` and `INPUT_FORMS.md`. If a field name, enum, lookup, date format, or write path is wrong, fix the generator or evidence mapping and re-export. Do not hand a defect list back to the user.

## Completion rule

A workflow is complete only when Review returned `审核通过` and every planned step satisfies its contract completion criteria. Do not infer success from prose alone. If Review is blocked, follow `下一步` before export. If the user asked for 新增/修改 and the catalog has no verified create/update, go back to the page, fix the form, and record a successful submit before export. Do not hand that gap back as a finished Skill.
