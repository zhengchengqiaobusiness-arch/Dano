---
name: business-skill-studio
description: Record a real business web system, capture UI/API evidence, infer query/create/update/review/delete capabilities, validate them, plan safe compositions, and export an evidence-backed Agent Skill. Use when the user wants to learn an existing system from real operations, turn browser/API workflows into reusable skills, verify recorded capabilities, or package a business-system skill.
---

# Business Skill Studio

Use the registered `business_skill_*` tools. Keep the process deterministic.

This host is Windows. The bash tool is disabled. Page recording uses `business_skill_record_start` and `business_browser_control` only. Never use bash, WSL, or filesystem tools to inspect or click the business page. If a shell is unavoidable, use powershell. Do not retry a failed shell/read command.

## Workflow

1. **Record**
   - Call `business_skill_record_start` with the real system URL. Put every operation explicitly requested by the user into `expectedOperations` (for example `查询+新增` becomes `["query", "create"]`). If the user requires every field except upload/attachment, also pass `completeFieldCoverage: true`; review then rejects any blank visible field or empty business-detail collection instead of exporting a partial Skill. These caller-defined requirements are persisted unchanged; later UI clicks and network evidence never invent extra requirements.
   - Let the user operate the headed browser, or use `business_browser_control` after a `snapshot`.
   - Snapshot returns `formFields` / `todoFields` / `controls` plus `operationInventory` and enabled `availableOperations` across the main page and visible iframes. This is the source of truth for the page × operation checklist. When the user asks for every operation, exercise every applicable inventory item, not only the menu entry. Actual business clicks and correlated requests remain evidence for capability inference, including unfamiliar button text whose operation is established by the real HTTP method/path; they do not change the caller-defined completion contract.
   - If the user says to run 查询/新增/修改 and fill every field except upload: snapshot, then call `exercise-form` once as the authoritative whole-form action. That single call must inspect and fill every currently visible eligible field in one pass; it must not intentionally split the initial form into one-field tool calls. A second or third call may repair returned `todoFields`, a returned failure, newly revealed `新增一行` rows, tree/cascader choices, or format constraints. Then call `submit-form` once; if it returns `ok: false`, repair from its returned evidence before retrying, and never submit again after `ok: true`. Do not skip optional or blank fields, table rows revealed by `新增一行`/`添加明细`, or a second control that shares a label (`部门名称-2`). Do not treat「请选择」or a required number `0` as already filled. A prompt like「请输入项目名称」next to a select is its own field. An empty avatar/plus well in a process rail is its own field; the node title is not a filled value. Upload/attachment widgets are the only skip. Locate a field by its own snapshot selector; never the first input in the dialog. Choosers must pick a real option or tree node, not a typed sample. `submit-form` is not success unless a form request is seen or the form closes.
   - Hard stop: only actual failures consume the repair budget, and a successful automatic operation clears the consecutive-failure streak. After the first or second failure, re-read the current scope and repair the returned error, `todoFields`, or selector; do not blindly replay an unchanged selector. On the third consecutive failed automatic operation, the same tool call pauses and Studio switches to manual mode with a takeover card. A `snapshot` never requests takeover by itself. Do not issue another tool call, stop, analyze, or start a fallback path while takeover is pending. Wait until the person finishes the current page and clicks「我已完成，继续自动执行」; then continue from the returned `resumedAfterManualTakeover` snapshot. The failure budget is scoped to the current URL/form; a new page gets a fresh budget. 无法点击 / 无法复现 / 卡死 loops are forbidden.
   - `exercise-form` must leave required numbers greater than 0 and later date/time fields later than earlier ones, including both ends of a range picker (`rangeIndex`). If a write page computes duration from dates, fix the dates; do not stop because a required number shows `0`.
   - After a write submit: snapshot once. If there is still no successful write response after automatic attempts, let the built-in manual takeover complete before continuing. Do not loop. Do not analyze or export a planned 新增/修改/审核/删除 with no successful write response. `submit-form` only clicks a button in the active scope; when both search and write buttons exist, it prefers 提交/确定.
   - Do not click the page title to close pickers and do not press Escape.
   - If `snapshot.recentUserActions` already contains the user's manual clicks or fills, keep those values; still fill remaining empty fields when complete coverage was requested. Manual clicks in Pi automatic mode, or typing in the headed page, must record the same field labels, final values, options and write request as `exercise-form`. A bare click without a form inventory is not enough to export a Skill.
   - When the user says 手动录制完毕, stop this session and analyze it. Do not start a new recording just because Chinese labels do not match request keys; bind by unique values, recorded lists, and page-context IDs from this session.
   - If this session already has a successful write request and lookup queries (product/simple-list, get-count, dict, same-resource scalar fetches, and similar), do not `record_start` again when review reports a backend interpretation problem. Review automatically rebuilds the affected session from raw UI/network evidence without another paid model call and repeats until the finding set is stable. If disambiguating evidence is genuinely absent, use browser automation to collect a targeted second sample; manual takeover is only for a page that cannot be operated, never for editing generated fields, bindings, endpoints, or contracts. A second recording that only clicks a picker must not replace or drop the recorded write capability.
   - Capture actual UI events, field choices, requests, responses, and statuses.
   - Call `business_skill_record_stop`.

