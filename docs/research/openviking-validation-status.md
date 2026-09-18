# OpenViking integration validation status

Parent: [#465](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/465)
Gate: [#473](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/473)

## Current result

2026-09-18: **not passed**. No memory runtime feature has been enabled. The
implementation gate remains open until the real upstream service contracts and
host protections have executable evidence.

## Executed host file-tool probe

Baseline: Dano `3dad01dd9d3045eb8cba2f0b91bd8ec538976894`, pi coding-agent
`0.82.1`, Node `22.22.3`, macOS. Dependencies were installed with the frozen
lockfile. The fixture loads the installed package's declared ESM entry.

Run from the checkout:

```sh
node docs/research/fixtures/openviking-host-boundary.mjs "$PWD/apps/dano/package.json"
```

The fixture creates only synthetic data in its own temporary directory and
removes that directory in `finally`. It never reads credentials or existing
user files. A sibling `protected` directory has mode `0700`; its synthetic file
has mode `0600`. The same OS user executes the tools.

| Tool channel | Observed outside-workspace access |
|---|---|
| read using absolute path | Allowed |
| read using a workspace symlink | Allowed |
| write using absolute path | Allowed |
| edit using absolute path | Allowed |

Dano's detached-session assembly directly registers these pi tool definitions.
This experiment invokes those actual definitions, but it is not a full model,
browser, container, Shell or HTTP-channel test. Exit zero means the diagnostic
ran successfully, **not** that isolation passed; inspect its JSON findings.

Conclusion: relocating memory state outside the Runtime Workspace, even with
owner-only permissions, does not protect it from tools running as the same OS
user. Before introducing memory credentials, demonstrate an enforced access
boundary covering file tools, symlinks, Shell and HTTP. Preserve legitimate
project/Skill access; do not rely on prompt instructions or pretend the
Heimdall Bash boundary also wraps native file tools. The raw-tool experiment
bypasses extension hooks; the Linux guard result below narrows this finding.

### Linux with the real Heimdall guard active

On 2026-09-18, `fixtures/heimdall-memory-boundary.mjs` loaded the actual
Heimdall extension and invoked its registered `session_start` and `tool_call`
handlers before executing the real pi read tool. The existing Linux image
`sha256:faa21ab482017c6cb4b08bff7126235e6d88a3350b4223e64960406cb31e0994`
contains bubblewrap, pi `0.82.1` and Heimdall `0.2.17`. The fixture confirmed
the sandbox-active notification and used an explicit deny rule for a synthetic
protected directory.

| Read channel | Guard blocked | Synthetic content exposed |
|---|---|---|
| Absolute protected path | Yes | No |
| Workspace symlink into protected directory | No | Yes |

This establishes a symlink gap in the registered guard path, not an absence
of all native-file protection. It still does not exercise a model turn or
prove Bash sandbox execution. The temporary container and synthetic files
were removed. A separate process identity or a stronger file execution
boundary needs executable evidence before credentials are introduced.

### Separate tool-worker identity feasibility

`fixtures/isolated-memory-tool-worker.mjs` ran in the same disposable Linux
image with a trusted parent and a tool subprocess under UID/GID 65534. The
parent owned a synthetic credential in a mode-0700 directory and passed an
explicit environment allowlist to the child. Real pi read/write/edit tools
failed with permission errors for both absolute and symlink paths. Real pi
Bash could neither read that file nor see the parent's synthetic key variable.
The child still successfully wrote and read its own workspace file. The
parent verified its credential remained unchanged.

A synthetic HTTP authorization endpoint accepted the parent credential and
returned 401 to the child without it. This checks credential withholding in
the prototype only; it does not replace real OpenViking authorization evidence
or prove every reachable HTTP endpoint is safe. The prototype establishes a
feasible OS-enforced file boundary, not a completed Dano/standard-pi process
architecture. Lifecycle integration, legitimate Skill paths, unprivileged
startup, process controls and actual OpenViking access remain to be validated.
All temporary containers and files from this experiment were removed.

## Executed real-server Session contract probe

The unmodified PyPI `openviking==0.4.20` package was installed in an isolated
Python 3.12 environment and served on loopback in explicit `api_key` mode.
`/health` reports version `0.4.20` and healthy. The configured model endpoint is
intentionally unavailable: these results prove only non-model authentication
and Session behavior, not extraction, embeddings, search or recall quality.

```sh
python docs/research/fixtures/openviking-session-contract.py /path/to/isolated/ov.conf
```

The fixture creates random synthetic accounts and USER keys and never prints
keys. It leaves its synthetic data in that isolated server for follow-up tests.
Do not point it at production. On 2026-09-17 it observed:

| Probe | Actual result |
|---|---|
| Concurrent Alice/Bob Session creation | Both successful |
| Alice reads her Session | Successful |
| Bob reads Alice's Session ID | HTTP 404 |
| Bob forges account/user headers to read Alice's Session | HTTP 404 |
| No key reads a Session | HTTP 401 |
| Bob writes to Alice's Session ID | HTTP 200, creates a distinct Bob-owned URI; Alice's owner and message count unchanged |
| Same source ID appended twice | Message count grows from 1 to 2 |
| Active context source metadata | Source ID visible through public context endpoint |
| Rotate Alice's key, then use old key | HTTP 401 |
| Use rotated key to read original Session | Successful with same Alice owner |

The HTTP 200 on Bob's write is **not** alone evidence of cross-user mutation:
the public route auto-creates missing Sessions inside the caller's namespace.
The fixture explicitly verifies distinct URIs, owners and unchanged Alice data.
An adapter must bind references to an owner and not treat a raw Session ID as
globally unique. Full cross-user URI/search/export tests are still pending.

The duplicate append is a verified limitation: `source_message_ids` is not a
server idempotency key. The public context endpoint exposes source metadata,
which is a possible reconciliation input, but completeness across archive,
truncation, response loss, concurrent commits and restart is still unproven.

### Concurrent account, USER and actor-Peer boundary

On 2026-09-18, `fixtures/openviking-peer-boundary.py` created two accounts,
including Alice in each, and Alice/Bob within one account. It wrote synthetic
memory files under Alice's project-a and project-b Peer paths through public
content APIs, then issued six concurrent reads of project-b. The owning Alice
with actor project-b succeeded (200). Alice with actor project-a received 403;
Bob and Bob with forged account/user headers received 403. Alice from the other
account received 404. Explicit project-b search and content replacement under
actor project-a both returned 403, and the original content remained intact.

Omitting the actor header allowed the owning Alice to read project-b (200).
The Peer header selects a view within the USER identity and must therefore be
bound by trusted host code; it is not a separately authenticated project
credential. The fixture proves these public API cases, not the final host's
scope authorization or empty-target handling. Its initial search request
incorrectly supplied a context-only field and received 400; the recorded 403
result came from the corrected valid `find` request.

### Response discarded before adapter receipt, with process restart

On 2026-09-18, `fixtures/openviking-lost-response.py` ran its `prepare`,
`reconcile` and `finish` phases as three separate processes against the real
model service. It persisted an operation/source reference before mutation,
discarded the successful append response body and restarted. Public context
reconciliation found the source ID and metadata confirmed exactly one message;
the message was not resent. It then discarded the commit response body and
restarted again. Filtering public tasks by the dedicated Session found exactly
one task. That task completed and search recalled the new synthetic fact.

This demonstrates a viable reconciliation path for one dedicated Session and
one writer, within task retention. The fixture models response loss at the
adapter boundary, not a TCP failure or server crash. It does not establish
concurrent commit safety, context completeness for long Sessions, task receipt
retention beyond expiry, or durable recovery from server restart. Ambiguous
receipts stop the fixture rather than trigger a blind retry.

### Actual server-process interruption and task recovery

On 2026-09-18, `fixtures/openviking-server-crash.py` prepared a fresh synthetic
account and a real commit. The supervisor verified the loopback listener PID,
its exact isolated config path and the task's pending/running state, then
sent SIGKILL. It restarted the unmodified service using the same data and
configuration. An earlier attempt finished before interruption and was
excluded from crash evidence; the successful injection checked and killed
within one supervisor step.

After restart, the same USER key and Session remained valid. Public task
listing returned one running task, which subsequently completed without any
message or commit retry by the fixture. Search recalled the synthetic report
preference. The source ID was initially visible in context; after completion
it was absent from current context but present in the completed archive
retrieved using the task result's archive URI. This demonstrates real process
crash recovery and archive reconciliation for this single in-flight commit,
not all possible interruption points, task expiry or multi-writer races.

### Multiple local writer processes and abandoned delivery receipt

On 2026-09-18, `fixtures/openviking-multiwriter.py` used a POSIX advisory lock
and durable host-owned receipt file around a real public Session append.
The first worker exited with code 77 after the service accepted the message
and before persisting its delivery receipt. Six independent processes then
raced to retry. The OS released the dead worker's lock; one successor found
the source in public context and persisted the receipt, while the other five
observed that receipt. Session metadata confirmed exactly one message.

The coordinator then persisted revocation under the same lock and deleted the
Session. Six further independent retries all stopped before transport, and
the Session still returned 404. This proves the local single-host locking and
receipt-reconciliation sequence for a dedicated Session with one bounded
message and auto-commit disabled. It does not prove cross-host coordination,
commit concurrency or absence checks over truncated/archived context. Every
writer must participate in the lock protocol; an advisory lock alone cannot
stop a process that bypasses the trusted adapter.

## Executed pi entry-point probe

```sh
node docs/research/fixtures/pi-memory-entrypoints.mjs "$PWD/apps/dano/package.json"
```

The real pi `0.82.1` resource loader successfully loaded both an explicit file
entry and an inline host factory in isolated directories. Each registered
`session_start`, `before_agent_start`, `context`, `turn_end`, `agent_end`,
`session_before_fork`, `session_tree` and `session_shutdown`. Reload left one
extension in each loader rather than duplicating registration. This proves
loading and hook registration only; emitted lifecycle events, stable entry IDs,
actual CLI package installation and model execution remain to be exercised.

### Real model events and Runtime replacement

On 2026-09-18, `fixtures/pi-memory-lifecycle.mjs` exercised the real pi
`0.82.1` AgentSessionRuntime using both a file extension entry and an inline
host factory. Each made a real synthetic model request with tools disabled,
then navigated the session tree, forked at the user entry, reloaded, created a
new Session and disposed the runtime. Both observed `before_agent_start`,
`context`, `turn_end`, `agent_end`, `session_tree`, `session_before_fork`, and
the corresponding startup/shutdown reasons. Fork created a distinct Session
while retaining the source user entry ID; reload retained that ID as well.
No extension error was reported. Temporary configuration copies and session
files were removed in `finally`; model credentials were not printed.

An initial `bindExtensions({})` probe observed shutdown on reload without a
new startup. The installed runtime checks for an actual UI, command, shutdown
or error binding before emitting reload startup. Providing the host's error
listener binding made shutdown/startup pairing pass in both modes. Hosts must
bind runtime services and rebind replacement Sessions; registering hooks alone
is insufficient. This verifies SDK lifecycle behavior, not an installed CLI
package, Dano browser lifecycle, memory extraction hooks or Runtime cwd changes.

### Real CLI local-package installation

On 2026-09-18, `fixtures/pi-memory-cli-install.mjs` invoked the installed pi
CLI, installed a minimal local package through `pi install`, and verified its
registration in isolated agent settings. A print-mode real model request with
tools and session persistence disabled executed all six registered hooks:
startup, before-agent, context, turn-end, agent-end and quit shutdown. The
CLI returned the expected synthetic response. `pi remove` removed that package
registration. Temporary agent configuration, workspace and package files were
cleaned up; the user's normal settings were unchanged. Local paths are stored
relative to the settings directory, so the fixture resolves them before
checking package identity. This proves local package installation/loading,
not registry publication or installation of the final memory extension.

### Installed license metadata

The installed OpenViking `0.4.20` wheel declares `License-Expression: AGPL-3.0`
and contains its LICENSE file. Published `@openviking/sdk@0.1.0` declares
`Apache-2.0`; installed pi coding-agent `0.82.1` declares `MIT`. These are
artifact metadata observations, not a completed distribution review. The
final service image, extension tarball and source/notice delivery still need
to be checked against their actual shipped contents before release.

## Executed real-model extraction and SDK probe

A separate isolated service used the configured OpenAI-compatible chat gateway
and OpenViking's unmodified local `bge-small-zh-v1.5-f16` embedding provider.
The synthetic input asked to remember two preferences: technical plans should
state goals/non-goals, and use Simplified Chinese by default. No real user
conversation was uploaded.

- `POST /sessions/{id}/commit` with `keep_recent_count: 0` returned accepted,
  a task ID and `archived: true`.
- The task progressed pending → running → completed in approximately 40.2 s
  (sampled every 5 s). It reported one memory write, zero skipped operations,
  7,161 prompt tokens and 411 completion tokens.
- A user-scoped `find` returned a memory containing both requested facts.
  The published TypeScript `@openviking/sdk@0.1.0` was separately installed
  and successfully performed `getSession`, `find` and `read`; the read content
  confirmed both facts and the Session retained the same owner.
- Bob's direct memory read, explicitly Alice-targeted search and content write
  all returned HTTP 403; Alice's memory content remained unchanged.

This is one successful synthetic extraction and API recall sample, **not** the
full evaluation set, p95/cost acceptance, automatic Dano model recall or browser
proof. No recall-quality aggregate or billing conclusion is drawn from it.

Reproducible extraction fixture (makes real model calls):

```sh
python docs/research/fixtures/openviking-model-extraction.py /path/to/isolated/run-directory
```

Actual package combination: server `0.4.20`, Python SDK dependency `0.1.11`,
TypeScript SDK `0.1.0`, llama-cpp-python `0.3.35`, httpx `0.28.1`.
The TypeScript package registry integrity is
`sha512-5RI0GTOKAW5+NUky5NW3AwLYXDF10UQBi9TSo6Y6yp0/aq7kNMNnQdtRlkMC4Aa3Cf1kHZs9NGbtF+1cxEHrFA==`.
The downloaded GGUF SHA-256 is
`ab9b81d9cd329c712eee379cf0068eabe6a5e2a01d0def61535eba9384085e2c`.

Python initially rejected the gateway chain whereas local Node verified it.
Exporting Node's actual default trust set (162 roots, including its existing
platform trust additions) to an isolated CA bundle allowed Python's verified
request to reach HTTP 401 without credentials and HTTP 200 with the configured
key. TLS verification remained enabled; no leaf certificate was blindly trusted.
Local llama context creation failed in the Codex sandbox and succeeded with
the same weights outside it; the real-model service used that execution mode.

## Executed correction, selective forgetting and old-source replay

On 2026-09-18, `fixtures/openviking-governance-primitives.py` used the real
model-service sample above and only public APIs. Replacing the goals/non-goals
fact with an acceptance-steps fact updated both direct reads and search while
preserving the Simplified Chinese preference. Removing the corrected fact
preserved that unrelated preference. Deleting the source Session returned
success and subsequent retrieval returned HTTP 404.

The probe then deliberately replayed the original message with its original
`source_message_ids` into a new Session. Real extraction completed and search
returned the forgotten goals/non-goals fact again. Upstream deletion alone
therefore does not prevent an old queue from resurrecting forgotten content.
This is an observed failing baseline, not a passed adapter deletion barrier.
The fixture mutates its synthetic sample; create a fresh extraction sample
before rerunning it. Shared-source handling, in-flight tasks, durable source
revocation and restore-time replay protection remain open gate requirements.

### Durable deletion intent with in-flight extraction

On 2026-09-18, `fixtures/openviking-deletion-barrier.py` ran `prepare`,
`recover` and `verify` as separate processes. It created a fresh synthetic
account and a source containing two preferences, then observed a pending or
running real extraction task before writing the source revocation. The state
file uses an owner-only temporary file, file fsync, atomic replacement and
directory fsync outside OpenViking storage/backups.

After process restart, recovery read that intent, waited for the old task to
complete, removed the goals/non-goals fact through public content replacement
while retaining Simplified Chinese, and deleted the source Session. After a
second restart, the prototype's delivery guard rejected the revoked source
before transport. Direct reads and search omitted the forgotten fact while
retaining the unrelated preference. The source Session returned 404 and its
task list contained only the original task.

This validates the sequence against real extraction for one writer and the
known synthetic fact shape. It does not prove a production queue, concurrent
delivery exclusion, unknown accepted tasks, service crashes, arbitrary semantic
editing or full derived-content enumeration. The replay guard is part of the
prototype, not an upstream API capability; without that guard the earlier raw
replay test demonstrated resurrection. These remaining conditions must be
implemented and tested before the gate can pass.

## Executed export and same-service recovery

On 2026-09-18, `fixtures/openviking-pack-recovery.py` exercised the public APIs
against the synthetic account. USER export produced a valid ZIP (13 entries);
another USER's attempt to export Alice's memory scope returned HTTP 403.
An ADMIN backup with `include_vectors: true` returned HTTP 400, specifically
`Cannot export incomplete OpenViking vector index snapshot`. This failed path
is retained as evidence; a complete vector snapshot was not demonstrated.

The supported backup without vectors succeeded (167 ZIP entries). After the
probe durably recorded a deletion outside that backup, deleted one memory file
and rotated Alice's key, public restore with `vector_mode: recompute` succeeded.
The new key read the restored file with its exact original content; the old
key still returned HTTP 401. Thus this content restore preserved the current
identity store and also brought the deleted file back, as expected from the
old snapshot. Reapplying the separate deletion record removed it from both
direct reads and search results.

This is a same-service, quiescent content-recovery experiment. It does not
prove clean-server identity restoration, atomic backup under concurrent
writes, recomputed-index completeness, full rollback, or absence of all
derived facts after deleting a file. Operational recovery must gate access
until identity reconciliation, durable deletion/revocation replay and index
readiness finish. The fixture rotates the synthetic Alice key and updates
its mode-0600 run state; it never prints key material.

### Separate-instance recovery and index readiness sample

On 2026-09-18 a second unmodified OpenViking `0.4.20` instance started with a
new storage directory and root key on loopback port 19338. The fixture
`fixtures/openviking-clean-recovery.py` recreated the synthetic account and
Alice through public administration APIs and restored the earlier public
backup with vector recomputation. The source-service USER key returned 401
both before and after recovery. The newly issued destination key could read
the restored file and search returned its known acceptance-checklist fact.

`on_conflict: fail` initially returned 409 against the account's automatically
created default directories; `overwrite` was needed even in the new instance.
Applying the independent deletion record then made the target read return 404
and removed both its URI and its known fact from search results. An initial
fixture assumption that a separate checklist memory would survive was false:
inspection of the synthetic ZIP showed all three preferences in one file.
The final checks correctly distinguish pre-deletion recall from post-deletion
absence; this experiment is whole-file deletion, not selective fact editing.

The destination service was stopped after verification. This proves public
content recovery and one real index-readiness sample with recreated identity;
it does not prove restoration of a complete identity/key snapshot, queued
operations, concurrent-write consistency, or production upgrade/rollback.

## Environment observations

- Podman machine is running. `podman images` fails with
  `readlink .../containers/storage/overlay: invalid argument` both inside and
  outside the Codex sandbox. VM storage has approximately 25 GiB free. No
  containers, images or user data were removed to work around it.
- An isolated Python 3.12 virtual environment under `/private/tmp` successfully
  installed and started the unmodified OpenViking `0.4.20` distribution. The
  authentication-only service was stopped after its probes; the separate
  real-model environment supports ongoing governance experiments.

## Remaining gate evidence

- Actual service and SDK version combination, dual pi entry points and event
  lifecycle, license/distribution conditions.
- USER authentication, rotation, concurrent account/user/Peer/Session access.
- Message/commit response-loss reconciliation and usable `ready` state.
- Correction/deletion with shared sources and in-flight extraction.
- Protected host state across all tool channels.
- User export and consistent backup/restore including identity and post-backup
  deletion/revocation records.

Static API findings are tracked separately from executed tests. None of the
remaining items is waived by the successful dependency install or file probe.
