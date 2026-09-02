---
name: control-in-app-browser
description: Control the active Business Skill Studio headed browser through grounded snapshots and real page actions. Use when the agent needs to inspect, navigate, click, fill, select, press keys, wait, or screenshot the browser while recording a business workflow; this is the Pi-portable adapter, not the Codex desktop private browser bridge.
compatibility: Requires the project business_browser_control tool and an active business_skill_record_start session.
---

# Control In App Browser — Pi portable adapter

Use `business_browser_control` only against the active recorder browser.

## Grounded loop

1. Call `snapshot` at the start, and again only after navigation, dialog open/close, or submit. Do not snapshot after every field.
2. Read `recentUserActions` and any control with `filled: true`. Those are already observed user operations; do not repeat them unless the user asked to change the value.
3. Choose a snapshot selector. Prefer `placeholder=`, `label=`, `role=`, or `text=`. Never use generated `#el-id-*` selectors.
4. Use one action: `goto`, `click`, `fill`, `select`, `choose`, `press`, `wait`, or `screenshot`.
5. For dropdowns and comboboxes, call `choose` with the field selector and the visible option text in one step. Do not click the inner input and wait for a timeout.
6. For dates, `fill` or `choose` the field with `YYYY-MM-DD`. Do not click a bare day number like `text=2` on the whole page, and never click the modal mask or blank overlay.
7. Keep actions inside the open dialog or picker. If a dialog is open, do not click page-background buttons such as 新增 again.

## Write safety

- Execute click, fill, select, choose, press, submit, and navigation immediately. Do not ask the user to confirm page operations.
- Filling/selecting a form is not proof that a business change succeeded. Success comes from captured network evidence and later capability validation.

## Recording objective

The browser is an evidence source. Prefer actions that expose:
- actual form field names and labels;
- real select/combobox candidates;
- request method, URL, query/body;
- response status and JSON;
- the exact UI trigger that caused the request.
