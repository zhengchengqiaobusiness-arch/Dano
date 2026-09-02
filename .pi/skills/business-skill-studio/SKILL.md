---
name: business-skill-studio
description: Record a real business web system, capture UI/API evidence, infer query/create/update/review/delete capabilities, validate them, plan safe compositions, and export an evidence-backed Agent Skill. Use when the user wants to learn an existing system from real operations, turn browser/API workflows into reusable skills, verify recorded capabilities, or package a business-system skill.
---

# Business Skill Studio

Use the registered `business_skill_*` tools. Keep the process deterministic.

## Workflow

1. **Record**
   - Call `business_skill_record_start` with the real system URL.
   - Let the user operate the headed browser, or use `business_browser_control` after a `snapshot` to perform grounded page actions.
   - If `snapshot.recentUserActions` already contains the user's manual clicks or fills, treat them as evidence and do not replay those actions with `#el-id-*` selectors.
   - Capture actual UI events, field choices, requests, responses, and statuses.
   - Call `business_skill_record_stop`.

2. **Analyze**
   - Call `business_skill_analyze`.
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
   - Call `business_skill_export`.
   - Export only primary verified capabilities (search/create/update/review/delete with caller fields) plus the lookup APIs those fields need. Do not export IM/notify/permission/login/tenant polls.
   - The exported package is Prefer HTTP: `SKILL.md` + `references/reference.md` + `scripts/execute.py`. Do not invent endpoints.

## Completion rule

A workflow is complete only when every planned step satisfies its contract completion criteria. Do not infer success from prose alone.
