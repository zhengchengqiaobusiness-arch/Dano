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

### Durable Dano provenance receipt primitive

`MemoryUserProvenance` now records a host-only custom entry in the protected
original pi session. The receipt contains owner digest, original entry ID,
timestamp, content digest and the length of the user-authored prefix, never a
copy of the message or injected file references. Recording requires the persisted
user message to exactly match the dispatched text and the original browser text
to be its prefix. Unmatched template expansion cannot obtain a receipt. Retrying
the same receipt is idempotent; conflicting attribution is rejected.

Three tests passed using real pi session persistence/reopen and `forkFrom`,
including copied-ancestor recovery, foreign-owner rejection, no transcript copy,
missing template provenance, changed digest/timestamp and conflicting capture.
`pnpm run check:type` passed. Logs: `/private/tmp/dano475-provenance-tests.log` and
`/private/tmp/dano475-provenance-types.log`.

This primitive is not yet called by Dano dispatch or the extension selector.
Connecting prompt/steer/follow-up capture, durable settlement ordering and source
projection remains necessary. No runtime behavior or product version is changed
by the currently unreferenced module alone.

### Dano dispatch provenance wiring

The authenticated memory runtime now exposes capture to the backend context, and
the RPC adapter invokes it for `prompt`, `steer` and `follow_up` immediately before
dispatch. Pending captures retain only dispatched-text digests, user-prefix
lengths and pre-dispatch entry IDs. Queue APIs keep captures until settlement;
failed/no-op dispatches release them. The runtime's provenance `agent_settled`
hook runs before its memory extension and writes receipts only for newly
persisted matching entries. Runtime disposal clears pending captures. Ambiguous
queued prefix attribution fails closed, and old dispatch cleanup cannot erase a
new capture for the same session.

Capture requires both main authorization and the independent automatic switch.
Optional authorization reads have a foreground deadline and share one pending
read, so a locked memory store cannot indefinitely block chat or accumulate a
read per prompt. Root product version advances to `0.2.29` for this runtime wiring.

The four affected test files passed 172 tests; `pnpm run check` reported no type
or Svelte diagnostics. The additional foreground-lock test initially intercepted
the independent delivery poller too; after isolating that poller, the 17 runtime
tests passed. Logs: `/private/tmp/dano475-provenance-wiring-tests.log`,
`/private/tmp/dano475-provenance-wiring-check.log`, and
`/private/tmp/dano475-provenance-timeout-tests.log`. Use Node 22 for this checkout's
existing fs-ext ABI; the initial bundled Node 24 run could not load that binary.

The collection selector is not yet connected to this Dano receipt reader because
Dano still pins published extension `0.1.2`. Independent release, exact-version
upgrade, collection configuration/settings and real browser acceptance remain
pending. Dispatch unit evidence is not a browser or complete #475 acceptance claim.

### Independent 0.1.3 release

The independent extension release PR is
https://github.com/josephyoung/pi-openviking/pull/1, merged as
`bb09412260d3c89276e6e186c21585e7366953af` on 2026-09-21.
Candidate `04c72400a24886c6a1360a53008156aec1a2e597` contains the reviewed
implementation and the `0.1.3` version change. Both repositories were refreshed
and rebased before pushing; no force push was used.

**Standards:** no documented-contract breach or confirmed source-boundary defect;
one P3 maintainability suggestion remains for repeated authorization predicates.
**Spec:** the independent extension's prior P2 is closed. The public Skill parser,
protected CLI template restriction and explicit host projection contract address
the extension's responsibility; Dano-specific integration remains required.
Summary: Standards one heuristic P3; Spec zero remaining extension findings.

All 184 extension tests passed under Node 22
(`/private/tmp/dano475-extension-release-tests.log`). Package dry-run found 47
expected files and no unexpected paths (`/private/tmp/dano475-extension-pack.json`).
The version change triggered Trusted Publishing run
https://github.com/josephyoung/pi-openviking/actions/runs/35601803347.
The run completed successfully, including clean dependency install, type check,
184 tests and publish. The log explicitly reports
`+ @josephyoung/pi-openviking@0.1.3`; preserved at
`/private/tmp/dano475-publish-013.log`. npm's exact-version endpoint confirmed
version `0.1.3`, keywords `pi-package`/`pi-extension`, and integrity
`sha512-6XSqvdzg3uSGugIPMRPl/ePTxCDdDK1gBIkWBQGxI/bnxQf2wawyIktJrjAhgOexFQ+3krg0KdpV+pxddfMbKQ==`
(`/private/tmp/dano475-npm-013-direct.json`). The initial package-index lookup
returned stale metadata and 404 for this version; direct version verification
resolved the discrepancy without republishing. The remote extension PR branch
was verified absent after merge. Dano's exact upgrade is the next integration
gate. This PR does not close Dano #475/#465.

