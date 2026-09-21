# #475 automatic collection and lifecycle

Status: implementation in progress, not published or enabled in Dano. #474 was
merged as `8056d71eabf0bfdeb6b67e16b8899f3fd4776520`; this work does not complete
#475, the parent #465, or any production enablement gate.

## Implemented authorization and delivery foundation

Independent extension branch `codex-475-memory-collection`, commit `e0111d1`,
adds a separately persisted automatic-collection consent with revision,
effective time, policy version, trusted scope and stable session/entry/branch
boundaries. The package remains unpublished; Dano still installs `0.1.2`.

- Automatic enqueue must carry the authorization epoch and collection revision
  captured for its source. A new grant cannot authorize old work implicitly.
- Revoking only collection blocks and removes pending automatic payloads but
  preserves enabled explicit saves and recall.
- Pausing all memory prevents every new send claim. Resuming preserves a
  separately granted consent while renewing its revision and source boundaries.
  Previously blocked operations remain terminal.
- Policy changes and send claims use the same owner-store transaction. A late
  response cannot make the next send phase eligible under an obsolete policy.
  In-flight unknown outcomes retain reconciliation information; accepted commits
  may still reach real ready after pause. No successful cancellation is claimed.
- The model-facing explicit save now passes the checked authorization epoch to
  enqueue, closing the source-check-to-enqueue pause/resume race.
- Existing explicit operation identities are unchanged. Automatic identities
  additionally include operation kind and collection revision.
- Missing/corrupt automatic-consent metadata fails closed without rewriting the
  stored state. Caller-supplied boundaries are metadata, not yet proof that a
  real session capture or collector enforces them.

Validation: all 58 extension tests pass on Node 22.22.3. Added cases include
separate grants, scope/revision mismatch, selective revocation, resume boundaries,
explicit-save authorization races and create/message/commit response-loss races
across policy changes and service reconstruction. These use transport doubles;
real network-loss/dual-user/browser tests for #475 remain required.
Log: `/private/tmp/dano475-consent-tests.log`.

## Verified pi event seam

The installed `@earendil-works/pi-coding-agent@0.85.1` public extension types expose
`agent_settled`, documented as occurring after automatic retries, compaction and
queued continuations finish. Its implementation awaits extension handlers before
notifying AgentSession subscribers. `before_agent_start` runs before the new user
message enters the agent loop; `message_end` persistence follows extension event
dispatch. Therefore raw message callbacks or individual `turn_end` events are not
safe substitutes for completed-request capture.

The collector still needs to use stable persisted entries, record request/source
boundaries and settlement durably, and distinguish cancelled/error results and
pending user interaction. A timestamp alone is not the required entry/branch
boundary. Shared ancestor entries must not be recollected after fork/tree/reload.

## Remaining implementation and acceptance

1. Connect actual session boundary capture and a durable source ledger to the
   verified pi lifecycle, including restart, retry, fork/tree and disposal.
2. Select confirmed facts without transmitting thinking, credentials, raw tool
   output, UI wrappers, recalled blocks or unconfirmed assistant inferences;
   implement bounded merging and maximum delivery delay without blocking stream
   output. A permissive collector is not an acceptable placeholder.
3. Add distinct authenticated Dano and ordinary-pi collection controls, browser
   projections and policy messaging. Preserve anonymous-to-authenticated binding
   rules and immutable owner credentials.
4. Test the complete Spec §8.3 state table, real in-flight response loss, restart,
   dual-user concurrency and browser multi-chat pause/resume/separate consent.
5. Publish the complete independent extension, pin the real artifact in Dano,
   bump Dano's runtime version, then complete regression, browser acceptance,
   independent reviews and the upstream PR/merge gate.

## Durable source receipts (2026-09-21)

Independent extension commit `cd19065` adds source receipts written in the same
owner-store transaction as the automatic delivery operation. The source key uses
owner, trusted scope, original entry ID, original entry timestamp and content
version. Session and branch IDs remain in operation provenance but do not make a
copied ancestor a new source. Consent changes do not erase these receipts. A
repeated source returns its original operation, including a terminal paused or
failed result; it cannot create another remote session by adopting a new grant.
Receipts retain a payload digest, not a second copy of conversation text, so
conflicting content remains detectable after payload removal.

