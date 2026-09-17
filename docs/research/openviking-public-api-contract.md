# OpenViking v0.4.20 public API contract

Research date: 2026-09-17. Scope: Dano #465 / feasibility gate #473.

## Conclusion and evidence boundary

The official `v0.4.20` tag resolves to commit `b54001e2e5c974ffd7a09ba543813fa104a99561`. All links below pin that commit. This report inspects official source and documentation; it does **not** claim that the runtime experiments below passed. Source files were retrieved through GitHub's contents API; full-repository clone/download attempts were not needed for these findings.

The version exposes session append, asynchronous extraction, task reconciliation, file correction/deletion, user export and administrative backup. Those primitives do not, by themselves, establish Dano's exactly-once delivery, selective forgetting with source removal, or complete disaster recovery contract. In particular:

- Message append generates a new server message ID for every request; the public request has no idempotency key.
- Session commit acceptance is not extraction completion, and completion is not proof that a particular requested fact was extracted and is retrievable.
- Deleting a memory file is not a documented deletion of its source messages or a durable prohibition on extracting them again.
- Public backup excludes accounts, keys and queues, is not atomic, and restore preserves target-only paths. It cannot alone prove rollback or deletion durability.

Sources: [message creation](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/session/session.py#L1386-L1468), [session router](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/server/routers/sessions.py), [filesystem service](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/service/fs_service.py#L381-L446), [OVPack contract](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/docs/en/api/14-ovpack.md#L291-L373).

## 1. Message delivery and reconciliation

| Operation | Public contract | Consequence for the adapter |
| --- | --- | --- |
| `POST /api/v1/sessions` | Optional caller-supplied `session_id`, `memory_policy`, `auto_commit_policy`, `memory_extraction_config`. Response includes resolved session identity and URI. | A deterministic session ID can associate an operation with a remote session; it does not deduplicate messages. Verify repeated create semantics separately. |
| `POST /api/v1/sessions/{id}/messages` | `role`, `content` or `parts`, optional `peer_id`, `created_at`, `turn_id`, `message_kind`, `source_message_ids`. Missing session is automatically created. Response reports `message_count` and `pending_tokens`. | Neither a stable caller message ID nor an idempotency key is accepted by the declared request model. Never infer acknowledgement of a particular operation solely from aggregate count. |
| `POST /api/v1/sessions/{id}/messages/batch` | Up to 100 messages using the same model; response adds `added`. | Batching does not introduce deduplication. |
| `GET /api/v1/sessions/{id}` | Session metadata, user and URI, pending tokens and configuration. | Metadata can locate state but is not a message receipt ledger. |
| `GET /api/v1/sessions/{id}/context` and `GET /api/v1/sessions/{id}/archives/{archive_id}` | Assembled active context and a completed archive, respectively. | Reconciliation must check both active and archived source state and establish completeness; a bounded context response must not be treated as an exhaustive receipt list. |

Source: [session request models and routes](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/server/routers/sessions.py).

The implementation creates `msg_` plus a random UUID in both normal and tool-aggregate message paths. It copies `source_message_ids` into metadata, then appends every constructed message. The inspected append path does not compare incoming source IDs against stored ones. Therefore **`source_message_ids` is provenance metadata, not a server-enforced idempotency contract**. A repeated POST can append a second copy even with identical source metadata. Source: [message construction](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/session/session.py#L1386-L1468), [authoritative append](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/session/session.py#L1300-L1383).

**Experiment:** send one synthetic fact with a stable source ID twice, recording active and archived messages and IDs. Then drop the first HTTP response after the server accepts it, restart the adapter, and prove that its reconciliation avoids a second append. Include a concurrent commit and service restart between append and reconciliation. A local queue with an operation ID alone does not close the remote acknowledgement gap.

## 2. Commit, extraction completion and readiness

`POST /api/v1/sessions/{id}/commit` performs archive preparation before returning and schedules extraction in the persistent QueueFS queue. The result includes `session_id`, `status`, `task_id`, `archive_uri` and `archived`. The request supports retention fields but declares no caller operation ID or idempotency key. Archive preparation uses a filesystem lock and a recoverable intent; this is useful crash-recovery machinery, not proof of the adapter's whole delivery contract. Sources: [HTTP commit](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/server/routers/sessions.py), [commit implementation](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/session/session.py#L1850-L1907), [service result contract](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/service/session_service.py#L401-L447).

Empty sessions return `status: skipped`, `task_id: null`, `archived: false`, `reason: no_messages`. If all messages fall inside the retention window, the reason is `all_within_keep_window`. Short-session acceptance must deliberately archive the relevant messages rather than interpreting either skip as a successful save. Source: [skip branches](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/session/session.py#L2032-L2109).

| Endpoint | Contract and limitations |
| --- | --- |
| `GET /api/v1/tasks/{task_id}?include_events=true` | Owner-scoped task status, result/errors and optional events. Not found includes expired records. |
| `GET /api/v1/tasks?task_type=session_commit&resource_id={session_id}` | Reconciliation surface when a commit response was lost; filters include status and limit (default 50, maximum 200). This list alone is not a permanent delivery ledger. |
| `POST /api/v1/tasks/{task_id}/cancel` | Cooperative cancellation; ROOT may not cancel tasks. Cancellation acceptance is not rollback of already written memory. |

Source: [task routes](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/server/routers/tasks.py). Completed/cancelled task records expire after 24 hours and failed tasks after seven days, as measured by the tracker's expiry logic. Source: [task tracker](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/service/task_tracker.py#L187-L188), [expiry calculation](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/service/task_tracker.py#L305-L309).

Extraction writes a done marker last, reports `memories_extracted`, skipped operations and optional `memory_diff_uri`, then calls tracker completion. The tracker records successful outcome and finalizes after owned work settles. These are suitable candidate readiness signals, but Dano still needs the requested memory's actual content and retrieval proof, and must account for skipped operations or zero extracted facts. Internal done markers must not become an adapter dependency when the spec requires public APIs. Sources: [extraction result](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/session/session.py#L2990-L3075), [tracker completion](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/service/task_tracker.py#L599-L617).

**Experiment:** archive a one-fact short session with `keep_recent_count: 0`; record the initial response, task transitions, result, memory content and cross-session search. Inject termination before and after archive preparation and during extraction. Drop the commit response and recover using only the public task/session/content surfaces. Test task expiry and more than 200 same-session task records rather than assuming an unbounded reconciliation history.

## 3. Correction, deletion and source removal

`POST /api/v1/content/write` accepts `uri`, `content`, `mode` (default `replace`), `wait` (default false), optional timeout, processing mode and tags. It provides file mutation plus processing, but its declared request has no expected version/hash or compare-and-swap field. Concurrent correction therefore needs a proven sequencing strategy; replacing a document must not accidentally erase unrelated facts or required memory metadata. Source: [write model and handler](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/server/routers/content.py#L38-L52).

`DELETE /api/v1/fs?uri=...&recursive=false&wait=true` removes the selected URI and exposes deletion/semantic-refresh results. The lower-level operation removes vector records and files under a path lock, treats a missing target as successful after orphan-index cleanup, and can report a busy resource. Source: [HTTP delete](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/server/routers/filesystem.py#L273-L311), [storage deletion](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/storage/viking_fs/_ops.py#L143-L280).

The public filesystem service refreshes memory overviews, but its resource-reference cleanup is conditional on `context_type == resource`. That helper tries a memory update, removes remaining resource links, and returns cleanup information. It does **not** establish selective deletion of arbitrary session-sourced facts. The filesystem deletion occurs before this helper in the inspected service, so a subsequent cleanup error is not proof the original resource still exists. Sources: [service ordering](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/service/fs_service.py#L381-L446), [resource-link cleanup](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/service/resource_memory_link_service.py#L354-L463).

`DELETE /api/v1/sessions/{id}` removes the session tree through VikingFS. Its service does not provide a fact selector or explicitly cancel related tasks in the inspected method. Conversely, deleting one memory file does not traverse and redact the original session messages. No public fact-level tombstone, source-redaction transaction or permanent suppression field was identified in the reviewed session/content/filesystem models. This is a bounded negative finding, not a claim about every extension or private deployment. Source: [session deletion](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/service/session_service.py#L343-L364).

**Experiment:** put facts A and B in the same source session and, separately, in the same generated memory file. Correct A while an older extraction is in flight; verify A's new value survives and B remains. Delete A with a queued commit, during extraction, after completion and after restart; inspect active messages, archives, generated memory, overview, search and an exported pack. Retry old delivery and extraction. Only call forgetting passed if A cannot reappear and B survives. If the public API cannot achieve this, record the missing contract as a gate failure; deleting all memories or hiding A only in the UI is not equivalent.

## 4. Export, backup, restore and rollback

| Operation | Actual scope |
| --- | --- |
| `POST /api/v1/pack/export` with `uri`, `include_vectors` | Streams a ZIP-format `.ovpack`; ROOT, ADMIN and USER roles are allowed subject to URI access controls. Scope is the specified subtree. |
| `POST /api/v1/pack/backup` | ROOT/ADMIN only; includes resources and all users' content in the current account, including sessions. Excludes runtime `temp`, `queue`, accounts and API keys. Reads live files; not an atomic snapshot. |
| `POST /api/v1/pack/import` | Uses a prior resource temporary-upload ID and a parent URI. Accepts `on_conflict` and `vector_mode`. Regular import rejects backup packs. |
| `POST /api/v1/pack/restore` | ROOT/ADMIN only; restores public roots. `overwrite` is merge-upsert: target-only paths remain. Accounts must be recreated with matching user IDs and target-environment keys. |

Sources: [pack routes and schemas](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/server/routers/pack.py), [export format](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/docs/en/api/14-ovpack.md#L13-L43), [backup/restore boundaries](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/docs/en/api/14-ovpack.md#L291-L373).

Dense vectors can optionally be packaged; hybrid index types reject vector snapshot export. Restore supports `auto`, `recompute` and `require`; compatible dense snapshots are checked against embedding configuration. A successful pack transfer therefore does not prove working search in a recovered installation. Source: [vector restore semantics](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/docs/en/api/14-ovpack.md#L158-L166).

The separate `/api/v1/snapshot/commit`, `/restore`, `/show`, `/diff`, `/log` APIs provide git-style content versioning. Snapshot restore is a forward commit and can report `RESTORE_WRITEBACK_PARTIAL` after HEAD advances. This is distinct from complete service backup and must not be assumed to cover identity, queues, or the adapter's durable operation/deletion state. Source: [snapshot router](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/openviking/server/routers/snapshot.py).

**Experiment:** stop new adapter writes and settle/reconcile in-flight extraction; capture the adapter ledger, ownership mappings, deletion state, OpenViking content, required identity configuration and pinned package/embedding versions as one documented recovery set. Restore into a clean isolated service, recreate identical user IDs with new keys, and run two-user content/search/export checks. Test an upgrade-window new path and an intervening deletion: merge-upsert restore alone must not preserve a path that rollback should remove or resurrect a deleted fact. Account for excluded queues explicitly instead of silently dropping unfinished operations.

## 5. Feasibility decision table

| Requirement | Static evidence | Evidence still needed |
| --- | --- | --- |
| Lost append response does not duplicate | No native message idempotency in inspected path; public source metadata exists | Fault-injected adapter reconciliation across active/archive state, concurrency and restart |
| Save becomes ready only when usable | Commit/task and content/search APIs exist; skip and failure states exist | One requested fact traced from source through extraction to cross-session retrieval |
| Correction preserves unrelated facts | File replacement exists; no request-level compare-and-swap | Concurrent old/new extraction, mixed-fact memory and version-order experiments |
| Forget removes source and prevents resurrection | File/session deletion and cooperative cancellation exist; no reviewed fact-level suppression transaction | Selective source removal, durable barrier, old queue retry, restart and restore experiments |
| User export is isolated | Scoped export and role/URI checks exist | Inspect two-user archives for forbidden content and credentials |
| Backup/recovery is complete | Public backup explicitly excludes identity/queues and is non-atomic | Quiesced recovery set, identity recreation, deletion replay and matched-version rollback |

None of these rows is marked runtime-passed by this report. The implementation gate remains open until the actual fixed-version environment and adapter satisfy the relevant experiments without weakening #465's acceptance criteria.