### Dano 0.1.3 integration and separate consent surface

Dano now pins the published `@josephyoung/pi-openviking` version `0.1.3` exactly;
its lockfile integrity matches the npm publication above. The owner runtime
constructs the public collection registry/selector/scheduler with the registry's
caller-owned protected session root. Its selector projects only the user prefix
proven by Dano's persisted provenance receipts. Two tests use real pi files and a
fresh collector with no live source map: attributed input reaches a local outbox,
while unproven template expansion never reaches the selector model.

The authenticated settings endpoint accepts separate automatic-consent mutations.
Grant requests must carry the displayed collection policy version; mixed main
and automatic mutations, missing versions and caller-supplied owner/scope fields
are rejected. The runtime compares the version with its trusted configuration,
requires main memory to be enabled and snapshots current source boundaries.
Revocation remains possible when collection configuration is absent. Pause and
resume retain the separate grant while replacing its source boundary, and
revocation survives runtime recreation. Browser-safe status exposes only the
available policy and consent version/scope/time/revision, excluding source paths
and boundaries. The dialog has a distinct consent action, withdrawal while
paused, and policy-change state; its main enable action does not grant collection.

Before background screening or model selection, the owner runtime rechecks tool
isolation. Screening combines configured sensitive values with the current
owner's existing USER key without provisioning a key. Regression tests prove
that a message containing that exact private value, or a loss of isolation,
never reaches model selection or produces an outbox operation.

Validation under Node 22:
- Four collector/runtime/HTTP files: 39 tests passed
  (`/private/tmp/dano475-collection-consent-tests.log`).
- Settings component: 6 tests passed
  (`/private/tmp/dano475-collection-ui-tests.log`).
- Full suite with the existing acceptance Python venv on PATH: 1578 passed,
  1 skipped across 135 files (`/private/tmp/dano475-collection-full-tests.log`).
  The initial system-Python run lacked `httpx`; all 34 provider-Python tests
  passed using the existing venv (`/private/tmp/dano475-pin-013-python-tests.log`).
- After adding the owner-secret/isolation checks, all 21 runtime tests passed
  (`/private/tmp/dano475-collection-owner-tests.log`); final full type/Svelte
  diagnostics are recorded in `/private/tmp/dano475-collection-final-check.log`.

The production service factory still does not configure the collection model.
Wiring the protected MiMo configuration and performing real rendered-browser
acceptance remain required before this surface is considered delivered. These
unit/integration results do not close #475 or replace its real-service gates.

### Protected model configuration and real MiMo adapter proof

The private host schema now accepts an explicit optional collection policy,
selector limits, scheduler bounds and model provider/id/output/temperature/thinking
settings. Arbitrary model-path and payload overrides are rejected. Protected
startup injects a lazy deployment ModelRuntime factory, evaluated after Dano
has resolved its agent configuration. The model adapter uses public pi APIs,
no tools, and a fixed-error boundary. It reads `AuthResult.auth` (the actual
0.85.1 contract) when refreshing sensitive credential values; tests cover secret
rotation, cancellation, incomplete responses and startup remaining offline.

Fourteen model/config/service tests passed, full type/Svelte checks passed, and
build output is recorded in `/private/tmp/dano475-model-config-build.log`.
The real-service fixture `fixtures/dano-collection-model.mjs` exercised this exact
Dano adapter with the protected MiMo configuration. One real `mimo-v2.5` call
completed in 1803 ms, matched the actual credential snapshot, had no tool
capabilities, and returned only source-backed quotes. Evidence:
`/private/tmp/dano475-model-binding-real.log` and
`/private/tmp/dano475-model-binding-tmS6iN/result.json`. Initial harness attempts
failed on ESM export/loading mechanics before any model call; the successful
fixture resolves each package's declared import export and uses jiti for Dano TS.

