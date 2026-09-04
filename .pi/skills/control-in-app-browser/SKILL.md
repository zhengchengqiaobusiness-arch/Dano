---
name: control-in-app-browser
description: Control the active Business Skill Studio headed browser through grounded snapshots and real page actions. Use when the agent needs to inspect, navigate, click, fill, select, press keys, wait, or screenshot the browser while recording a business workflow; this is the Pi-portable adapter, not the Codex desktop private browser bridge.
compatibility: Requires the project business_browser_control tool and an active business_skill_record_start session.
---

# Control In App Browser — Pi portable adapter

Use `business_browser_control` only against the active recorder browser. The page kit may be Element Plus, Ant Design, Arco, or native HTML. Do not special-case one business page.

This host is Windows. The bash tool is disabled. Never call bash, WSL, or filesystem tools to operate the page. If a shell is unavoidable, use powershell. Do not retry a failed shell/read command.

## Grounded loop

1. Call `snapshot` at the start, and again after navigation, dialog/drawer open or close, or submit. Do not snapshot after every field.
2. Treat `formFields` / `todoFields` / `controls` as the checklist for the **active scope** (`page` or `dialog`). `operationInventory` lists recognized business actions in the main page and visible iframes; `availableOperations` is its enabled operation set. When every operation was requested, cover this page × operation inventory instead of stopping after navigation. Background page buttons disappear from `controls` while a dialog is open. Placeholder text such as「请选择」is empty, not filled. Do not use Vue `prop` as a field name.
3. If the user asked to fill every field (上传/附件除外), call `exercise-form` once as the authoritative whole-form action: its first call must inspect and fill all currently visible eligible fields in one pass. Do not decompose the initial form into one-field tool calls. A second or third `exercise-form` is allowed only for returned `todoFields`, newly revealed `新增一行`/`添加明细` rows, a second shared-label control (`部门名称-2`), tree/cascader nodes, or format repairs. Then call `submit-form`. The first or second failed automatic operation is not a stop. Re-read the current scope and repair the returned error or `todoFields`; for a failed direct selector, use a newly grounded selector instead of blindly replaying the unchanged selector. Do not skip optional or blank fields. Locate must be unique: use the snapshot selector for that field, never the first input in the dialog. Empty assignee wells in a process rail (avatar/plus, not only a `+` button) are fields; the node title is not a filled value. Choosers open by clicking that field's shell once; pick a visible option, tree node, or dialog row. Do not type a sample into a select filter. `submit-form.ok` is false unless a form request is seen or the form closes.
4. Login is never an automatic form attempt. When Studio detects a login page, the current start/control call pauses immediately with `loginRequired: true` and shows the manual-takeover card. Do not click, fill, submit, navigate, or retry. Wait for the person to log in and click「我已完成，继续自动执行」; continue only after the pending call returns `resumedAfterManualTakeover: true`.
5. Only actual failures consume the repair budget; successful automatic operations clear the consecutive-failure streak. On the third consecutive failed automatic operation in the same page/form scope, that same tool request pauses automatically and Studio shows a manual-takeover card. A read-only `snapshot` never requests takeover by itself. Do not stop the recording, analyze, start another path, or issue another browser command while takeover is pending. Wait for the person to complete the current page and click「我已完成，继续自动执行」; the pending tool call then returns `resumedAfterManualTakeover: true` with a fresh snapshot. Continue from that snapshot. Navigating to another page starts a fresh budget. Do not `record_stop` + analyze a planned 新增/修改 with no successful write response. 无法点击 / 无法复现 / 卡死 loops are forbidden: no blind replay of an unchanged selector and no fourth consecutive failed repair.
   Manual takeover only repairs page operation. Wrong endpoint/field/source/binding/completion inference must be repaired from the recorded UI and network evidence; never ask the person to edit generated Skill content.
6. `recentUserActions` and `filled: true` are already observed. Do not replay those values unless the user asked to change them. Empty fields still must be filled when the user required complete coverage. Manual clicks or typing in automatic mode must record the same field labels, final values and form inventory as `exercise-form`. A bare click is not enough. When the user says 手动录制完毕, stop this session and analyze it; do not start a new recording because Chinese labels do not match pinyin request keys.
7. Prefer snapshot selectors: `placeholder=`, `label=`, `role=`, `text=`. Never use generated `#el-id-*` selectors.
8. Dropdowns: `choose` with the field selector and the visible option text in one call. Dates: `fill` or `choose` with `YYYY-MM-DD`. Never click a bare day number like `text=2` on the whole page, and never click the modal mask.
9. If a dialog is open, only operate inside it. Do not click background buttons such as 新增 again, and do not press Escape.

## Write safety

- Execute ordinary business click, fill, select, choose, press, submit, and navigation immediately. Login is the exception and must pause without any automatic attempt.
- Filling a form is not proof that a business change succeeded. Success comes from captured network evidence and later capability validation.

## Recording objective

The browser is an evidence source. Prefer actions that expose actual field labels, real select candidates, request method/URL/body, response status/JSON, and the UI trigger that caused the request.
