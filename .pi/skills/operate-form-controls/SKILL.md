---
name: operate-form-controls
description: Operate recorded business forms from snapshot evidence using click/fill/choose/select. Use when filling, selecting, opening pickers, or submitting in the Studio browser; prefer this over kit-specific TypeScript class lists.
---

# Operate form controls

You decide how to use the current `snapshot`. The browser tool only clicks and types. Do not assume Element/Ant/Arco class names.

## Grounded loop

1. `snapshot`. Read `formFields` / `todoFields` / `controls` in the active `scope` (`page` or `dialog`).
2. For each empty eligible field (skip upload/attachment only), pick one primitive:
   - text / textarea / number → `fill` with that field's `selector`
   - native select → `select`
   - combobox / `kind=select` → `choose` with visible option text, not a typed sample
   - date → `fill` or `choose` with `YYYY-MM-DD`
   - `kind=picker` or readonly「请选择」→ `click` the field selector, `snapshot`, then `click` a real row/option/tree node, then confirm if a confirm button exists
3. `snapshot` after a dialog opens or closes. Only operate inside the open dialog.
4. After fields are filled, `submit-form` or `click` the submit/search button in this scope.

## Rules

- Locate by the snapshot selector (`label=`, `placeholder=`, `role=`). Never `#el-id-*`.
- Two controls with the same label (`部门名称-2`) are two fields. Use `groupIndex` / the exact selector.
- 「请选择」and required number `0` are empty.
- Do not click the page title, mask, pagination, or sidebar to dismiss a popup.
- Do not press Escape.
- Login page: do not type. Wait for manual takeover.
- When the user requires every field except upload, the first `exercise-form` is the authoritative whole-form action. If it fails, finish leftover `todoFields` with primitives.
- Three consecutive real failures → stop and wait for manual takeover. Do not loop.

## Success

A write is successful only when a business-success network response is recorded. A green button is not enough.