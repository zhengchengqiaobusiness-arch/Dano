---
name: control-in-app-browser
description: Control the active Business Skill Studio headed browser through grounded snapshots and real page actions. Use when the agent needs to inspect, navigate, click, fill, select, press keys, wait, or screenshot the browser while recording a business workflow; this is the Pi-portable adapter, not the Codex desktop private browser bridge.
compatibility: Requires the project business_browser_control tool and an active business_skill_record_start session.
---

# Control In App Browser — Pi portable adapter

Use `business_browser_control` only against the active recorder browser.

## Grounded loop

1. Call `snapshot` before selecting a target.
2. Read `recentUserActions` and any control with `filled: true`. Those are already observed user operations; do not repeat them unless the user asked to change the value.
3. Choose a snapshot selector. Prefer `placeholder=`, `label=`, `role=`, or `text=`. Never use generated `#el-id-*` selectors.
4. Use one action: `goto`, `click`, `fill`, `select`, `press`, `wait`, or `screenshot`.
5. After navigation, dialog changes, or form submission, call `snapshot` again.
6. Keep actions small so the recorder can correlate UI events to requests.

## Write safety

- Execute click, fill, select, press, submit, and navigation immediately. Do not ask the user to confirm page operations.
- Filling/selecting a form is not proof that a business change succeeded. Success comes from captured network evidence and later capability validation.

## Recording objective

The browser is an evidence source. Prefer actions that expose:
- actual form field names and labels;
- real select/combobox candidates;
- request method, URL, query/body;
- response status and JSON;
- the exact UI trigger that caused the request.
