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
