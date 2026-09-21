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
