# Executed adapter contract for #473

Verified environment: OpenViking 0.4.20, pi 0.82.1, TypeScript SDK 0.1.0.
Date: 2026-09-18. This table connects observed public API behavior to the
adapter responsibilities. It does not declare #473 or #465 complete.

The [static contract](openviking-public-api-contract.md) pins upstream source.
The [validation record](openviking-validation-status.md) contains outcomes and
limits. Fixtures require isolated synthetic services; secret-bearing run
state remains outside the checkout.

## Operations

All paths below start with `/api/v1`. USER requests use a credential bound to
the trusted account/user. ROOT is used only for administration. A trusted
project scope sets `X-OpenViking-Actor-Peer`; model arguments must never choose
credentials, account/user IDs, actor headers or arbitrary remote URIs.

| Operation and principal | Sanitized request | Preconditions and verified postcondition | Retry contract and fixture |
|---|---|---|---|
| Provision USER; ROOT/admin | `POST /admin/accounts/{account}/users`, `{"user_id":"alice","role":"user"}` | Trusted identity mapping exists; issued key accesses only its account/user scope | Resolve existing identity before retrying creation; [Session contract](fixtures/openviking-session-contract.py) |
| Rotate USER key; ROOT/admin | `POST /admin/accounts/{account}/users/alice/key` | Same user owns old data; old key receives 401 and new key accesses it | Rotation is not idempotent; protect and atomically replace host credential reference; [Session contract](fixtures/openviking-session-contract.py), [snapshot recovery](fixtures/openviking-snapshot-recovery.py) |
| Create Session; USER | `POST /sessions`, `{"auto_commit_policy":null}` | Host binds returned Session to owner and operation before message delivery | Raw Session ID is not globally unique; missing Session append can create a new owner-local Session; [Session contract](fixtures/openviking-session-contract.py) |
| Append source; USER | `POST /sessions/{id}/messages`, `{"role":"user","content":"synthetic fact","source_message_ids":["source-1"]}` | Durable source/operation record and writer lock precede transport | Source IDs do not deduplicate. On unknown result reconcile context/archive before retry; absence in truncated context is insufficient; [lost response](fixtures/openviking-lost-response.py), [multiwriter](fixtures/openviking-multiwriter.py) |
| Commit; USER | `POST /sessions/{id}/commit`, `{"keep_recent_count":0}` | Explicit source delivery proven; accepted result has task/archive reference | Six concurrent commits produced one accepted task and five skipped replies. Reconcile by dedicated Session; do not treat skipped as saved; [commit race](fixtures/openviking-commit-race.py) |
| Reconcile task and archive; USER | `GET /tasks?task_type=session_commit&resource_id={id}`, then `GET /sessions/{id}/archives/{archive}` | Exactly matching owner, Session, source and task; task completes and relevant content is retrievable | Persist receipts before upstream expiry. One real server crash recovered the original task without retransmission; [server crash](fixtures/openviking-server-crash.py) |
| Recall; USER with trusted Peer | `POST /search/find`, `{"query":"synthetic preference","target_uri":"viking://user/alice/peers/project-a/memories","limit":10}` | Exact owner and authorized scope fixed before search; direct reads enforce the same boundary | Do not broaden empty/invalid scope. Another actor's read/write/search receives 403; no actor header restores owner-wide view; [Peer boundary](fixtures/openviking-peer-boundary.py) |
| Correct one fact; USER | `POST /content/write`, `{"uri":"authorized memory URI","content":"corrected document","mode":"replace","wait":true}` | Writer exclusion, drained old extraction and preserved unrelated content/metadata | No compare-and-swap parameter. Reconcile read and search after ambiguous writes; [governance](fixtures/openviking-governance-primitives.py) |
| Forget; USER plus protected host state | Content replacement or `DELETE /fs?uri=...&wait=true`, then `DELETE /sessions/{id}` | Persist revocation, prevent new delivery, drain accepted work, remove selected fact and source, verify derived content | Raw old-source replay resurrects content. A durable source barrier blocked replay after process restart; [deletion barrier](fixtures/openviking-deletion-barrier.py), [derived audit](fixtures/openviking-derived-content-audit.py) |
| User export; USER | `POST /pack/export`, `{"uri":"viking://user/alice","include_vectors":false}` | Owner-authorized scope; ZIP includes only public scoped content | Another USER export was rejected; forgotten literal absent from audited export; [pack recovery](fixtures/openviking-pack-recovery.py), [derived audit](fixtures/openviking-derived-content-audit.py) |
| Content backup/restore; ADMIN | `POST /pack/backup`, then upload and `/pack/restore` with `vector_mode:"recompute"` | Public content only; matching identities provisioned separately, writers controlled, deletion records replayed before release | Vector snapshot failed as incomplete. Restore can require overwrite for default directories; verify actual search, not HTTP success; [clean recovery](fixtures/openviking-clean-recovery.py) |
| Consistent full-state recovery; operator | Stop service, copy complete data/config/host state, verify copy, start isolated target | Service exit confirmed before copy; 630 files hash matched; identity/task/archive survived | Old snapshots restore revoked credentials/content. Reapply post-backup records before access; [snapshot recovery](fixtures/openviking-snapshot-recovery.py) |

## Operation-specific ready evidence

The operation must retain its trusted owner, scope, stable source entry IDs,
remote Session and accepted task/archive references. A `ready` transition needs
all of: resolved delivery outcome, completed extraction, applicable memory
content and scoped retrieval. `accepted`, HTTP 200, `skipped/no_messages`, an
empty extraction or an unrelated successful search cannot supply that proof.
Current fixtures prove those signals for synthetic samples; the production
adapter's state transitions still require implementation tests.

## Host boundary and lifecycle

- Real pi file and inline-factory entries executed model events, tree/fork,
  reload, new Session and shutdown. Source entry IDs survived fork. Hosts must
  bind and rebind extension services; empty bindings do not establish reload
  startup. Local CLI package install/remove also passed.
- Real Heimdall 0.2.17 blocked absolute denied paths but allowed a symlink read.
  A Linux prototype using distinct trusted/tool process UIDs blocked native
  read/write/edit and Bash access while preserving workspace file access.
  Its synthetic HTTP probe does not replace actual service authentication.
- The final platform/process architecture, distribution obligations, general
  semantic correction and host-owned authorization are still gate decisions;
  these results do not authorize an unprotected implementation.

## Remaining evidence must retain its original scope

The experiments are bounded technical evidence. In particular, literal scans
cannot prove every semantic paraphrase is removed, advisory locks do not
protect against bypassing writers, and an offline snapshot is not a live
atomic backup. Final #473 review must resolve these implications against the
actual planned architecture rather than silently treating them as guarantees.
