# Agent guidance

This repository builds evidence-backed business Agent Skills.

Rules:
1. Never invent endpoints, fields, enum values, success criteria, or bindings.
2. Treat recorded network/UI evidence as the source of truth.
3. Keep credentials and secret-bearing headers out of generated contracts.
4. A write operation (create/update/review/delete) requires explicit confirmation at execution time.
5. Automatic composition is allowed only through `approved: true` bindings.
6. If a target, capability, binding, or result is ambiguous, stop and ask the user.
7. Only `validation.status = "verified"` capabilities may be exported.
8. Preserve editable generated descriptions; do not overwrite manual edits unless requested.