This closes the previously missing production service-factory wiring, not the
#475 acceptance gate. A current clean image and real browser flows, including
concurrent chats and pause/resume, remain necessary.

### Current-source regression and image build preparation

For candidate `c42e08a8`, the full regression suite passed 1588 tests with one
existing skip across 136 files (`/private/tmp/dano475-c42e08a8-full-tests.log`).
The live parent PRD/Spec and #475 checklist were refreshed before browser work;
#475 and #465 remain open. The existing loopback acceptance project has exactly
`app`, `nginx`, and `ov-relay` services, with its recorded configuration/data
volumes. Its current memory configuration has no automatic collector yet.
No production deployment or user grant was changed during preparation.

The persistent localhost certificate is valid until 2027-09-11, includes localhost
and loopback SANs, and passed macOS system trust verification. No certificate or
CA was regenerated. The existing authenticated in-app Browser tab remains at
`https://localhost:18711/`.

The first clean Dockerfile build completed dependency installation and application
compilation, then failed while cloning the pinned public open-websearch Skill
because GitHub's TLS handshake failed. Its process was observed terminal before
retry. Log: `/private/tmp/dano475-c42e08a8-image-build.log`.
The existing Mac proxy and VM loopback proxy tunnel were then independently
verified against the exact public GitHub URL (HTTP 301 with TLS verification
retained). A second build of the same candidate uses that tunnel only through
standard build proxy arguments and the VM host network. It later exited with npm `ECONNRESET` while installing the runtime search
package; log: `/private/tmp/dano475-c42e08a8-image-build-proxy.log`. The next
attempt retains the GitHub proxy and explicitly bypasses it for npm, Node,
Debian/Aliyun and PyPI dependency domains, matching the first attempt's working
direct dependency route. Its log is
`/private/tmp/dano475-c42e08a8-image-build-routed.log`; completion is pending.
A running build is not image or browser acceptance evidence. The original app container remains running
until a successful image is available.

### Dano Standards/Spec review and policy-update fix

Review fixed point: `8056d71eabf0bfdeb6b67e16b8899f3fd4776520` (upstream/main),
candidate `c42e08a84`. Standards found no actionable documented violation or
blocking heuristic. The custom provenance seam is justified by Dano's ownership
of browser-text/file-reference boundaries; other mechanisms reuse public pi and
published extension APIs. No ask_user_question contract changed.

Spec found one P1: a changed host collection policy could run against an older
persisted consent while the UI said reauthorization was required. A reproduction
with configured v2/consented v1 invoked the selector and queued an automatic fact
(`/private/tmp/dano475-policy-review.mjs`). The candidate image must therefore
not be deployed as accepted, regardless of its build result.

The fix moves both scheduler starts out of the constructor. Before publishing an
authenticated runtime, create reads its grant and revokes automatic consent if
its policy differs from configuration or the collection configuration is absent.
The public extension's revoke transaction blocks unfinished collection and
unsent automatic delivery, removes pending automatic bodies, and retains explicit
permissions/operations and unknown in-flight reconciliation. Creation failure
closes the unpublished runtime. A new user grant creates new source boundaries;
old blocked operations remain terminal.

Two parameterized regression cases hold the revoke operation open and prove
neither scheduler starts early. They execute the real revoke transaction and
verify automatic body removal, explicit queued-body preservation, unknown
in-flight preservation and reauthorization without old-operation replay.
All 23 runtime tests and the full type/Svelte check passed:
`/private/tmp/dano475-policy-fence-tests.log`,
`/private/tmp/dano475-policy-fence-check.log`.
The Spec reviewer confirmed the P1 closed after inspecting the production
UserRuntimeRegistry/withUserMemory/create path and these tests. Final review
summary: Standards zero findings; Spec zero remaining source findings (one P1
fixed). Image/browser gates remain outstanding.

### Protected image and first browser collection gate (2026-09-21)

The routed build of c42e08a8 finished successfully but was not deployed. The
corrected c42da19c image then built successfully using the same real Dockerfile,
official npm registry and TLS-verifying routes. Image digest:
`e4d9267057b2e785c5cbf462f9a2737b05aa9f3b0af3b7ab88684da3189bfbfd`.
Log: `/private/tmp/dano475-c42da19c-image-build-routed.log`.