Validation: 64 extension tests pass on Node 22.22.3, plus TypeScript check.
`/private/tmp/dano475-ledger-tests.log` records the run. New cases cover concurrent
viewers, reopening the file store, pause/resume followed by fork replay, distinct
messages with reused short IDs, transaction failure before commit, malformed
receipt rejection without rewriting state, and the real pi 0.85.1
`SessionManager.createBranchedSession` path with label entries removed and the
ancestry chain rewritten. The real pi test confirms preserved entry identity;
it does not prove full agent lifecycle or real OpenViking network behavior.

This is a delivery-side foundation. The trusted collector still must derive
these fields from actual entries, durably capture request completion and enforce
source boundaries before selection. Sources rejected before enqueue, coalesced
batches, privacy filtering and interrupted-request recovery are not implemented
by this receipt table. Package version remains 0.1.2 and this work is unpublished.

## Request lifecycle integration (2026-09-21)

Independent extension commit `e53bbf5` connects a metadata-only request journal
to `before_agent_start` and `agent_settled`. It snapshots the current original
entry boundary before the new user message is persisted. Retries/continuations
may retain an in-process request token; a new process cannot silently resume an
unfinished request as though it had settled. Tree divergence invalidates an old
anchor, and forks start after their copied ancestors. Successful settlement
requires a new user message and a final assistant message with `stopReason=stop`;
error, aborted, length-limited and unfinished tool rounds are not accepted.
Blocking extension UI prompts defer settlement. Pause or collection revocation
blocks running and settled requests in the same transaction as delivery policy.

The journal stores IDs and policy metadata only. Completed records remain in the
owner store across service reconstruction for the forthcoming selection worker.
Shutdown is not the sole persistence point. An interrupted run without a durable
settlement is not inferred to have completed; starting a fresh run discards that
unfinished record and establishes a current boundary.

Validation: 75 extension tests pass (`/private/tmp/dano475-lifecycle-tests.log`).
The additional [real AgentSession probe](fixtures/pi-collection-settlement.mjs)
ran two actual `mimo-v2.5` requests through installed pi 0.85.1 and the unpublished
extension. Observed message counts before each request were 0 and 2. At both
`turn_end` and `agent_end`, the newest journal record remained running; at
`agent_settled`, it was durably settled. Both requests had two distinct source
references with no overlap. Probe log: `/private/tmp/dano475-real-settlement.log`.
The initial probe import failed before any request because the package exposes
an ESM-only entrypoint; the corrected probe reads the manifest's import export.

This probe disables all tools and uses an empty recall transport. It proves pi
lifecycle ordering with a real model, not isolation, privacy selection, automatic
OpenViking delivery, cancellation races or Dano UI acceptance. The final type
export/shutdown-reference cleanup and malformed-journal test were verified by the
75-test suite after the model probe. Privacy filtering, bounded coalescing,
selection-worker recovery, Dano form-wait integration, bounded unavailable-store
behavior and the separate authenticated consent UI remain outstanding. Neither
package publication nor the Dano exact dependency has changed.

## Local candidate screening (2026-09-21)

Independent extension commit `34c6c8e` adds `CollectionInputBuilder`. It reads only
settled, still-authorized request IDs from the owner store and original pi
entries from the matching session. Only text blocks from user messages and
successful assistant responses are projected. Assistant text remains explicitly
`assistant_reference`, never a confirmed fact. Thinking, images, raw tool
arguments/results, failed assistant rounds and custom recall/form wrappers are
not projected. Missing sources and oversized input block the entire request
instead of silently truncating its meaning. Consent is rechecked after scanning
and host credential discovery, closing concurrent pause/revoke races.

### Detector choice and boundaries

