# Protected supervisor HTTP development evidence

Date: 2026-09-18. Dano source: `0d406040`.

The compiled `runProtectedSupervisor` → `protected-host-entry` → `runDanoMain`
path was exercised in a disposable Linux Podman container. The fixture is
[protected-supervisor-http.mjs](fixtures/protected-supervisor-http.mjs).

## Observed results

Both the graceful-stop and `--crash-host` runs exited successfully:

- The real HTTP server became healthy and accepted two authenticated synthetic
  users through `POST /api/clients` (201 responses).
- `/proc` evidence identified the HTTP host at UID 1000 and separate live
  workers at UIDs 10001 and 10002.
- A second launcher against the same identity pool failed with
  `SUPERVISOR_ALREADY_RUNNING`.
- Graceful shutdown returned exit code 0. Killing the HTTP host with SIGKILL
  produced supervisor exit code 1, as expected.
- After either exit, no worker UID processes, worker brokers, or protected HTTP
  host processes remained. The container used `--init` to reap orphan exits.

The first attempt rejected the fixture configuration because `productName` was
missing. Supplying the synthetic product name resolved that configuration error;
no runtime code change was needed.

## Scope and limitations

This is a development contract check, not the Compose/browser/model release
acceptance. It uses synthetic JWT identities, no model calls, and a container
with networking disabled and no published host ports. Port 18710 is used only
inside the container. The test requires the same temporary capability envelope
used by the earlier worker isolation fixtures (`--cap-add ALL` and
`seccomp=unconfined`); it does not prove a minimal deployment capability profile.

The extension fixture is the unreleased independent source `c9305bb`, which
exports protected worker provider API version 1. The published npm 0.1.0 package
lacks that capability and is intentionally rejected by this integration. This
result does not satisfy the actual patch publication/exact dependency gate.

The runtime is created under container-local `/tmp`, outside the checkout and
without a macOS bind mount. Both test containers were automatically removed.
The temporary source image and its three-layer chain were removed, preserving
the reusable dependency base `9582c21a2198`.

Local evidence logs:

- `/private/tmp/dano474-supervisor-http-build.log`
- `/private/tmp/dano474-supervisor-http-run.log`
- `/private/tmp/dano474-supervisor-http-crash.log`

Still required: shipped launcher/Compose wiring, real published dependency,
OpenViking memory composition, real model and in-app Browser flows, lifecycle
management, and the remaining issue #465 acceptance and release gates.