2. **Analyze**
   - Call `business_skill_analyze` with the `sessionId` returned by `business_skill_record_stop`. Do not analyze the entire recording history.
   - Analyzing one session merges into the catalog by method+path. It must not delete 主能力 that another session already recorded. The analyze/validate report only covers this session's page. Do not treat another page's unverified 新建 as this session's 回到页面补录.
   - The analyzer may improve names/descriptions with the model, but it must not invent endpoints, request fields, response fields, candidates, or evidence IDs.
   - Report 主能力 and 字段候选 separately. 主能力 is only this page's 查询/新建/修改/审核/删除. A recorded search/filter that returns a paged or listed result is 查询, even when the path is `getXxx`/`selectXxx`/`fetchXxx` and does not end in `/list`/`/search`/`/query`. A recorded conversational ask (`sys_query` / `question` / `prompt`) is also this page's 查询, even when the path looks like chat/send. Session setup (`save_*chat*`, `getappid`) and homepage widgets are 字段候选, not 主能力. User/product pickers, stock lookups, IM, and login are not 主能力. Do not start a new recording just because the search API name is unfamiliar.
   - For each write field the caller will not type, test origin hypotheses against the recording. Write a rule only when exactly one hypothesis uniquely explains the value. Never freeze a recorded business sample.

3. **Review**
   - Call `business_skill_validate` with the same `sessionId`. This is the gate. Inference is not assumed correct. If the process restarted, the last analyzed session is remembered; still pass `sessionId` when you have it.
   - **Pass** only when the tool returns `审核通过`: every operation listed in the recording's `expectedOperations` exists; every 主能力 is `verified`; every write non-caller field has a unique origin rule; computed formulas do not use IDs, enums, or timestamps as operands; picker/user-select fields expose a recorded query instead of a frozen page enum; every key in the successful write request has a field or an assemble rule; and every lookup used by `from:` / candidates is usable. Then export is allowed.
   - **Fail** when it returns `审核未通过`. Do not export. Do not treat “some checks passed” as passed. A write Skill that freezes a user picker as a static enum, uses `computed:day - type`, or drops `startUserSelectAssignees` is not passed.
   - Read the **entire** findings list first. Group by stage. Do not bounce back after the first failure.
     - One pass only: collect every `回到页面补录` item. If the successful write and its lookups are already recorded, do not collect them as 补录 and do not ask the person to repair generated content. Let the deterministic review repair rebuild field ownership, candidates, bindings, completion criteria, and transport from the evidence.
     - Then `validate` once. Review performs its bounded evidence-only repair loop internally; do not trigger repeated paid analysis.
     - If targeted evidence is missing, return to the browser and automatically collect only that evidence. The three-failure human takeover remains limited to operating the page.
   - Preserve manually edited titles/descriptions, but do not ask a person to edit `.business-skill-studio/catalog/capabilities.json` to fix analyzer output. Explicit `business_skill_approve_binding` remains only for a user-authored cross-capability composition decision, not as a repair for field recognition.

4. **Plan**
   - Call `business_skill_plan` for a natural-language goal.
   - Route to a single verified atomic capability when possible.
   - Chain capabilities only through `approved: true` bindings.
   - If multiple targets match, required inputs are missing, a binding is absent, or a result is ambiguous, ask the user.
   - Execute planned operations immediately. Do not ask the user to confirm page actions or writes.

5. **Export**
   - Export only after Review returned `审核通过`. If it did not, go back; do not export a blocked catalog.
   - Call `business_skill_export` with the page's Chinese business name, such as `采购订单`, and the same `sessionId`. Do not export a newer unrelated recording.
   - The package has two layers: primary recorded operations (查询/新增/修改/审核/删除) and lookup APIs used only to pick field values. Report them separately. Do not tell the user that lookup APIs, IM, login, or unrecorded edit/delete are exported business capabilities.
   - The exported package is a handbook, not a business spec: `SKILL.md` (router + composition rules) + `references/CAPABILITIES.md` + `references/INPUT_FORMS.md` + `references/OPTIONS.md` + `references/PLAYBOOK.md` + `references/CONTRACT.json` + `scripts/execute.py`. Do not invent endpoints.
   - Report 主能力 and 字段候选接口 separately. Lookup APIs are for field values only.
   - For every write-capability field the caller will not type, infer an evidence-backed origin rule from the recording: whatever uniquely explains the value (another recorded query, request-side calculation, copy of another field, true system default, session, generated id, and so on). Write that rule into the contract and handbook. Do not freeze a recorded business sample as a fixed value. Do not export a write Skill that still has unexplained request keys. Caller fields still must not freeze recorded samples.
   - After export, read the generated `SKILL.md` and `INPUT_FORMS.md`. If a field name, enum, lookup, date format, or write path is wrong, fix the generator or evidence mapping and re-export. Do not hand a defect list back to the user.

## Completion rule

A workflow is complete only when Review returned `审核通过` and every planned step satisfies its contract completion criteria. Do not infer success from prose alone. If Review is blocked, collect every finding, then follow `下一步` in one pass before export. If the user asked for 新增/修改 and the catalog has no verified create/update, go back to the page once, follow recorded manual steps or ask the user to record manually, and record a successful submit; do not click-loop. Do not hand that gap back as a finished Skill.
