# Remote execution and release decisions

Use the existing deployment helpers and Compose contract. The transport below
adds checksum verification, private output and inherited locking; the caller's
reviewed script remains responsible for staging and machine checks.

## Prepare a file, then execute it

Save the complete script outside the checkout. Use normal file tools to review
it. Execute it through the local Node runtime:

```sh
node .agents/skills/dano-production-deploy/scripts/run-remote-script.mjs diagnostic /absolute/path/inventory.sh
node .agents/skills/dano-production-deploy/scripts/run-remote-script.mjs mutation /absolute/path/release.sh
```

The runner uses the skill's exact BatchMode SSH identity/host through pipes. It
checks the same script bytes locally with `bash -n`, uploads them into a private
directory, verifies SHA-256 and remote Bash syntax, and executes the file with
stdin closed. Ordinary child stdout/stderr stay on-host. It emits an `uploaded`
receipt immediately and a `completed` receipt after execution. Retain these
receipts across context compaction; the directory is a cleanup ownership record.

Both connections use `ConnectTimeout=15`, `ServerAliveInterval=15` and
`ServerAliveCountMax=3`. OpenSSH detects a lost connection without imposing a
short runtime limit on a healthy machine operation. Upload additionally has
a 30-second local bound. An interrupted execution is unconfirmed: the remote
worker may still hold the lock, so inspect its receipt and ownership before
resuming. Bound individual machine commands and health waits inside the payload.

`mutation` executions acquire the production lock on fd 9 and export
`DANO_DEPLOY_LOCK_FD=9` for existing helpers. Hold the same worker through image
preflight, switch, machine health/smoke, rollback if necessary, receipt and
cleanup. Do not wait for a Browser decision. After machine success, release the
lock and ask about optional feature acceptance, defaulting to no acceptance.
The `acceptance` runner kind is retained for explicit maintenance operations;
a Browser run does not need to hold the deployment lock.

The runner has **no rollback trap**. CLI exit 0 means a valid execution receipt
was obtained: check `exitCode` and `disposition`, not the wrapper exit alone.

| Receipt | Next action |
| --- | --- |
| `exitCode=0`, `passed` | Verify the step's actual completion criterion. |
| Failed diagnostic, `retry-diagnostic` | Keep release state; inspect capabilities and retry a bounded, corrected read-only check. |
| Failed mutation/acceptance, `stop-and-classify` | Stop dependent mutations, inspect actual state and apply the skill's acceptance/rollback rules. |
| CLI exit 2, `incomplete` | Execution is unconfirmed. Inspect/reacquire the lock and reconcile container/config state before another mutation. |

A failed diagnostic or transport interruption is not itself a product regression.
Inspect the candidate-bound machine receipt and live lock/state before resuming.
A failed health/smoke check triggers the recorded rollback. Optional acceptance,
a browser timeout or an unanswered question does not roll back a smoke-passed
release. A later confirmed product regression is a separate recovery operation.

## Capability and input handling

Run `scripts/probe-host.sh` from this skill as the diagnostic payload before
building. Read its known boolean-only output entirely on-host. It neither opens
runtime config nor starts containers. Missing optional features select a fallback:

| Capability | Execution |
| --- | --- |
| `gitC=false` | Change the subprocess working directory; use plain `git`. |
| `gitShowCurrent=false` | Use `git symbolic-ref --short HEAD`; handle detached HEAD separately. |
| `node=false` | Run public helpers in the already-present Dano Node image with narrowly scoped mounts. |
| `buildxFormat=false` | Use the supported `du --verbose` output; parse fields on-host. |
| `composeInteractiveFlag=true` | One-off checks use `run -T --interactive=false ... </dev/null`. |
| `composeInteractiveFlag=false` | Use `run -T ... </dev/null`; the file-based runner already closes stdin. |