Only the existing local acceptance app was replaced through Compose. Its private
configuration was backed up before enabling the configured MiMo collector.
The fixed localhost ports, named volumes and trusted certificate were retained.
The deployed HTTPS/API/anonymous Cookie/SSE smoke passed
(`/private/tmp/dano475-c42da19c-smoke.log`). Both active owner workspaces passed
the real Bubblewrap preflight on XFS with their separate worker UIDs.

The authenticated in-app Browser proved main enable leaves automatic collection
unauthorized. Separate consent displayed policy `dano-collection-v1`, revision 1
and its effective timestamp. A synthetic preference was then submitted without
an explicit save tool. The ordinary answer completed, but no collection session
or request was registered; an assistant's claim to have recorded it is not
delivery evidence.

Diagnosis found the OA ownership-transfer path creates the destination session
root with default 0755 permissions. The public collection registry correctly
requires an owner-private 0700 root. The source provenance receipt was present.
The fix uses the existing safe-directory helper when creating that destination.
A regression test exercises ownership transfer with a separate protected session
root, checks content retention and the private permissions. All eight registry
tests and the server type check passed:
`/private/tmp/dano475-session-transfer-permissions-tests.log`,
`/private/tmp/dano475-session-transfer-typecheck.log`.

For continued diagnosis, only the identified synthetic acceptance owner's
session root and its affected child directory were tightened to 0700 using
no-follow directory handles, retaining UID and content. This fixture repair is
not proof that the new source fix has been rebuilt. Automatic delivery, recall,
withdrawal and concurrent pause gates remain pending until observed end to end.

After the fixture permission repair, a fresh synthetic request registered one
source session, reached collection `processed`, and produced an `automatic`
operation at `ready`. The browser showed the new ready record. Withdrawing
collection then displayed automatic collection unauthorized while long-term
memory stayed enabled (consent revision 2). In a new chat, the real model
correctly recalled the newly collected final-section preference, “松岚回顾”.
Screenshot: `/private/tmp/dano475-automatic-recall-browser.png`.
These observations are from image c42da19c with the scoped permission repair;
the newly committed creation fix 887afae2 is being rebuilt separately. No
concurrent pause/race, two-user or full parent release gate is implied.

The 887afae2 image subsequently built and replaced the local app through Compose.
Image digest: `63a9257c76350f8a72e753ad91e6152cd418b3107f3044fbb65b52f884ee34ec`.
Its HTTPS/API/SSE smoke passed (`/private/tmp/dano475-887afae2-smoke.log`). After
reconnecting the same authenticated browser, main memory remained enabled,
automatic collection remained unauthorized at revision 2, and all three ready
records remained visible. This proves persisted withdrawal survived this app
replacement; crash/claim races and a fresh real OA transfer still need their
separate acceptance cases.

### Multi-chat pause/resume and real transport reconciliation

On image 887afae2, two in-app Browser tabs shared the authenticated owner but
used separate chats. A new separate collection grant was visible in both at
revision 3. Pausing in the first chat made a real `memory_save` in the second
return `blocked / MEMORY_DISABLED`. Reopening settings in the second chat
showed main memory paused and collection authorization retained but suspended.
The save-record refresh button only reloads records; it does not refresh an
already-open authorization panel. The server enforcement was verified directly
through the second chat's real tool call.

Resuming in the first chat advanced the collection receipt to revision 4. The
second chat then collected a new synthetic quarterly-report preference
(“溪桥复盘”) to `ready`. A read-only audit matched the single paused synthetic
user entry (“雾杉收束”) against collection requests and operation sources:
zero selected and zero delivered. The two automatic operations both remained
ready, with no extra operation for paused history. Screenshots:
`/private/tmp/dano475-peer-paused-browser.png`,
`/private/tmp/dano475-peer-resumed-browser.png`.

The new [real reconciliation fixture](fixtures/openviking-pause-reconciliation.mjs)
imports the installed published 0.1.3 extension and uses isolated OpenViking
0.4.20 with fresh synthetic Alice/Bob identities. It intercepts only responses
after real append/commit mutations have succeeded. Verified results:

- Pause before claim prevents any remote mutation and clears the unsent body.
- A real append with a lost response remains `message_unknown`; a reopened
  store reconciles by reading, then blocks further commit and clears its body.
  Resume cannot replay it; exactly one append occurred.
