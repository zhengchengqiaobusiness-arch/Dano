# #465 / #477 final acceptance audit — 2026-09-29

Release pair: Dano `0.2.67`, published `@josephyoung/pi-openviking@0.1.19`,
official OpenViking `v0.4.20`. No production deployment is included in this merge.

The user cancelled the independent second OA Browser identity case and billing/
cost acceptance on 2026-09-29. Neither is counted as passed. Existing real-service
isolation tests remain required and retained. Earlier separate-consent and visible
configuration requirements were replaced by default-on, management-only behavior.

## Requirement audit

| PRD / Spec | Final evidence and disposition |
|---|---|
| AC-01/02; T-01 | Independent npm publication, exact lockfile and native image; retained ordinary Pi CLI save/source/new-session recall and loader checks. Extension 266 tests; PR18 published 0.1.19. |
| AC-03; T-08 | Real OA Browser explicit and automatic save, ready/source management, post-restart new-chat recall; frozen recall 59/60 correct and sources 54/60. |
| AC-04; T-05 | 18 real MiMo/native Pi selection attempts: 15 exclusions create no automatic operation, three positive controls create one each. Cancelled sources correctly blocked; raw scorer errors retained. |
| AC-05/06; T-02/03/11 | 60 frozen isolation case rounds plus real USER-key, forged-header, direct-read/export, replay and protected-file checks. Only the additional independent OA Browser identity case is cancelled. |
| AC-07; T-09 | 30 correction and 30 deletion rounds, including queued/in-flight work and old-backup recovery. Native final image restores two owners/five documents/nine events and verifies both fresh readbacks. |
| AC-08; T-10 | Default-on and management-only Browser proof; 30 authorization rounds include pause/retry/restart races. Explicit opt-out persists, no historical backfill. |
| AC-09/10; T-04/06/07 | 43 lifecycle and 84 recovery tests, real race/recovery matrix, three retained credential-rotation exercises, truthful processing/failure and no stale-context collection. |
| AC-11; T-12 | Retained real Browser text, image description, model bash, form/Field Assist, generic Skill and compression evidence; SSE integration retained. Unrelated OA business regressions are outside scope. |
| AC-12; T-13 | Clean protected builds, real multi-owner backup/recovery, upgrade and matching rollback evidence, followed by final native 0.2.67/0.1.19 restore/readback. |
| AC-13; T-14 | Frozen 80 cases × three rounds covered, including semantic review and original failures. Matched 100 requests per arm retained; wait upper bound steady p95 406.851 ms, max 1,124.202 ms, injection max 203 tokens. Thinking-disabled same 20 facts all read back, ready p95 30.705 seconds. Billing/cost portion cancelled. |

## Evidence and limitations

- [Final matrix](evidence/issue477-final-matrix-supplement-20260928.json)
- [Waiting measurements and historical cost data](evidence/issue477-wait-cost-supplement-20260928.json)
- [Durable private evidence archives and hashes](evidence/issue477-acceptance-archive.md)
- [Detailed chronological report](issue477-memory-release.md)
- [Targeted final repairs and scope changes](issue477-remaining-acceptance.md)

Both Standards and Spec reviews reported no new findings after the final runtime
repairs. Type/Svelte checks and native build passed. Historical full-suite failures
remain recorded; this report does not claim a clean full-suite run. Original
unsuccessful attempts are retained alongside repaired-path repetitions, rather
than rewritten as passes. The final change after these checks is documentation
only; JSON parsing and `git diff --check` validate it.

Acceptance is complete under the revised scope. Merge/issue closure are tracked
separately from acceptance; `fullGoalComplete` stays false until those actions
and scoped cleanup have actually completed.