Require Bash, SHA-256 verification, Docker/Compose and flock for the release.
Use `LC_ALL=C LANG=C PYTHONIOENCODING=utf-8`; preserve product-name bytes exactly
from executable JSON. Resolve containers by inventoried Compose project/service
labels, require an unambiguous match and retain the resolved ID. Container names
are inventory data. Close stdin for commands that do not consume explicit input;
JSON-filter pipelines consume only their producer's pipe.

## Checkpoints and scope

Persist a private on-host release record with `targetSha`, immutable image ID,
previous image, approved Compose files, candidate criteria and phase receipts.
Track `prepared`, `switched`, `machine-passed`, `deployed` or
`rolled-back`; write a transition only after its evidence passes. Record
`readiness_timestamp` after real healthy/smoke checks, never before a wait.

Immediately before each switch, revalidate upstream once via a successful fetch
or authenticated GitHub commit query and record the channel/time/SHA. A remote
fetch connectivity failure can use a verified local fetch/API channel, provided
server HEAD, clean build context and OCI revision still prove the same target.
Keep upstream checks outside traffic mutation and acceptance completion logic.

Reuse completed PR-specific proof only for the identical candidate and unaffected
session/material scope. After a container restart, repeat machine smoke. Browser checks belong only
to a separately requested feature acceptance.
After Browser-control reconnection alone, retain completed evidence and resume
the unfinished interaction in the same in-app tab. A changed candidate invalidates
candidate-specific evidence.

Compare inventory by resource responsibility:

- Dano image/mount/config/secret/TLS changes: reconcile under the lock before
  switching; stop dependent work on unexplained differences.
- Shared nginx/network changes: inspect route/config diff, preserve the external
  change, then repeat affected projection/hash/route gates under the lock.
- Explained additions outside Dano's affected resources: retain them and update
  the preservation baseline. Never silently whitelist an unknown change.
- Already-stopped neighboring services: preserve their recorded baseline; any
  new degradation of a running neighbor blocks the update.

## Build budget and cleanup

Before a dependency build, measure bounded connectivity/transfer to public
indexes/artifacts from the Dockerfile's supported mirrors. Record durations and
failure classes only; apply supported build arguments without source edits or
TLS bypass. Budget space for the verified context, extraction, image/layers,
failed-attempt caches, other jobs and the retained rollback image. Check capacity
between attempts; stop only this run's verified process when an attempt stalls.

Reuse this run's completed candidate after checking its provenance instead of
rebuilding it solely to recreate a lost receipt. Cache markers or unchanged web
asset hashes alone do not prove stale code. Enumerate expected assets from the
candidate and compare the serving page against that inventory.

Register every temporary directory/cache artifact before use. Export only
allowlisted receipts before cleanup. Remove the runner's exact private directory,
including script/raw logs, after disposition and evidence export; retain it on
uncertain execution until reconciliation. Keep referenced uploads for their
documented lifecycle. Record final disk usage and container preservation.

## Diagnose once, then decide

Use one scoped app/nginx log summary for the switch/readiness windows. The
filter classifies timeout configuration, nginx buffering and anonymous sweep
partial failures without returning messages. Known maintenance failures remain
visible counts: compare the recorded baseline and changed code, state their
impact and disposition, and retain unrelated user data. A recognized reason is
not an automatic pass.

For an unknown incident, perform one on-host structured extraction of error
class/code, stage and timing; add a reusable classifier when its exact public
format is established. Repeated keyword-guess SSH round trips are not a useful
diagnostic strategy. Never export raw exceptions, configuration or session data.

Record stage start/end and selected path in the existing release receipt. Use a
60-second transport probe, a 120-second no-progress dependency-download window
and one attempt per unchanged route as default work budgets. Measurable download
progress can continue within the reviewed build deadline. An exhausted budget
selects the documented alternative or reports the specific blocker; it never
waives a machine safety gate. Poll phase changes rather than unchanged
logs. Reconnection reuses completed evidence for the same candidate; a changed
candidate repeats its machine checks; optional feature acceptance remains separate.
