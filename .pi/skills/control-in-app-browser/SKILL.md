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
2. Treat `formFields` / `todoFields` / `controls` as the checklist for the **active scope** (`page` or `dialog`). Background page buttons disappear from `controls` while a dialog is open. Placeholder text such as「请选择」is empty, not filled. Do not use Vue `prop` as a field name.
3. If the user asked to fill every field (上传/附件除外), call `exercise-form` once. If `todoCount > 0` or `ok` is false, call `exercise-form` once more. Then `submit-form`. Do not click, fill, or choose individual fields after a failure. Locate must be unique: use the snapshot selector for that field, never the first input in the dialog. Empty assignee wells in a process rail (avatar/plus, not only a `+` button) are fields; the node title is not a filled value. Choosers open by clicking that field's shell once; pick a visible option or dialog row. Do not type a sample into a select filter. `submit-form.ok` is false unless a form request is seen or the form closes. If it is false, stop. Never click the page title or a heading to close a date picker or dropdown, never press Escape, and do not screenshot-loop.
4. `recentUserActions` and `filled: true` are already observed. Do not replay those values unless the user asked to change them. Empty fields still must be filled when the user required complete coverage.
5. Prefer snapshot selectors: `placeholder=`, `label=`, `role=`, `text=`. Never use generated `#el-id-*` selectors.
6. Dropdowns: `choose` with the field selector and the visible option text in one call. Dates: `fill` or `choose` with `YYYY-MM-DD`. Never click a bare day number like `text=2` on the whole page, and never click the modal mask.
7. If a dialog is open, only operate inside it. Do not click background buttons such as 新增 again, and do not press Escape.

## Write safety

- Execute click, fill, select, choose, press, submit, and navigation immediately. Do not ask the user to confirm page operations.
- Filling a form is not proof that a business change succeeded. Success comes from captured network evidence and later capability validation.

## Recording objective

The browser is an evidence source. Prefer actions that expose actual field labels, real select candidates, request method/URL/body, response status/JSON, and the UI trigger that caused the request.