- A real commit with a lost response remains `commit_unknown`; a reopened
  store queries the actual task and reaches `ready` while main memory is still
  paused. Exactly one commit occurred, with terminal body cleanup.
- Bob concurrently reaches ready while Alice is paused. Bob recalls only his
  own synthetic fact, not Alice's.

Evidence: `/private/tmp/dano475-real-pause-reconciliation.log`, private result
`/private/tmp/dano475-pause-reconciliation-s19eKr/result.json`. This fixture
reopens actual files but does not itself kill a process or prove browser
isolation; those remain separate from the existing SIGKILL fixtures and browser
checks. Full current Dano regression: 1591 passed, one skipped across 136 files
(`/private/tmp/dano475-current-full-tests.log`).

### Real branch lifecycle follow-up

In the same protected browser chat, a non-factual greeting was edited through
the product's real message editor (`navigate_tree`), preserving the earlier
quarterly-report preference as an ancestor. The edited response completed.
After selection, all four source requests were processed while only two
automatic operations and two collected source receipts existed: the shared
ancestor was not saved again. Screenshot:
`/private/tmp/dano475-edited-tree-browser.png`.

The [branch fixture](fixtures/pi-collection-branches.mjs) uses the installed
published extension 0.1.3 and locked pi 0.85.1, with real MiMo chat and selection
calls. It performs real runtime fork, tree navigation, reload and disposal.
Checkpoint operation counts are 1, 1, 2, 2: the original preference is collected
once, the fork greeting adds none, a new tree fact adds one, and the reload
greeting adds none. Original ancestor source IDs remain stable and occur once
in the operation ledger. No provider, selector output or pi transition is
mocked; the fixture deliberately claims no remote memory delivery.
Evidence: `/private/tmp/dano475-real-branches.log`,
`/private/tmp/dano475-real-branches-LIkOW8/result.json`.

### OA re-login boundary and final review gap

The latest protected image was exercised through real logout, a fresh anonymous
chat, and real OA authorization back to the fixed localhost origin. Anonymous
memory settings said login was required and offered no memory grant. A synthetic
anonymous preference (“匿名鹿鸣”) was retained in the conversation after login,
but not collected. The logged-in account retained its own consent revision 4 and
four ready records. A subsequent non-factual authenticated message completed.
The source audit found one anonymous entry, zero selected/delivered references,
five processed collection requests, and still only two automatic operations.
Screenshot: `/private/tmp/dano475-oa-rebinding-browser.png`.
This is an existing-account re-login, not creation of a second OA account or a
forced token-refresh test. It does not replace the independent-browser identity
gate in the parent release acceptance.

Refetched upstream/main and origin work branch; both rebases were already
up to date. Review base is `8056d71eabf0bfdeb6b67e16b8899f3fd4776520`, reviewed
HEAD `2a481ccd8810b2fda98800c0a777f64c7e15fd14`.

Standards review: zero actionable violations or heuristic findings. The existing
safe-directory helper, public extension APIs and Pi ModelRuntime are reused;
no ask_user_question contract changes require its guide to change.

Spec review: one P2 functional gap remains. FR-02 requires collection from both
conversation and confirmed task results; Spec §5.3 requires allowlisted,
declassified task facts. Dano's protected services currently configure only the
selection model and budgets, never `taskFacts`. The published extension's
synthetic business-tool fixture proves its interface, not Dano's production
integration. This gap belongs to #475 and prevents signing off or merging it.

The next implementation must use Dano's actual authenticated provider path:
`CredentialBroker` and `withProviderPython` observe real HTTP responses and
binding evidence before returning them to the tool. `wrapProviderBash` already
overwrites its own `providerRequests` metadata from trusted observations. A
task-fact adapter must project only configured successful response fields with
verified actor/owner binding, never parse arbitrary bash stdout or trust a
model's claim of business success. The projection must remain available through
the protected original pi result for restart/fork, while excluding raw response
bodies and credentials from collection. Both direct `provider_request` and
Python-through-bash paths need coverage, including forged metadata, failure,
foreign actor, revoked authorization and sensitive fields. Deployment-owned
contracts and policy revisions must drive the allowlist; do not hardcode an OA
endpoint or business decision to satisfy this acceptance case.

### Confirmed task result implementation follow-up

