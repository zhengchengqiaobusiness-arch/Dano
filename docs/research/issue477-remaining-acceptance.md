# #465 remaining acceptance

Updated 2026-09-28. This records the unfinished PRD/Spec gates, including the user-approved default-on revision; it
does not add release requirements. #473–476 are closed; #477, #465 and Draft
[PR #490](https://github.com/zhengchengqiaobusiness-arch/Dano/pull/490) remain open.
The full historical AC/T ledger is [here](issue477-memory-release.md).

## Fixed inputs and retained passes

- Candidate Dano `0.2.65`, published `pi-openviking@0.1.18`, official
  OpenViking `v0.4.20`. Current protected image
  `9edfcd0b24cc44760aa7460964a78927e165218a44e2427a1475ac7a40f99dcd`
  contains the repaired native recovery helper. Retained `0.2.64` image
  `5c0794071128d4a62c381c6d216f9aab2982c07dd86f2039752353b0489e162c`
  established default-on initialization and the management-only Browser surface.
  Five fresh synthetic accounts verified both defaults without settings writes;
  restart preserved explicit pause/opt-out. The real OA Browser shows automatic
  ready content and its source. These affected-path checks do not close the full
  matrix; see the [current summary](evidence/issue477-0264-managed-defaults-20260928.json).
- Retained prior Dano `0.2.63` / `pi-openviking@0.1.18`, official
  OpenViking `v0.4.20`. Built protected image
  `b771b66b5480acaf63dab0b4ddcf623c94fcc612169a8d1c420dd390a8fda629`
  completed the affected Browser rerun, new-chat recall, text/bash and actual
  image upload/read. The unedited image response calls the red circle an ellipse;
  the receipt records this precision limit.
- Prior protected image
  `1297fc14a8f8ae6ab5f5527fa5eec3a6b72378bccc2bf900be2e9f24fb7e925a`
  (`0.2.62`/`0.1.17`) remains the source of the retained Browser receipts.
- MiMo `mimo-v2.5`, existing authorized private configuration, thinking off.
  Model-key replacement is not a prerequisite. Model and tokenizer bindings
  come from the private deployment configuration, not test-specific runtime code.
- Reuse the isolated stack and trusted `https://localhost:18711/` entry.
  Runtime, credentials, assets, TLS and recovery data stay outside the checkout.
- Retain current ordinary Pi CLI consent/save-ready/source/new-session/pause,
  OA single-user login/text/image/bash, Browser save/correct/forget/recall, and
  one collection/pause/export/forget/resume observation. Their receipts state
  their limits. Retain every failed attempt alongside subsequent passes.

## Remaining execution scope

The user confirmed this scope on 2026-09-28. Only #465 requirements and paths
directly affected by these changes are acceptance gates. Reuse passing checks
unless a later relevant code change invalidates them; a product version change
alone does not require rerunning them. Ordinary OA business Skills and forms are
outside this remaining scope. Retain completed image/bash checks without repeats,
and do not use unrelated full-suite failures to block this memory release.

| Group | Next concrete checks | Completion evidence |
|---|---|---|
| 1. Collection and lifecycle | Finish sensitive-data/reasoning/unconfirmed-inference exclusion, pause/in-flight overlap, reconnect/branch/dispose without duplicate collection or historical backfill. Retain default-on and management-only rendering proof. | Source/task records and relevant rendered Browser proof; original repetition requirements still apply. |
| 2. Real user isolation | Complete a second independent real OA identity: memory, sources, export/delete permissions and account switching. | Separate authenticated identities and owner-scoped Browser readbacks; no OA business regression expansion. |
| 3. Correction, deletion and fault recovery | Cover remaining queue/cache/in-flight/recovery cases, deletion non-resurrection and honest failed-save status. Reuse two-owner real recovery/rollback and all three USER rotations. | The [real recovery receipt](evidence/issue477-0265-real-recovery-20260928.json) and the remaining frozen-case attempts; no additional rotation round. |
| 4. Quality and performance | Execute the remaining 10 frozen cases x3 = 30 attempts and any cases invalidated by relevant changes. Reuse the 90 quality attempts and matched 100 on / 100 off requests with five users; add missing save-ready latency, complete waiting and extraction/embedding cost evidence. | Every attempt retained; original Spec thresholds and complete cost accounting, without clean-only filtering or repeated baseline workloads. |

After these four groups reach the agreed standards, audit AC-01–13/T-01–14,
review resulting changes, merge upstream PR #490, close #477/#465 and complete
scoped branch/runtime cleanup. Unrelated checks are not additional merge gates.

### Current overlap observation

One normal stable-preference message produced an automatic ready operation
`188bc3484d776dccbef807505043d89cc0eb085fa5a03be62e57bd511539dd4f`
and an unsolicited explicit attempt
`911c6f25a4370f1d85e7bccc638623582bcf840c58980a98782320b2667c184c`
which failed with `MEMORY_NO_EXTRACTED_FACT`. The ready document was read and
recalled correctly. Later read-only digest comparison established that the
automatic quote and fact were the exact explicit saved text. Historical raw
selector input/response was not recorded, so its inference path remains unknown.

[Pi PR #17](https://github.com/josephyoung/pi-openviking/pull/17) fixes the
confirmed acceptance gap: even if a model selects an excluded quote, the
selector rejects duplication against a valid receipt from that original source.
Independent facts and later source turns stay eligible; combined quotes remain
retryable. The version bump automatically published `0.1.18` through OIDC.
All 264 extension tests pass. Baseline and fixed versions both pass six real
MiMo selector probes, so those probes alone do not reproduce the historical
failure. See the [fix receipt](evidence/issue477-pi-0118-summary-20260928.json).
The affected final-image Browser rerun reached explicit `ready` in 35.259 seconds
while the completed automatic selection created no operation. Browser source
readback and a fresh-chat recall succeeded. The broader semantic exclusion
matrix remains open; this narrow guard and one Browser observation do not close
AC-04. See the [current Browser receipt](evidence/issue477-0263-overlap-browser-20260928.json).

### Reusable entrypoints

- Ordinary Pi: `ordinary-pi-memory-real.mjs` wraps the published standard
  launcher through `linux-cli-memory.mjs` and `cli-memory-host.mjs`. Supply the
  private service/model files plus `DANO_FIXTURE_PROVIDER`,
  `DANO_FIXTURE_MODEL` and `DANO_FIXTURE_TOKENIZER_REVISION`; mount the verified
  tokenizer assets read-only. Run in the fixed image on the isolated service
  networks. It creates a disposable account, waits for asynchronous USER
  deletion, and retains an empty test account. Failure logs remain private.
- Dano real service: reuse `protected-supervisor-http.mjs`; its synthetic
  authenticated protocol proof does not replace real OA Browser identity.
- Quality: `fixtures/issue477-evaluation.json`, SHA-256
  `554c9c0c704a9c465c1f6d4bf215656388b09ef8fd5fde356ffed67efbe013b8`.
  The `0.2.64` byte input (`58e0a812…`) is archived in the real-recovery
  bundle; this update changes candidate metadata only.
  Keep the revised expectations frozen. Historical runs retain their original
  fixture hashes. Old candidate reports cannot establish current-pair
  quality or matched workload completion. Reuse the current published-extension
  `0.2.63` measurements above where subsequent changes did not affect their paths.
- Recovery: use the shipped operator recovery commands and existing public-API
  recovery fixtures. Check ownership, exact image, matching volumes and current
  synthetic dataset before any mutation; keep credentials and original receipts
  private. Do not modify local phases to manufacture successful recovery.

## Retest rule

A failure must record its stage and actual cause or explicitly say that the
cause is still unknown. Repair operator configuration without changing the
model key or frozen expected results. Repeat only affected completed checks
after a repair, plus the final checks required by the original Spec. If a real
runtime fix requires a new package/image, record the new immutable pair and
identify which earlier evidence no longer applies before proceeding.

## Evidence storage

Full raw attempts and duplicate intermediate reports are preserved outside Git.
The [archive catalog](evidence/issue477-acceptance-archive.md) gives their location,
original paths and SHA-256 hashes. Retain future raw outputs there and commit only
brief final summaries with archive hashes. Frozen inputs, reproduction scripts,
thresholds and the unfinished gates above remain unchanged.

## Latest protocol measurement

The prior `0.2.63` / `0.1.18` pair completed 90 recall/irrelevant attempts plus matched 100 on and
100 off requests across five synthetic users. [The brief summary](evidence/issue477-0263-quality-workload-summary-20260928.json)
retains original failures, source-scorer limitations and independent public source
review. The current supplement completes 60 isolation, 30 correction, 12 deletion and 18 authorization attempts. This remains partial evidence: 10 frozen cases x3 remain;
healthy-save latency, total model cost, all lifecycle waiting, canonical rendered
profile/Browser identity and the remaining Group 1/3/4 checks still need evidence.
Use the existing authorized credentials, rendered profile and retained synthetic
runtime for the next relevant check; do not replace failures with a clean-only run.

## Default-on requirement revision

On 2026-09-28 the user explicitly replaced default-off/separate consent with
memory and configured automatic collection on by default. The live [PRD](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/465)
and [Spec](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/465#issuecomment-5674833976)
now require no consent step, preservation of explicit pause/opt-out, and fresh
source boundaries without historical backfill. Historical default-off receipts
remain historical results. The [revised frozen input](fixtures/issue477-evaluation.json)
changes only A-01/A-02/A-06 scenarios and candidate metadata; the previous input is retained byte-for-byte in the protocol archive
`20260928T061907Z-formal-0263` as `cases.json` and in Git history; all raw
attempts remain intact. Counts, repetition requirements, quality,
latency and cost thresholds are unchanged. The old Goal text's separate-consent
clause is superseded by this user instruction.

### Management entry revision

The user's subsequent instruction hides manual memory/collection configuration
and renames the remaining menu/dialog to **Memory management**. The browser
surface exposes receipts, content, correction, forgetting, clear and export.
Default-on initialization and trusted-host lifecycle controls remain in place;
UI switch checks are superseded by management-only rendering checks. Internal
pause/resume, queue and isolation checks remain required.

## Real shared-document restore and credential rotation (2026-09-28)

Two actual defects were reproduced on real OpenViking state: completed commits
retain sources in archives after working context is cleared, and checkpointed
pending operations can acquire task/archive receipts before a later clear.
Candidate `0.2.65` repairs these checks while still refusing active writers.
Its native helper passed matched-volume replay and repeated replay, preserving
all six current documents. Both owners read back exact exports and sources,
kept the newer writers, recalled them with MiMo in fresh chats, and did not
revive cleared queue content. Actual `0.2.63` rollback passed the same readbacks.
Three independently authorized public USER rotations passed with real Dano host
restarts and owner isolation. These results reuse the existing authorized keys.

The [brief receipt](evidence/issue477-0265-real-recovery-20260928.json) preserves
all original attempts and their limits. In particular, four post-clear ready
latencies were 60.293, 34.807, 75.235 and 96.980 seconds; these do not establish
the latency gate. A later old-image HTTP 500 has an unknown cause despite the
finite diagnostic retry passing. Type/Svelte and 82 focused recovery tests pass;
the failed-file serial rerun passed 330 tests. Full local suite attempts still
returned failures, so there is no clean full-suite claim. No further broad local
reruns are scheduled without a specific unresolved change.

## Current frozen-matrix supplement (2026-09-28)

The current candidate completed real-service isolation **60/60**, correction
**30/30** with source readback, and four deletion/old-ledger cases **12/12** after
targeted request repairs. Raw scoring and all failed requests remain preserved:
two correction answers and one deletion answer needed semantic review because
generic palette suggestions were mistaken for old personal preferences. Five
forget requests were rejected before mutation because their selected line ended
with repeated date metadata; selecting the exact fact without that suffix passed
and retained unrelated facts. No runtime code, frozen fact or threshold changed.

Eight additional explicit saves reached ready in 24.644–60.230 seconds. The
supplemental p95 is **60.230 seconds**, exceeding the 60-second target; prior
concurrent-save and recovery observations remain included in the evidence.
All registered memory hook types were timed in the deletion subset. These
measurements do not replace the original matched 100-request workload. Native
public receipts now supply extraction and embedding usage for its 20 retained
seed tasks; query-embedding accounting is still incomplete.

The second real OA identity and independent Browser authentication context remain
unverified. The isolated VM stopped with a hypervisor virtualization error while
starting the authorization subset; the same VM, volumes and fixed entry were
restored without rebuilding or rotating credentials. All 18 attempts for A-01/A-02/A-05/A-06/A-07/A-10 then passed through
real Dano HTTP/SSE, MiMo and OpenViking. Independent runtime roots avoided
exhausting the retained append-only identity pool; no live pool was rewritten. Full acceptance and merge are still open.

Remaining frozen cases: D-02-01, D-02-02, D-03-01, D-04-02, D-05-01,
D-05-02, A-03, A-04, A-08 and A-09 (three repetitions each).

## Native extraction latency repair (2026-09-28)

Actual native MiMo requests lacked the thinking-disable body even though chat and
collection selection had thinking off. The slow retained task spent 56.636 seconds
in native extraction and reported 1,237 reasoning tokens. The isolated OpenViking
configuration now sets `vlm.extra_request_body.thinking.type` to `disabled`; no
credentials or production configuration changed. The actual backend parameter
probe failed before this repair and passed after it.

The [same frozen seed retest](evidence/issue477-thinking-off-latency-20260928.json)
verified all 20 facts through public source reads across five users. MiMo created
17 genuine tasks (Bob grouped four facts in one task), all ready, p95/max 30.705
seconds, and zero extraction reasoning tokens. All old failures are retained.
This closes this healthy-save latency observation; query embedding/full waiting,
the remaining frozen attempts and independent real OA Browser proof remain open.

Candidate `0.2.66` additionally ignores late `agent_settled` after runtime closure
or Pi context invalidation. The regression first reproduced the stale-context
throw, then all 36 focused tests and type/Svelte checks passed. Both final reviews
reported no new findings; affected real lifecycle checks continue.

## Final targeted supplement (2026-09-28)

The remaining ten frozen cases now have three completed repetitions each:
12 pause/retry/restart races, 12 queued/in-flight deletions and six old-backup
restores. Combined retained evidence covers all 240 case rounds. This is coverage,
not a claim that every original attempt passed: original failures and literal
color-name scorer false positives remain in the private archive. Semantic recall
remains 59/60 and independently verified sources 54/60.

Real runtime deletion exposed two extension defects: missing Markdown creation
normalized outer whitespace, and review removing the last fact left a deleted
preserved URI in relocation. Pi PR #18 fixes both, passes 266 tests, and has
published `0.1.19` through the existing OIDC workflow. Dano pins that exact version
and removes its duplicate outer retry. Recovery still preserves the seal and old
state on a final transport failure. Both review axes report no new findings.

Eighteen real MiMo/native Pi selection attempts cover credentials, unconfirmed
inference, thinking, recalled content, cancelled results and positive preferences.
All expected safety outcomes held: 15 excluded inputs created no automatic
operation and all three positive controls did. The original scorer marked the
three correctly blocked cancelled sources false; raw results and this reason are
retained. This is selector/state evidence, not a second Browser identity.

[Matrix and release summary](evidence/issue477-final-matrix-supplement-20260928.json).
[Waiting and cost supplement](evidence/issue477-wait-cost-supplement-20260928.json)
correlates all 100 recall embeddings (1,290 tokens) and bounds complete request
memory waiting: steady p95 406.851 ms, maximum 1,124.202 ms. Original native
extraction token totals plus ON chat total 17,647,336 plan credits, versus
17,879,428 OFF chat credits. Native task usage does not expose exact extraction
LLM invocation count; local embedding infrastructure cost is not recorded as
zero. Complete cost reporting remains open.

The final native `0.2.67` / published `0.1.19` image now passes two-owner
older-backup restore (five documents, nine events and two remote-writer checks)
and both fresh-chat readbacks. The actual OA Browser retained login, displayed
ready records and source content through management, and recalled the automatic
monthly-report fact after container replacement. The fixed port and persistent
trusted certificate were reused. The temporary connection-refused attempt during
container startup is retained; a fresh controlled tab recovered without new login.

Lifecycle coverage is recorded by the extension's 266-test run (retry settlement,
branch divergence, scheduler fencing, timeout and shutdown) and 43 Dano tests
(shared Viewer initialization, reconnect, disposal and memory default/boundary
handling), together with the twelve real pause/retry/restart cases. No unrelated
business or broad-suite rerun is added.

Still open: independent second real OA Browser identity and complete cost reporting.
Chrome's supported browser connection is unavailable; the existing real OA
in-app session remains intact. No additional user confirmation is requested.

### Cost metadata follow-up

All 20 original seed tasks were re-read through the native public task API and
confirmed completed before the matched workload. The response exposes aggregate
usage but no extraction invocation counter. The original tool-event observer is
also incomplete: even confirmed seed saves have empty `toolCalls` arrays, so those
arrays cannot support a claim of zero workload extraction calls. The cost gate
therefore remains explicitly open. The archive and exact timestamps are included
in the waiting/cost supplement. Chrome selection was revalidated and still returns
`Browser is not available: chrome`; the real in-app OA session is retained.
