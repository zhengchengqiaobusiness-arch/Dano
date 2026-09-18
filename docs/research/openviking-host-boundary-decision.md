# #473 selected host boundary

Decision date: 2026-09-18. Target: the independent pi package and Dano factory
entry, using the same protected adapter contract.

## Supported execution profile

Select a Linux process-isolated profile for memory-enabled operation. The
installation launcher starts a trusted host and a distinct tool worker, then
drops bootstrap privileges. The tested prototype uses host UID 1000 and worker
UID 65534 with a shared workspace group; these numeric IDs are fixture inputs,
not application constants. Production installation must allocate/configure
nonconflicting identities and verify ownership before startup.

The trusted host owns pi lifecycle, user/scope mapping, memory SDK access,
credentials and durable delivery/correction/revocation records. Its private
directory is owner-only and lies outside the worker workspace. The worker
executes model-accessible native file and Shell operations under its own UID
and an explicit environment allowlist. The shared workspace remains writable.
Arbitrary user-supplied URIs/credentials never reach the memory client.

For ordinary pi, the standard package entry runs inside this launcher profile;
for Dano, the host factory receives the same protected services. The package
installer alone does not confer isolation. On macOS the supported protected
profile is a Linux container/VM, not same-UID host tools. If the protected
worker is unavailable, memory activation fails closed rather than silently
using ordinary same-process tools.

## Integration invariants for #474

- Route all model-reachable arbitrary file/Shell operations through the worker.
  Host-side UI/control/memory tools may remain trusted only when their inputs
  do not expose arbitrary filesystem, process or authenticated HTTP access.
- Disable executable extension discovery from tool-writable workspace paths.
  The host's extension package, configuration and service sockets belong to
  protected installation/state directories. Worker file writes must not become
  trusted code after reload, fork or Runtime replacement.
- Preserve existing workspace/Skill functionality through configured read-only
  Skill mounts and shared workspace access. Do not give the worker memory keys,
  root credentials, privileged control sockets or a host-environment fallback.
- Keep the same protection when registering replacement tools and rebinding
  runtimes. IPC permits only the intended tool operations and validates results;
  it is not an arbitrary host-execution endpoint.

These are implementation obligations of the chosen feasible boundary, not
claims that Dano's current runtime already enforces them.

## Executed evidence

`fixtures/isolated-memory-tool-worker.mjs` bootstrapped as root, cleared
supplementary groups, spawned the distinct worker and dropped the trusted
process to UID/GID 1000. Against real OpenViking 0.4.20, the trusted host's
USER credential received HTTP 200 and the worker's uncredentialed request
received 401. The credential remained unchanged. Absolute and symlink
read/write/edit operations failed with permission errors; actual pi Bash
could not read the protected file or see its credential environment variable.
The worker successfully read and wrote its own workspace.

The real service remained on macOS loopback. A temporary SSH reverse forward
bound only to Podman VM loopback connected the test container to that service;
it did not emulate OpenViking or alter authentication. The forward, test
container and temporary connection files were removed after validation. The
same kernel boundary passed an earlier local synthetic HTTP test, but that
test alone was not used as real-service evidence.

This resolves the feasibility decision for the selected Linux profile.
Production launcher/IPC integration and real browser acceptance remain #474–477
work; this document does not claim support for an unprotected native macOS
execution profile or all third-party extensions.
