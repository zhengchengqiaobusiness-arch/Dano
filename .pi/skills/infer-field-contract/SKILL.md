---
name: infer-field-contract
description: Bind recorded request keys to page evidence and decide caller vs system vs lookup/computed rules. Use when analyzing a Business Skill Studio session, repairing field ownership, or when business_skill_analyze / business_skill_infer_fields runs.
---

# Infer field contract

You judge fields. TypeScript only clusters evidence and keeps request keys. Do not invent paths, endpoints, or values.

## Input

You receive, per capability:

- `id`, `method`, `pathTemplate`
- flattened request keys with recorded values
- UI observations: `name`, `label`, `value`, `type`, `options`
- other capabilities in this session (for `from:` lookups)

Only patch keys that already exist in `inputForm`.

## Bind

1. Same `name` as a visible editable control → `caller`. Use the page label.
2. Same recorded value as exactly one observation, and that value is distinctive (not `0`, `""`, `false`, `1`, `true`) → `caller`.
3. A chooser row display uniquely joins a recorded list row `id`/`code` → `caller` on the id key; label from the row.
4. A write key equals a prior query response leaf, joined through a caller field (`via`) with exactly one matching row → `source=binding`, `defaultRule=from:<queryId>:<jsonPath>|via:<callerName>`.
5. A number in the write request is exactly computable from other request fields (dates → days, qty*price → amount) and operands are not ids/enums → `computed:<expr>`.
6. Otherwise → `system`, `defaultRule=literal:<exact recorded JSON>`. Keep empty objects, empty arrays, `0`, and `null`.

Never bind leftover 1:1 just because one field remains. Never use synonym dictionaries. If two observations fit, leave it `system` + literal.

## Widgets

Use the observed control: text / textarea / number / date / boolean / select / multiselect / json. A dialog/tree/table picker is still `select` plus a candidate query if one was recorded.

## Candidates

Static options only if the page actually showed them. Dynamic candidates only if this session recorded a query whose response list uniquely supplies `id`+display for that field. Point `candidateCapabilityId` at that query id.

## Output

Return structured patches only. `sourceDetail` in concise Chinese. Do not drop request keys.