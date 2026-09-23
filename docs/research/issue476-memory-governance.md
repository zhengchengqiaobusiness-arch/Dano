# Issue 476: memory governance implementation record

## Scope and starting point

Authoritative requirements are [#476](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/476)
and [the parent Spec](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/465#issuecomment-5674833976).
Both were refreshed on 2026-09-22. Prerequisite #475 was merged in PR #482
(commit `ce04e89e58b09802b394defa959e5e470502a3b4`). This document is an
implementation record, not a completion claim.

## Transport groundwork verified

The independent extension branch `codex-476-memory-governance` starts at the
published 0.1.5 source. OwnerMemoryClient now supplies three host-side transport
primitives, not model tools:

- `replaceMemory`: checks the bound owner/project URI, writes using the public
  replacement API with wait enabled, then requires exact content readback.
- `removeMemory`: deletes one ordinary Markdown memory document without
  recursive deletion, then requires an actual 404. It rejects directories,
  hidden metadata files, traversal/encoded references and foreign scopes.
- `removeSource`: accepts an owner/scope-bound operation and validates its
  remote session reference, then deletes and verifies the source is absent.

Permission failures and stale successful responses are not deletion success.
Repeated deletion can reconcile an already absent target. These are transport
operations only: their caller must persist revocation and drain writers first.
They are not yet connected to a user-facing governance operation.

Validation:

- Extension build and all 198 tests pass, including four new transport cases
  covering identity/project boundaries, stale responses, nonrecursive deletion,
  source binding and local rejection before any network access.
- The real-service fixture `fixtures/openviking-governance-transport.mjs`
  provisions a synthetic isolated account, obtains a real extracted memory,
  replaces its synthetic title, verifies exact readback, deletes the document
  and source, and repeats both deletions successfully.
- Logs: `/private/tmp/dano476-extension-tests.log` and
  `/private/tmp/dano476-real-governance-transport.log`; real run result:
  `/private/tmp/dano476-governance-transport-dscDtU/result.json`.
- This proves transport behavior only. It does not prove the persistent
  governance barrier, same-source multi-fact preservation, cache invalidation,
  concurrent old extraction cleanup, or browser acceptance. The extension has
  not been published or pinned into Dano for this change.

## Next implementation boundary

The existing public API has no compare-and-swap replacement; an HTTP delete
cannot cancel previously accepted extraction. The production adapter must
therefore coordinate writers using the same durable owner state that claims
outbox delivery, rather than relying on a separate in-memory lock.

1. Persist a governance job and revision before remote calls. Resolve targets
   from trusted owner/scope state; record source/version references and the
   affected document identity. Pending governance must suppress affected recall
   including cached text; recheck revision after remote reads.
2. Interlock enqueue, delivery claims and selection handoff with the same state
   transaction. Block old unsent work and keep already-sent references for
   reconciliation. Do not acknowledge cleanup while any accepted old mutation
   remains unresolved. Source suppression must survive forks and restart.
3. Drain old work, then edit/remove using public APIs. A document or source can
   contain multiple facts: preserve unrelated content, and retain only the
   metadata needed to stop old source replay. Remove old remote source archives
   after resolving their writers. Temporary replacement content must be erased
   after completion; deleted plaintext is not a permanent tombstone.
4. Reconcile ambiguous remote results and derived data before completing the
   job. Persist failures as recoverable cleanup, never as success. Later explicit
   re-save is a new version; old automatic sources remain suppressed.
5. Expose one governance service to model tools and authenticated controls.
   Resolve ambiguity before mutation, enforce existing-style clear confirmation,
   preserve management access while paused, and disallow correction while paused.
6. Add scoped export and account retirement. Keep the sole cleanup credential
   until remote cleanup completes. Finish real concurrent extraction, multi-fact,
   multi-owner/project and browser acceptance before release/PR closure.

None of these remaining steps is waived by the successful transport probe.

## Durable barrier and writer interlock

The independent extension now has `MemoryGovernanceBarrier.begin`. It accepts
only a trusted scope and an existing document reference, persists a monotonic
governance revision in the same flock/fsync-protected owner state as delivery,
and records affected operations plus all pre-barrier scope writers. Only one
pending job per scope is permitted. Correction is disallowed while paused;
forget/clear remain permitted. No completion API is provided yet.

The interlock is active in explicit enqueue, selection handoff, delivery claims,
selection scheduling and direct selector calls. Related unsent operations lose
their payload and cannot be reactivated; other unsent scope operations remain
held with their payload intact. Holding does not consume delivery or model
retry budgets. Unknown accepted mutations can still reconcile. Their late
responses cannot reopen a revoked send phase. Other trusted project scopes
remain usable.

Recall suppresses the pending scope, discards cached context and rejects a
result if governance changed during the search. Source tombstones contain hashes
of owner/scope/stable pi entry identity, not deleted text; copied branches and
reopened runtimes cannot reauthorize the old source. Fresh explicit input has a
new entry identity and is not permanently banned from saving the same content.

Tests cover these boundaries, malformed persisted jobs, two competing writer
processes and SIGKILL after durable registration. The tests that transition a
job to complete do so with an explicitly labelled test-only state transaction;
they do not prove remote cleanup. The production coordinator must still drain
or selectively edit **every** pre-barrier writer before completing, including
other old queued sources that could refer to the same fact. Simply releasing
unrelated-looking queued work is insufficient proof of non-resurrection.

Next: implement that coordinator and bounded retry/recovery, preservation of
unrelated facts in shared documents/sources, remote and derived cleanup, scoped
export/retirement, then shared model/UI controls and real-service/browser gates.

Final barrier build and extension suite: **211/211 passed**.
Log: `/private/tmp/dano476-barrier-complete-tests.log`.

## Whole-scope clear coordinator

`MemoryClearCoordinator` now implements the clear path only. It checks every
pre-barrier writer through owner/scope-bound read-only remote evidence before
persisting `applying`. Unknown commit results remain pending; a failed task is
only evidence that extraction stopped, not that its partial data is absent.
Automatic delivery retry exhaustion now preserves its last phase for this
reconciliation instead of losing whether a mutation had been sent.

The coordinator removes old source sessions, removes the client-bound memory
tree through the public recursive API and verifies absence, then commits local
completion and drops payload/current-memory references. Errors retain the job,
suppression and cleanup credentials. Restart in `applying` does not depend on
an archive already removed by the preceding attempt. A dedicated kernel flock
covers remote cleanup through completion: another cleaner cannot finish later
and delete a newly authorized version. SIGKILL releases the lock; durable job
state remains for recovery. No lease timeout is used as permission for a second
live remote writer.

The six coordinator tests cover pending extraction, another project's state,
source/clear errors, concurrent cleaners, identity/scope rejection and actual
child-process SIGKILL during applying. This fault test uses a synthetic
transport and is not claimed as a real remote-crash test.

### Project extraction issue found during real clear validation

The first real-service run could not confirm the project's initial memory.
Inspection found that an actor header alone restricted access but did not assign
message provenance to a peer. Project-bound sessions now explicitly disable
self extraction and enable peer extraction; append sets `peer_id` from the
trusted client scope. No model-provided project is accepted.

An intermediate attempt also disabled working memory. On OpenViking 0.4.20,
`get_session_archive` requires an overview and that setting suppresses it, so
source verification correctly failed with 404. That unrelated setting was
removed: ordinary archive generation stays enabled. The adapter still requires
its real source/archive proof. Failed-run logs are retained as
`/private/tmp/dano476-real-clear-coordinator-before-peer-fix.log` and
`/private/tmp/dano476-real-clear-coordinator-working-memory-disabled.log`;
neither run counts as acceptance.

### Real-service clear and recovery evidence

The corrected fixture completed against the isolated OpenViking service with
real MiMo extraction in flight. It observed the durable pending barrier,
confirmed that recall stayed suppressed while extraction completed, removed
the old source and memory, and verified that another project and another user
were preserved. A copied old Pi entry was blocked; a fresh explicit save of
the same fact created a new version; a later coordinator retry did not remove
that new version. The result is in
`/private/tmp/dano476-clear-coordinator-6oNYzX/result.json` and the log is
`/private/tmp/dano476-real-clear-coordinator.log`. Its `browserVerified` field
is false.

The first project-specific follow-up lost its isolated service connection.
The local embedding model loaded, but the restricted process could not create
a macOS Metal command queue. The same model passed the minimal loader outside
the sandbox, and the service was restarted without changing its data or
configuration. The pending project clear job remained durable and resumed;
the follow-up then removed project memory while retaining the newly saved
global version and the other user's memory. See
`/private/tmp/dano476-real-project-clear-retry.log` and the synthetic
`project-clear-result.json`. No browser acceptance or selective governance is
claimed by either run.

## Scoped export groundwork

`MemoryExportService` reads document content and known source metadata only
through the current host-bound owner/project transport. The caller can choose
a bounded page size and opaque cursor, but cannot supply an owner or project.
The cursor binds owner, scope and state revision; malformed, cross-scope and
stale cursors fail closed. The transport checks every listed document URI under
its bound memory tree, and the export service rechecks state after remote reads.
A pending governance job suppresses export of that scope so content already
marked for deletion is not returned. Paused memory still permits export.

The synthetic real-service follow-up paged Alice global, Alice project and
Bob global separately. It verified that Alice's newly saved content and
source metadata appeared, the cleared project did not, Bob's memory remained
isolated, and no API keys were emitted. Result:
`/private/tmp/dano476-clear-coordinator-6oNYzX/export-result.json`; log:
`/private/tmp/dano476-real-export.log`. This is a host-side export probe, not
the browser export acceptance or the full account-retirement path.

## Selective correction and forget groundwork

`MemorySelectiveService` resolves one exact text selection in one bound memory
document before registering the durable barrier. A duplicate selection is
reported as ambiguous before mutation. The persisted plan contains the
selected and replacement text only while pending; verified completion erases
that plaintext. Once old scope writers settle, the coordinator removes the
target's old source sessions, edits every document containing the exact old
selection, verifies it is absent, then releases the scope. A shared document
keeps its unrelated lines. Old pre-barrier entries are revoked and a fresh
explicit entry may save the same fact afterward. Failed remote responses leave
the plan pending for recovery. Corrected documents keep the governance revision
and prior operation linkage as metadata; export includes completed correction
or forget revisions without exposing cleanup credentials.

The isolated real-service fixture added two synthetic facts to one document,
corrected one, then forgot that new version. The unrelated line remained and
neither old nor corrected fact appeared in the subsequent real recall. Result:
`/private/tmp/dano476-clear-coordinator-6oNYzX/selective-result.json`; log:
`/private/tmp/dano476-real-selective.log`. This validates exact-text remote
behavior, not semantic paraphrase matching, a new browser chat, or the full
model-tool/management flow. Those remain required by #476.

## Shared governance entry point and recovery

The extension now exposes one `MemoryGovernanceService` for authenticated
host controls and model tools. It binds the state store and OpenViking client
to the same owner/scope, returns a durable job ID with `pending` or verified
`complete`, and offers an owner-level scheduler that resumes pending jobs
without an open browser. Model tools for correction, forget, clear, export
and status call that service; model-driven clear also requires an actual
`ctx.ui.confirm` response before registration. Export content is labelled
untrusted memory data. The extension suite passed **228/228** serially after
these changes (`/private/tmp/dano476-governance-tools-suite.log`).

Dano still uses published extension 0.1.5 and has not wired this service to
its authenticated management controls or tested the browser flows. The
extension changes remain on an unpublished work branch. Account retirement,
project host integration and the remaining acceptance gates are outstanding.

## Two-axis review fixes before publication

The review found that the first selective barrier revoked all pre-barrier
scope sources, which dropped unrelated queued facts. It now revokes only
sources linked to the selected document or exact selected text. An unrelated
queued writer remains held while governance is pending, then the coordinator
advances it under the same durable barrier before editing remote documents.
The normal scheduler cannot use this privileged drain, and an unrelated
writer cannot be released after completion without first being reconciled.
Already-pending automatic selection requests are discarded because their
unselected contents cannot be proven unrelated; this is not claimed as
preservation of an unsaved fact.

The review also found that clearing `memoryUris` on correction erased the
source relationship for a surviving shared document. The URI lineage is now
retained while the old payload is erased and its source marked revoked.
Scoped export distinguishes current from revoked sources and includes
completed governance revisions. URI validation now shares one owner/scope
parser across transport, state, export and selection. Export preflights each
document's remote size, bounds both document and page bytes, and makes an
oversized document an explicit error instead of silently truncating it.

These fixes have targeted tests. The new isolated real-service fixture
`fixtures/openviking-selective-drain.mjs` verifies an unrelated queued fact
is actually delivered before the selected correction completes, and that
export retains its source relationship. The run passed against real MiMo and
OpenViking: `/private/tmp/dano476-selective-drain-H17AW6/result.json`, log
`/private/tmp/dano476-real-selective-drain.log`. This is host/service proof;
the browser acceptance is still pending.

Post-review extension type check/build and serial suite passed **230/230**.
Log: `/private/tmp/dano476-review-fixes-suite.log`.

## Fact-level follow-up (2026-09-23, still unpublished)

The second review showed that the earlier selection barrier still discarded
unrelated automatic candidates, and that one automatic operation could contain
several facts. The extension now records each selected fact as an independent
operation and remote Session. A selective job waits for registered pre-barrier
collection requests and their outbox operations before remote editing. It
keeps current facts from the same pi entry, uses fact-specific source digests
for the targeted operation, and blocks any later replay of the old entry after
the barrier completes. A shared document whose selected text cannot be tied
to one source fails with `MEMORY_TARGET_AMBIGUOUS` before mutation. This is a
safe ambiguity result, not a completed correction.

The export transport now limits HTTP response bytes before JSON decoding,
even if OpenViking's `stat` size is stale. The page budget includes serialized
source metadata and cursor; the configured document limit cannot exceed the
transport limit. The extension serial suite passes **234/234** after these
changes (`/private/tmp/dano476-fact-all-tests5.log`).

`fixtures/openviking-fact-split.mjs` and
`fixtures/openviking-fact-split-verify.mjs` ran against isolated OpenViking
and MiMo with a fresh synthetic account. Result:
`/private/tmp/dano476-fact-split-fQk3jr/result.json` and
`/private/tmp/dano476-fact-split-fQk3jr/recall-result.json`; logs:
`/private/tmp/dano476-real-fact-split.log` and
`/private/tmp/dano476-real-fact-split-verify.log`. The two facts in one source
became independent operations; after forgetting the tea fact, the unrelated
meeting fact remained current and recallable, while old tea text was absent
from both remote documents and recall. The real service placed them in
different documents. Unit tests additionally cover shared-document ambiguity
and an old branch selecting a second old phrase from the same entry.

An unresolved path remains: an already queued extraction from a different
source can finish with a semantic paraphrase of the deleted fact in another
document. Exact-text document editing and source digests cannot prove that
this paraphrase was removed. Governance must stay pending on such ambiguity
or gain a verifiable source-level resolution path before #476 can be accepted.
Dano management integration, account retirement, browser acceptance, package
publication and exact Dano pin remain outstanding.