Dano now exposes optional deployment-owned `collection.taskFacts` contracts:
exact method/path, an explicit business success predicate, a result actor field
and typed scalar field allowlist with byte limits. The result actor must map to
the same immutable OAuth subject mapping used at login. The canonical mapping
was extracted without changing existing user identifiers. Unknown routes,
unsuccessful/malformed responses, foreign actors and arbitrary object fields are
excluded rather than interpreted by a model.

The owner runtime provides a capture callback to both direct provider_request
and the protected Python/bash wrapper. It requires separate current consent and
trusted evidence that the actual HTTP send used the initiating login and provider
origin. The raw body is projected locally; stdout is not a fact source. A
domain-separated HMAC receipt binds only the approved projection to owner,
tool/call, policy, epoch, scope and consent revision. The receipt lives in the
protected original pi result, enabling restart/fork verification without a
second raw-result database. The selector verifies signatures and the current
grant, then applies the extension's existing task-fact and secret screening.
Runtime disposal disables capture and erases its derived signing key.

Validation so far:

- First related suite: 101 passed across six files.
- Real loopback HTTP and real Python integration exercised both transport paths,
  forged worker metadata replacement and withdrawal preserving business output:
  39 passed across the transport/core files at that revision
  (`/private/tmp/dano475-task-facts-transports-tests.log`).
- Public extension input-builder screening: five core tests passed, including
  projected credential rejection before inference, failed tool result rejection,
  foreign owner/call, stale grant/epoch, malformed routes and receipt tampering
  (`/private/tmp/dano475-task-facts-screening-tests.log`).
- Type/Svelte check passed with zero errors/warnings
  (`/private/tmp/dano475-task-facts-check.log`).
- Full regression reached 1597 passed, one skipped, one failure caused by the old
  detached-session mock expecting a single argument instead of the new optional
  capture argument. Its assertion was updated and all three detached-session
  tests then passed (`/private/tmp/dano475-task-facts-detached-tests.log`).

These are deterministic/integration results, not final release acceptance. The
new source still needs final review, a verified actual OA response contract in
the isolated deployment, real model/OpenViking task-fact evidence and a rebuilt
protected image. Do not label the prior P2 fully accepted from these tests alone.

### Task-fact image and acceptance follow-up

Both review axes completed their follow-up on `4b8c6c53`: no new findings;
the Spec reviewer considered the production task-fact wiring gap fixed in code,
with real provider/model/delivery acceptance still outstanding. The source build
passed. The protected image `localhost/dano475-protected-image:4b8c6c53` built
successfully (image ID
`8ee73868b7eac71f5bd897e74cf94e6d041b11fffab8c9bb672adc59a67b3bab`)
and replaced only the existing isolated local acceptance deployment.

The first smoke attempt timed out creating a client during startup. Once the
browser reconnected, the repeated smoke passed health, anonymous cookie, client
creation, SSE, command/response and disconnect. Evidence:
`/private/tmp/dano475-4b8c6c53-smoke.log` and
`/private/tmp/dano475-4b8c6c53-smoke-ready.log`. Both active worker workspaces
passed the existing Bubblewrap preflight on XFS with exit code zero. The browser
reconnected at the persistent trusted `https://localhost:18711/` origin without
a new certificate trust operation.

The attempted browser OA schema probe did **not** execute a provider request:
the application model declined it because the corresponding OA capability was
not configured. This is neither an OA response-contract result nor task-fact
acceptance. The deployment still has no approved task-fact contract; real
contract verification, selection/delivery and new-image product gates remain
open. No production deployment was performed.

### Production task-fact adapter to real model and OpenViking

`docs/research/fixtures/dano-task-fact-delivery.mjs` passed using the installed
pi `0.85.1`, published extension `0.1.3`, Dano's actual `CredentialBroker` and
`MemoryTaskFacts`, real `mimo-v2.5`, and the isolated real OpenViking service.
The business HTTP server and Assistant Turn events are explicitly synthetic.
The fixture does not claim real OA semantics or a model-initiated business call.

The broker sent three authenticated loopback HTTP requests using host-held
synthetic credentials. The owned successful response produced one signed
allowlisted projection. After reopening the native on-disk pi session and
constructing a new receipt verifier, the real selector chose one fact sourced
from the tool-result entry. Real delivery reached `ready`, and real recall
returned the synthetic `REPORT-731` template. A foreign actor produced zero
receipts and zero selected facts. After consent withdrawal the business request
still succeeded, but no collection request or receipt was produced. Assertions
also excluded raw private response fields and provider credentials from selector
input and collection state.