- [Secretlint core](https://github.com/secretlint/secretlint/tree/master/packages/%40secretlint/core)
  supports in-memory text scanning in JavaScript. Exact `13.0.5` core and
  recommended preset packages are runtime dependencies, with original MIT
  license files retained. The wrapper supplies its own rules and virtual source;
  conversation content does not select configuration or filesystem paths.
- [The recommended preset](https://github.com/secretlint/secretlint/blob/master/packages/%40secretlint/secretlint-rule-preset-recommend/src/index.ts)
  supplies maintained vendor detectors. Its comment-filter rule is excluded:
  `secretlint-disable` in a conversation cannot authorize disclosure.
- [Gitleaks](https://github.com/gitleaks/gitleaks) was considered as an established
  alternative. Its separate executable/runtime would add packaging and process
  boundaries to both standard pi and the Node host; Secretlint fits this existing
  runtime without copying a vendor rule corpus into custom regexes.
- Small additional declaration guards cover generic password/token/secret fields,
  authorization and cookie headers, JWT syntax, and standard PEM private-key
  headers. NFKC normalization and zero-width separator removal happen only in
  the scanner view. Trusted host secret snapshots cover opaque configured values;
  neither snapshots nor scanner diagnostics are returned or persisted.
- The initial synthetic PEM test exposed the upstream detector's deliberate
  placeholder heuristic. A private-key header now rejects the whole message even
  when its body is malformed or incomplete. No placeholder exception is granted.

Any detected credential excludes its entire message. This avoids treating text
with removed spans as if it retained the user's original meaning. Scanner errors
return a fixed code without source text or diagnostic causes. An undetected text
is only a candidate: generic pattern matching does not prove universal secret
absence or semantic confirmation. The future fact selector must still reject
credential facts, unsupported inferences and unconfirmed assistant suggestions.
Host-specific sensitive-value wiring is not yet installed in Dano/ordinary pi.

Validation: 95 extension tests pass (`/private/tmp/dano475-input-tests.log`).
Coverage includes vendor tokens, partial PEM blocks, generic English/Chinese
credentials, environment variables, JSON credentials, cookie/auth headers,
comment suppression attempts, normalized labels, known opaque credentials,
excluded message types, scanner failures, missing sources, byte limits and
concurrent pause/resume. `npm audit --omit=dev` reports zero findings in
`/private/tmp/dano475-input-audit.json`; this is dependency advisory evidence,
not a security proof. Existing dependency versions remain unchanged; npm moved
unchanged `debug`/`ms` dependencies to the root during deduplication.

The updated real pi 0.85.1 / `mimo-v2.5` probe passed twice-completed-request
settlement plus screening over actual session entries. Its first request retained
one ordinary user candidate; its second excluded the synthetic-password user
message. Both retained assistant text solely as references. No automatic
OpenViking operation was queued. Log: `/private/tmp/dano475-real-input.log`.

Remaining: confirmed-fact selection (including explicit user confirmation of
assistant proposals), allowlisted/declassified task facts, bounded merge and
selection-worker recovery, runtime credential snapshot wiring, separate consent
UI, and complete real-service/browser acceptance. Package publication and Dano's
exact installed `0.1.2` remain unchanged.

## Source-backed fact selection (2026-09-21)

Independent extension commit `7c60c31` adds `CollectionFactSelector` over the
screened inputs. It orders settled requests using original pi entry positions,
rejects mixed branches/overlapping source sets, and gives the model opaque local
message labels rather than owner IDs or internal session metadata. Accepted
quotes must exist verbatim in a screened source. A confirmed assistant proposal
must point to the next actual user entry on that branch, including entries
omitted from the batch or excluded by screening. Its source anchor is that user
confirmation; both quoted evidence references are retained. A user-source item
cannot claim the assistant-confirmation structure. Duplicate facts collapse.

The model is told that automatic consent is already enforced by the host, so a
stable direct user preference does not need a second request to remember it.
Assistant references remain unconfirmed until the model identifies explicit
confirmation and the deterministic evidence/ordering checks pass. Those checks
prove provenance and structural eligibility, not semantic truth by themselves.
Model output has byte/count limits, a deadline and an abort signal; one complete
JSON code fence is normalized safely. Errors return fixed codes, and policy is
rechecked before inference and after its result. The selector only returns
candidates: it does not persist selected payloads or enqueue OpenViking writes.

Validation: all 109 extension tests pass. New coverage includes exact references,
forged source/text, confirmation order, omitted intermediate rejection, foreign
branches, already-aborted work, an actually invoked non-cooperative model,
late results after pause/resume, scanner-eliminated inputs, and JSON fences.
A full-suite run also exposed a pre-existing test cleanup race: fixture removal
preceded scheduler shutdown. The fixture now drains its tracked schedulers before
removing its own directory. No scheduler production behavior was changed.
Log: `/private/tmp/dano475-selection-tests.log`.

### Real MiMo semantic probe and failed attempts

[The fixed semantic fixture](fixtures/pi-collection-selection.mjs) uses actual
pi state/lifecycle/input screening and `ModelRuntime.completeSimple` against
`mimo-v2.5`. It covers a stated preference, a question, an unconfirmed inference,
an explicitly confirmed assistant proposal, rejection, quoted injection and a
hypothetical. Its seven-case dataset SHA-256 stayed unchanged throughout:
`2424843ca73365308fc43e8019c5d53b0d810eb9c7c94ce18d249b71e18847e1`.

Failures were retained rather than counted as success:

- Default MiMo settings passed 4/7; three calls hit the unchanged 45-second
  deadline. Several empty results consumed hundreds of output tokens.
  `/private/tmp/dano475-real-selection.log`.
- Disabling thinking alone passed 4/7, then a diagnostic reproduction passed
  6/7. Missing direct preferences, JSON fences and an incorrect proposal anchor
  were observed. Logs: `dano475-real-selection-disabled.log` and
  `dano475-real-selection-diagnostic.log` under `/private/tmp`.
- Clarifying automatic consent and accepting JSON fences passed 6/7. The remaining
  wrong anchor selected the initial request for advice rather than the confirmed
  assistant proposition. `/private/tmp/dano475-real-selection-v2.log`.
- The final prompt adds a general confirmed-proposal example using ISO date
  formatting (not a dataset answer), and validation rejects a confirmation
  structure attached to a user-source item. The probe requests disabled thinking
  and temperature 0 through the public model API, retaining the same cases,
  expected outcomes, 45-second deadline, maximum facts and output budget.
  These parameters are supported by the
  [official MiMo API](https://mimo.mi.com/docs/en-US/api/chat/openai-api).

The final prompt hash is
`3043eb3c409ea0805af7fea6dddd37330139fed7bc76a036230d28d4af815430`.
The first final-profile run passed 7/7 in about 0.58–2.06 seconds per case,
recording input/output/cache token counts. Its log is
`/private/tmp/dano475-real-selection-v3.log`. An unchanged repeat also passed
7/7 (0.62–3.96 seconds per case), recorded in
`/private/tmp/dano475-real-selection-v3-repeat.log`. The fixed fixture remains a small
component check, not the Spec T-14 dataset, a privacy proof, a price calculation,
or real automatic OpenViking delivery.

Remaining implementation includes owner-level bounded coalescing/recovery, confirmation context across
already-processed batches, safe allowlisted task-fact projection, host model and
credential-snapshot wiring, bounded unavailable-store behavior, separate consent
UI and real multi-user/pause/browser/service acceptance. Dano still installs the
published `0.1.2`; this feature remains unpublished.

### Atomic selection handoff and real OpenViking restart

Extension commit `0ef2af4` adds `collectSelection`: one owner-state transaction
records the selected batch digest, processed request receipts, source ledger and
outbox operation. Multiple source facts share one operation; only selected fact
text enters its payload. Evidence quotes are retained as hashes and source
references. Empty selections record completion without creating an operation.
Exact replays return the original receipt, including after pause; changed or
overlapping selections cannot create replacement writes. Current authorization,
scope and consent revision are checked at the durable handoff.

All 120 extension tests pass (`/private/tmp/dano475-handoff-tests.log`), covering
concurrent handoffs, pause/replay, malformed receipts, payload limits, duplicate
facts and child-process SIGKILL before and after the transaction commits.

[The real-service fixture](fixtures/openviking-collection-handoff.mjs) uses real
pi session entries and OpenViking 0.4.20. Two synthetic source facts become one
operation, the writer is killed after persistence, and a reopened owner store
resumes delivery to `ready`. Alice recalls the fact; Bob's scope remains empty.
Forged cross-user read, write and search each return 403. Replaying the same
selection returns the existing operation without another enqueue. Evidence:
`/private/tmp/dano475-real-handoff.log`. Private credentials and the result remain
under the fixture's private audit directory, outside the repository.

The real-service fixture supplies deterministic selected facts; semantic model
selection and browser automatic collection are not exercised by this probe.
The final small payload-deduplication and missing-receipt guards were covered by
the 120-test suite after that service run. Runtime scheduling, browser consent
and the remaining acceptance gates above are still pending. This does not close
#475 or publish the extension.

### Durable background selection scheduler

Extension commit `d41ddaa` adds the owner-level `CollectionScheduler`. It uses
configurable merge/max-wait windows, a per-batch request limit, durable selection
attempts and expiring claim tokens. One owner claim prevents concurrent inference
by other instances; a replaced or expired token cannot commit a result. Sibling
pi branches are processed separately. Missing sessions, failed inference and
non-cooperative callbacks have bounded attempts and an observable
`selection_failed` state. Pause invalidates active claims; processed requests are
not read or selected again on startup.

The original pi entries remain the source of truth. The trusted host supplies an
owner-bound session resolver and model adapter; this scheduler does not infer
session ownership from arbitrary paths. File-state reads and transactions now
accept cancellation while waiting for their lock. Cancellation before mutation
cannot become a delayed write after the lock is released; an atomic write already
started completes its durable commit.

Validation: all 135 extension tests and TypeScript checks pass. In addition to
the actual pi input/selector/handoff pipeline, coverage includes two scheduler
instances, merge/max-wait behavior, batch limits, sibling branches, SIGKILL after
a persisted claim, expired-claim replacement, pause during inference, exhausted
retries, non-cooperative callbacks, late handoff and real filesystem lock
contention. Log: `/private/tmp/dano475-scheduling-tests.log`.

[The scheduled real-service probe](fixtures/openviking-collection-scheduler.mjs)
also passed. Real `mimo-v2.5` selected both synthetic user preferences with one
call (383 input, 74 output, 512 cached-read, 969 total reported tokens). The
collection scheduler produced one operation from two sources. After SIGKILL,
the delivery scheduler reopened the same owner state and reached OpenViking
`ready`. Alice recalled the fixed report ending, Bob's own scope was empty, and
forged cross-user read/write/search each returned 403. A restarted collection
scheduler did not reopen processed sources or call the model again.

Evidence: `/private/tmp/dano475-real-scheduler.log`; private audit directory:
`/var/folders/nw/mhq_0_3x2rdbj8qpbl3xt3gr0000gn/T/dano475-collection-scheduler-g4ENNN`.
The probe ran before the subsequent lock-cancellation addition; the full suite
passed after that addition. The probe uses synthetic pi message entries and the
real selection model/service, not the browser's automatic-collection UI.

Still pending: production host session registry/recovery and model wiring,
cross-batch confirmation context, declassified task-fact projection, separate
consent UI, status projection and the full lifecycle/browser acceptance gates.
The independently published package and Dano pin remain `0.1.2`; #475 stays open.

### Protected source recovery and standard-host consent wiring

Extension commit `fdb88f1` adds `CollectionSessionRegistry`, which persists only
the original pi session ID/file reference in owner state. The configured owner
session root must be private and outside tool access. Registration/recovery
reject foreign roots, symlink escapes, mismatched IDs, invalid ancestry and
old-format sources. Recovery uses pi 0.85.1's public `parseSessionEntries` and
`SessionManager.inMemory` APIs. It deliberately avoids `SessionManager.open`,
which can repair empty files or migrate older sessions. Empty/corrupt sources
remain unchanged when recovery fails.

The extension now registers its source before a request and wakes collection only
after durable `agent_settled`. These hooks require collection host configuration.
Their separate `lifecycleTimeoutMs` bounds bookkeeping, including lock waits;
late work cannot mutate state after cancellation. A fixed error callback reports
failure without source text or blocking normal chat. An intermediate full-suite
run exposed the incorrect reuse of a 50 ms recall budget for settlement; this was
removed in favor of the explicit collection deadline, and the final suite passed.

Standard pi gains separately confirmed `auto-enable` and `auto-disable` memory
commands. Resume preserves an existing separate grant with fresh source
boundaries; it does not grant collection implicitly. The launcher starts/stops an
optional owner collection scheduler. The acceptance host script wires configured
pi model/auth files, key screening, source recovery and both schedulers from its
private configuration. Dano's production adapter has not yet been updated.

Validation: all 145 extension tests pass, including original-source reopening,
foreign roots and symlinks, corruption without repair, missing collected sources,
live branch boundaries, completed-event wakeup, foreground timeout, separate
confirmation/revocation and resume boundaries. Log:
`/private/tmp/dano475-host-sessions-tests.log`.

[The real host-restart fixture](fixtures/pi-collection-host-restart.mjs) runs real
pi `AgentSession` requests against `mimo-v2.5`. It kills the chat process after two
completed requests and their source references are durable. A separate process
with no live session manager or Viewer recovers the original files and runs the
selection scheduler. One real model call creates one queued operation from the
stable preference; the synthetic credential message is excluded before the
selection model. This assertion concerns the background selector, not the user's
original chat request, which the chat model necessarily receives.

The final run passed: `/private/tmp/dano475-real-host-restart-final.log`, with
private synthetic evidence retained under
`/private/tmp/dano475-host-restart-W9xdSv`. Selection reported 63 input, 29 output,
832 cached-read and 924 total tokens. An earlier run also passed under
`/private/tmp/dano475-real-host-restart.log`. This fixture does not exercise
OpenViking delivery, protected Linux worker isolation or browser consent; those
are separate gates. The existing settlement fixture was updated to supply the
now-explicit collection host configuration.

Remaining work includes Dano adapter/UI wiring, cross-batch confirmation context,
declassified task-fact projection, standard CLI and browser full-flow acceptance,
publishing the independent extension, exact Dano integration and the remaining
#475 ACs. No published version or production deployment changed in this step.

### Cross-batch confirmation without replaying history

Extension commit `c56fd2c` links a new request to its adjacent completed assistant
proposition when the owner, scope, authorization epoch and collection revision
match. The journal retains the proposition's entry ID, original timestamp and
message digest, not its body. A copied fork ancestor can retain its original
provenance; a short-ID collision, changed timestamp or changed content cannot
borrow it. Input screening still runs on the original reference text.

Only the new user's confirmation is an eligible collection source. The prior
assistant message is evidence and cannot enqueue itself; the processed request
and its receipt remain unchanged. The builder includes no older user messages.
Merged batches deduplicate a shared reference, and the selector checks the actual
branch's next user message so omitted rejection cannot become confirmation.
Pause/resume or renewed automatic consent prevents importing an old reference.
Receipt replay after pause remains truthful and cannot restore cleared payloads.

All 155 extension tests pass (`/private/tmp/dano475-cross-batch-tests.log`). New
coverage includes processed empty batches, positive confirmation and atomic
handoff, missing confirmation, credential-containing references, intervening
rejection, policy boundaries, shared-reference deduplication, copied fork
provenance, ID/timestamp mismatch and a proposition modified after request start.
These fork cases exercise copied original pi entries; full interactive fork/tree
acceptance remains part of the overall lifecycle gate.

[The real cross-batch probe](fixtures/pi-collection-cross-batch.mjs) passed all
four cases with `mimo-v2.5`: confirmation after a processed batch, rejection,
credential exclusion and an intervening rejection. Each batch performs real
selection and durable handoff before the next request starts; the fixture does
not inject a fake empty prior result. Only the positive case creates one queued
operation, anchored to the new confirmation. The other cases create none.

Dataset SHA-256:
`1331bb40a90d27b97ee096f9b3bbf46fbd5e574d18bf8a13f5d5eed1f85db351`.
The prompt remains
`3043eb3c409ea0805af7fea6dddd37330139fed7bc76a036230d28d4af815430`.
Both `/private/tmp/dano475-real-cross-batch.log` and the final-code repeat
`/private/tmp/dano475-real-cross-batch-final.log` passed 4/4. The final run records
nine model calls and token counts; each entire case took about 1.8–4.8 seconds.
This is a synthetic semantic/handoff probe, not OpenViking delivery, browser
acceptance or the full T-14 dataset. Dano wiring, declassified task-fact projection,
the independent release and remaining #475 acceptance still need completion.

### Allowlisted business-task facts and rule-version consent

Extension commit `d28288e` adds an optional host-installed `TaskFactPolicy`.
Raw tool messages remain excluded by default. A projector is invoked only for an
allowlisted name, one matching earlier call and one successful result within a
completed request, under the separately granted rule version. The host verifies
the business result/actor contract and returns necessary fact text; the adapter
receives a copy, so it cannot change the original pi entry. The same credential
scanner, host-secret snapshot, input budget and concurrent policy checks apply to
the projected text before model selection.

The model receives `task_fact` data with opaque source IDs, not raw tool arguments
or output. Selected facts retain the original tool-result source and tool/policy
provenance. The outbox wrapper is now `authorized_memory_facts`, since a verified
task outcome is not a claim of explicit user confirmation. Only selected fact
text is sent to OpenViking. No general-purpose shell-output projector is enabled
by default; a trusted business contract is required for each configured tool.

This exposed an authorization issue: main-switch resume previously replaced the
collection grant's rule version. Resume now preserves the granted version while
advancing its revision and source boundary. A newer task-fact policy stays
unapproved until a separate collection grant. Standard pi can receive the current
configured rule version, reports changed rules and confirms them through its
separate collection command.

All 173 extension tests pass (`/private/tmp/dano475-task-facts-tests.log`). Coverage
includes safe projection/provenance/handoff, absent or mismatched allowlists,
failed/unmatched/duplicate results, aborted requests, credentials in projected
text, throwing/malformed adapters, caller allowlist mutation, pause during
projection, byte limits, forged policy versions and separate rule-update consent.

[The real task-fact selection fixture](fixtures/pi-task-fact-selection.mjs) passed
6/6 with `mimo-v2.5`: completed template creation, unlisted tool, failed tool,
foreign business actor, projected credential and projected instruction. Only
verified creation queues a fact. Dataset SHA-256:
`763fcff69658c595be39dfce01d0f9e7e0e08fff66ca512d609ef633d986cfd7`.
Log: `/private/tmp/dano475-real-task-facts.log`.

The updated prompt hash is
`c7138e184dc30b9f91ac7ffe5f94345652dc6772709d2ec0eac4e991959ab6f4`.
The unchanged seven-case semantic dataset passed 7/7 again, and the unchanged
four-case cross-batch dataset passed 4/4. Logs:
`/private/tmp/dano475-task-prompt-regression.log` and
`/private/tmp/dano475-task-cross-regression.log`. These remain component datasets,
not the full T-14 gate.

[The scheduled OpenViking fixture](fixtures/openviking-collection-scheduler.mjs)
now accepts an optional `task-fact` scenario. It passed real MiMo selection,
atomic enqueue, SIGKILL/reopen, background delivery to `ready`, Alice recall of
`REPORT-42`, an empty Bob scope and 403 responses to forged cross-user read,
write and search. One model call reported 79 input, 50 output, 896 cached-read and
1,025 total tokens. Evidence: `/private/tmp/dano475-real-task-delivery.log`;
private audit directory:
`/var/folders/nw/mhq_0_3x2rdbj8qpbl3xt3gr0000gn/T/dano475-collection-scheduler-6wEtgn`.
The business tool contract is synthetic; this does not establish real OA business
operation acceptance, browser acceptance or a production deployment. Dano remains
pinned to published `0.1.2` pending independent release and full integration.

### Independent pre-release review: input provenance

Review baseline: `b2e576341bba2355d206190c0f171beb5a809663`;
reviewed head: `d28288eda897445f161fd8656720d3d07dfc51b6`.

**Standards:** no confirmed documented-standard violation. One P3 maintainability
finding concerns repeated authorization tuple predicates across collection phases.

**Spec:** one P2 finding: pi expands Skill/prompt-template commands before
persisting the resulting user message. Treating the whole persisted message as
user-authored evidence can collect example preferences from those resources.
A controlled-selector reproduction demonstrated this for a Skill; it is not a
measurement of real-model failure rate.

The initial fix uses pi's public `parseSkillBlock` to retain only the separate
user suffix. Skill-only, malformed and nested wrappers provide no user evidence;
original entry identity and content digest still identify the source. All 177
extension tests pass, including four added provenance regressions
(`/private/tmp/dano475-skill-provenance-tests.log`). Unmarked prompt-template
expansion still requires an input-provenance solution, so the P2 finding remains
open and independent release is not approved yet.

Standard CLI automatic-collection acceptance and Dano browser integration remain
pending. Read-only Podman inventory found the existing three acceptance containers
running, but image enumeration failed with `readlink .../storage/overlay: invalid
argument`. No shared container storage was repaired or removed.

Follow-up inspection of pi 0.85.1 confirms that normal `prompt` emits `input`
before expansion, but public `steer`/`followUp` and their RPC commands expand
without that event. An input-only provenance map would therefore leave queued
template messages uncovered. The existing protected CLI already disables prompt
templates, while the SDK resource profile does not; do not silently remove Dano
template capability to claim this cross-host finding fixed.

The standard CLI acceptance fixture now takes explicit provider/model/tokenizer
revision arguments instead of hardcoded qwen35 metadata. With collection config,
it exercises separate consent, one completed-request automatic save without
`memory_save`, ready state, new-session recall and automatic-only revocation.
The acceptance host now forwards the configured collection policy version.
Both scripts pass Node syntax checks; their new real-container scenario has not
run yet. Podman image enumeration reproduced the same error on recheck; storage
exists and has 14 GB free, so absence or disk exhaustion is not established.

### Standard CLI Linux run preparation

The known image `localhost/dano474-protected-image:memory-0.1.2` remains directly
readable (ID `ffbffe96ea19d3cabf6fc6a1ab95fa2c5ed6cfbffb58a6be6a7ba1f359c888b8`).
The disposable container `dano475-cli-auto` uses it with current extension dist
and scripts, lockfile dependencies (`npm ci --include=dev --ignore-scripts`),
and the existing same-ABI Linux fs-ext binary. This is an integration fixture,
not a clean package-build or release proof. No new published ports or host runtime
mounts are used. It connects to the isolated OpenViking loopback relay and a fresh
synthetic account; private inputs are in `/private/tmp/dano475-cli-auto-input`.

Two fixture startup faults were corrected inside that container: the base image's
production environment omitted the pi dev dependency, and the copied public
tokenizer directory needed read/traverse access for the unprivileged host. Secret
file permissions were not relaxed. The subsequent run reached explicit consent
through the actual standard RPC entry. The active run must still complete before
any automatic-collection or full CLI success claim; its log is
`/tmp/cli-acceptance.log` inside this container. Tokenizer revision:
`63651580ca774f8504f676040460aed3e1244ac1`, model `mimo-v2.5`.

The run subsequently exited successfully. Evidence:
`/private/tmp/dano475-cli-auto-result.json` and
`/private/tmp/dano475-cli-auto.log`. Through the real protected pi RPC entry,
it verified default-off state, explicit consent/save/ready/content/source,
new-session recall, separate automatic consent, a completed short request saved
automatically without a `memory_save` call, automatic `ready`, another new-session
recall, automatic-only revocation preserving the main switch, and final pause.
The configured OpenViking key was absent from RPC events and stderr. This is one
real MiMo/OpenViking CLI flow, not the full T-14 dataset, restart/pause-race suite,
Dano browser acceptance, or a clean release build. The disposable container was
removed after collecting evidence; the existing Dano stack was retained.

### Host user-text attribution interface

Extension commit `74aab9d` adds `projectUserText({ source, text, signal })` to the
collection input/selector API. A trusted host can resolve a user-authored span
from protected attribution records; an unavailable projection excludes the
entry, and non-string or non-contiguous output fails closed. Original source
identity/content digest remain unchanged, and projected text still passes secret
screening and the post-await consent check. Callback errors expose no source or
private diagnostics. This is a host interface, not a model-defined projector.

All 183 tests passed after the input implementation
(`/private/tmp/dano475-user-projection-tests.log`). An additional selector test
then proved that an exact quote from excluded template text is rejected; the
55 affected input/selector tests passed
(`/private/tmp/dano475-user-projection-targeted.log`).

Dano's `prompt`, `steer` and `follow_up` dispatch paths each know the original
input before file-reference injection. However, `TranscriptProjector` only keeps
its pending structured-message map in memory; it is not durable attribution for
collection recovery. Dano must connect protected, digest-bound provenance to the
new interface without copying conversation bodies into the outbox. That adapter
and restart/fork validation remain pending; the original cross-host P2 finding is
not claimed fully resolved by the interface alone.
