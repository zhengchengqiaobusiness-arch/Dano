# Remote execution and release decisions

Use the existing deployment helpers and Compose contract. The transport below
adds checksum verification, private output and inherited locking; the caller's
reviewed script remains responsible for staging and acceptance.

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

`mutation` and `acceptance` executions acquire the production lock on fd 9 and
export `DANO_DEPLOY_LOCK_FD=9` for existing helpers. Reuse that descriptor inside
the payload. Keep the same worker alive through Browser disposition and cleanup;
coordinate its bounded wait using a private, candidate-bound on-host decision
file. Validate its target SHA/image ID before applying any decision. Record a
pending Browser phase before waiting. Do not finish a switch script and release
its lock before Browser acceptance.

The runner has **no rollback trap**. CLI exit 0 means a valid execution receipt
was obtained: check `exitCode` and `disposition`, not the wrapper exit alone.

| Receipt | Next action |
| --- | --- |
| `exitCode=0`, `passed` | Verify the step's actual completion criterion. |
| Failed diagnostic, `retry-diagnostic` | Keep release state; inspect capabilities and retry a bounded, corrected read-only check. |
| Failed mutation/acceptance, `stop-and-classify` | Stop dependent mutations, inspect actual state and apply the skill's acceptance/rollback rules. |
| CLI exit 2, `incomplete` | Execution is unconfirmed. Inspect/reacquire the lock and reconcile container/config state before another mutation. |

An inventory typo, unsupported option, read-only fetch timeout, evidence-export
failure or transport failure is not itself a release regression. An unknown
required acceptance failure remains incomplete: retry safely, then use Phase 8's
explicit disposition. Confirmed regression still requires immediate rollback.
Use EXIT/signal traps for scoped cleanup and recording interruption; decide
rollback explicitly from phase, health and acceptance evidence.

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
Track `prepared`, `switched`, `machine-passed`, `browser-passed`, `accepted` or
`rolled-back`; write a transition only after its evidence passes. Record
`readiness_timestamp` after real healthy/smoke checks, never before a wait.

Immediately before each switch, revalidate upstream once via a successful fetch
or authenticated GitHub commit query and record the channel/time/SHA. A remote
fetch connectivity failure can use a verified local fetch/API channel, provided
server HEAD, clean build context and OCI revision still prove the same target.
Keep upstream checks outside traffic mutation and acceptance completion logic.

Reuse completed PR-specific proof only for the identical candidate and unaffected
session/material scope. After a container restart, repeat machine smoke and the
current-process Browser text/bash/image baseline plus relevant persistence checks.
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

Before the no-cache build, measure bounded connectivity/transfer to public
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