Evidence: `/private/tmp/dano475-task-delivery.log` and
`/private/tmp/dano475-task-delivery-rywXR7/result.json`. Two real selector calls
took 1900 ms and 2266 ms. These isolated timings are not the full T14 benchmark.
The private synthetic remote-owner credentials are retained only in the mode-0600
run artifact for scoped cleanup; they are not source or report data.

### New-image browser regression and actual OA response shape

The `4b8c6c53` image passed real in-app-browser plain chat, model-triggered
`bash ls`, and upload/vision. MiMo returned the requested chat marker, executed
`ls`, and correctly described the fixed synthetic upload as red circle, blue
square and yellow triangle from left to right. Screenshots:
`/private/tmp/dano475-4b8c6c53-chat-bash-browser.png` and
`/private/tmp/dano475-4b8c6c53-image-browser.png`. This used the same persistent
HTTPS origin and one upload of the existing approved synthetic image.

A separate administrator diagnostic ran inside the isolated container against
the configured real profile endpoint. It reused the encrypted server-side
login credential in process memory and emitted only HTTP/business status,
field names/types and whether the actor matched the authenticated owner. It
did not print identifiers, credentials or profile values and did not collect
memory. The initial direct request returned HTTP 200 with business code 401;
unlike the application's broker path, this diagnostic did not refresh tokens.
After normal browser logout and real OA login back to the clean fixed origin,
the repeated request returned HTTP 200, business code 0, and verified that
`data.id` maps to the stored authenticated Dano owner. The response has scalar
profile fields plus nested company/department objects and role arrays.

Evidence: `/private/tmp/dano475-oa-contract-shape-current.log`; the earlier 401
is retained in `/private/tmp/dano475-oa-contract-shape.log`. The browser showed
the existing memory records after re-login; screenshot:
`/private/tmp/dano475-4b8c6c53-relogin-memory-browser.png`.

This proves a real read-only response/actor contract, not a real task mutation
or production collection policy. The isolated profile has no trusted OA Skills
and no `collection.taskFacts` contract yet. Deployment contract configuration,
its new-policy consent boundary, and protected-runtime task-fact capture remain
to be exercised before signing off #475. Real personal profile values must not
be used as synthetic acceptance facts.

### Policy upgrade and real protected task-fact capture

The isolated deployment now has an administrator-owned read-only OA connectivity
Skill and a `dano-collection-v2` contract. The only projected value is the numeric
business success code; real profile values are excluded. Original private
configuration files were backed up beside the deployed files before the change.
The browser proved startup revoked the old v1 collection grant (revision 5)
while preserving the main memory switch. Explicit reauthorization created a v2
grant at revision 6. Screenshot:
`/private/tmp/dano475-policy-v2-revoked-browser.png`.

The first actual Skill invocation exposed a production integration defect:
approved Skills were discoverable but neither their files nor the Provider
Python module directory were included in the worker's Heimdall path policy.
Read and Shell calls returned `SUPERVISOR_OPERATION_FAILED`. The attempted turn
was stopped rather than left repeating failures.

Commit `d63df28a` passes canonical root-validated installation resources from
the privileged launcher to workspace policy generation as read-only paths.
No host RPC accepts additional paths. Tests exercise the actual Heimdall access
rules and Bubblewrap mount arguments, including unrelated-directory exclusion
and workspace/resource overlap rejection. Related suite: 21 passed; full
type/Svelte check passed. Standards and Spec follow-up reviews found no new
code findings. Evidence: `/private/tmp/dano475-approved-resource-tests.log` and
`/private/tmp/dano475-approved-resource-check.log`.

The rebuilt protected image and the same test-only Skill overlay are running as
`localhost/dano475-protected-image:d63df28a-taskfact` (image
`b83a6713a4a90178aca4578e1c902874b91390bdabae8b447dd7b022a86a0beb`).
The repeated real browser invocation read the Skill and executed its Python
through the protected wrapper. Actual OA returned HTTP success and business code
0. Source audit found one signed receipt, a valid HMAC, one login-bound successful
request, and only the approved status field. Both collection requests subsequently
reached `processed`. Real model-triggered Shell checks confirmed approved Skill
read succeeds, write is denied, an unapproved installation file is inaccessible,
and host-private configuration cannot be opened. These checks opened descriptors
only and did not read private contents or alter any file.

Evidence: `/private/tmp/dano475-d63-smoke.log`,
`/private/tmp/dano475-taskfact-runtime-audit-details.log`,
`/private/tmp/dano475-oa-taskfact-success-browser.png`, and
`/private/tmp/dano475-approved-resources-isolation-browser.png`.

**Negative-case failure remains:** the selector chose the complete transient
status projection as a fact. The resulting automatic operation terminated with
`MEMORY_NO_EXTRACTED_FACT`; OpenViking produced no usable fact. This is a real
false-positive selection, not a successful durable-memory test. The first
independent semantic replay excluded the same transient projection (7/7 cases
passed), so it does not erase the deployed failure. The semantic fixture now
includes the actual request wording and repeated trials to investigate stability.
Do not sign off the task-fact quality gate until this discrepancy is resolved.

The expanded replay reproduced the deployed failure in all five trials with
the actual OA request wording: 7/12 total cases passed, and every
`readonly-connectivity-status-*` trial incorrectly selected the full status
projection. This is now a repeatable semantic regression, not merely a
nonreproducible model response. Evidence:
`/private/tmp/dano475-task-transient-repeated.log`; dataset hash
`608e41881ca24d22bbd85165d06680d888f79f5ff1a37c3199bbcba82cec3171`.
The deployed negative result remains failed pending an extension-level
selection fix, published integration and repeated browser acceptance.

### Published selector durability correction (0.1.4)

The independent extension now treats a verified `task_fact` as a candidate,
not automatic evidence of lasting value. Successful connectivity, health and
authentication checks, transient status codes and test markers are excluded;
reusable business outcomes still require independent semantic selection.
No OA route, field name or acceptance marker is hardcoded in the selector.

The same 12-case MiMo replay passed, including all five previously failing
connectivity trials and the reusable report-template positive case. The dataset
hash remains `608e41881ca24d22bbd85165d06680d888f79f5ff1a37c3199bbcba82cec3171`;
the new prompt hash is
`02bc2c5a96d9af36fddb7413546c23ce0d8389d45250cfd09a007b369ba8b443`.
The existing seven conversation-selection cases and 184 extension tests also
passed. Logs: `/private/tmp/dano475-task-durability-repeated.log`,
`/private/tmp/dano475-durability-conversation-selection.log`, and
`/private/tmp/dano475-extension014-tests.log`.

[Extension PR #2](https://github.com/josephyoung/pi-openviking/pull/2) merged as
`8ca9d9616ecb90386eeb720689571952f86f8539`; its remote branch was removed.
GitHub Actions run `35621630456` published version `0.1.4`. Fresh official
registry metadata and the downloaded tarball agree on integrity
`sha512-YU2vP33ypNctuY329MAZlkT8VkFnzI2eRTTbIeC3ijncZG52h9AGPoK4j+/zG3qNipGDFtKJz0dDdKL9yT4ReA==`.
Both required keywords are present. Despite a publish-log normalization warning,
registry metadata and the tarball retain the `pi-openviking` bin mapping; the
referenced `dist/cli.js` exists with its Node shebang.

This release and independent replay do not replace the rebuilt Dano browser
negative-case gate or the complete T-14 evaluation.

Dano's manifest and lockfile now pin this official `0.1.4` artifact. Installed
CLI invocation reaches its expected fail-closed profile validation. Full
type/Svelte checks pass. The full test run passed 1599 tests, skipped one and
timed out in one anonymous-release test; that file then passed all six tests in
an isolated retry. Logs: `/private/tmp/dano475-extension014-dano-check.log`,
`/private/tmp/dano475-extension014-dano-tests.log`, and
`/private/tmp/dano475-extension014-anonymous-retry.log`.

The real MiMo/OpenViking task-delivery fixture also passed against the installed
release: one confirmed reusable result reached ready and recall, a foreign actor
produced no receipt/fact, and withdrawal preserved the business request while
preventing collection. It used a synthetic business endpoint, not a real OA
write. Evidence: `/private/tmp/dano475-extension014-task-delivery.log` and
`/private/tmp/dano475-task-delivery-213foa/result.json`. Both incremental review
axes reported zero new findings; rebuilt-image browser acceptance remains open.
