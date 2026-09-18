# OpenViking integration validation status

Parent: [#465](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/465)
Gate: [#473](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/473)

## Current result

2026-09-18: **feasibility review passed for the selected Linux profile**;
[PR #480](https://github.com/zhengchengqiaobusiness-arch/Dano/pull/480) merged as
`eab2a9cb6e0d638e564e77cf957a233d531a0952` and #473 is closed. No memory runtime feature
has been enabled. The decision covers the original #473 feasibility gate,
not implementation or completion of #465. The host boundary and delivery
obligations below are mandatory inputs to #474–477.

## Implementation handoff

#474 implementation now lives in the independent
[pi-openviking repository](https://github.com/josephyoung/pi-openviking), with
package name `@josephyoung/pi-openviking`. Version 0.1.0 is now published,
but #474 acceptance is incomplete. The current Dano implementation
branch starts from the merged gate on upstream/main.

Dano's host-only owner registry now persists the stable authenticated user ID
and a deterministic account-scoped SHA-256 mapping. Display-name changes do
not change ownership; anonymous callers cannot create records. Six focused
tests cover restart stability, normalization collisions, concurrent creation,
private permissions and rejection of foreign, exposed or symlink records;
server type checking also passes. This registry is not yet wired into runtime
creation or credential provisioning. Its parent directory must remain inside
the protected host-state root enforced by the eventual launcher.

The management-only provisioning client checks ROOT or account-bound ADMIN
identity before registration, resolves an existing exact user before creating
one, and verifies the returned credential through authenticated `/health` as
the exact account/user with USER role. Lost registration responses trigger a
read reconciliation, never an automatic POST replay or key rotation. Existing
hashed keys without a recoverable plaintext credential require explicit
recovery. Transport redirects are rejected and upstream error bodies are not
propagated. Nine targeted tests and server type checking pass. A real local
OpenViking 0.4.20 run created Alice and Bob in synthetic account
`provision-c965eeac55e3`; a recreated client retrieved Alice's unchanged key,
and the two users had distinct keys with verified ownership/roles. This client
handles management transport only; storage and startup composition follow below.

The protected credential store and identity startup service now compose these
pieces: USER keys are encrypted with AES-256-GCM, with account/user and key
version authenticated as associated data. Reopening validates private file
permissions, metadata and ciphertext before returning a key. Startup verifies
the saved key without management access, shares concurrent initialization,
requires live tool isolation before credential access and before handoff, and
withholds the connection if durable persistence fails. Corrupt records or
changed encryption configuration are not treated as missing credentials.
All four identity modules have 26 focused passing tests and pass server type
checking. Runtime/session wiring and Linux end-to-end isolation remain open;
these unit tests do not prove the final Dano process boundary.

Independent extension commit `a242afe` adds an unreleased protected
`toolProviderModule` interface so Dano can preserve its tool policies inside
the worker. Its 41 tests and real fixed-image Linux runs of both default and
custom tool providers pass: UID/no_new_privs, private absolute/symlink access
denial, no inherited credential, workspace I/O, streaming and cancellation.
The interface is not in Dano's currently pinned npm 0.1.0 artifact. A separate
Bubblewrap probe verified compatibility with no_new_privs using Dano's
existing bound `/dev` and read-only `/proc` settings; fresh devpts mounting
failed. This guides the upcoming Heimdall adapter, not proof it is complete.
The two test containers and all seven layers created by this run were removed;
the pre-existing base image was retained. Podman's general image listing
reported a storage readlink error, but exact-ID cleanup succeeded after a
separate container inventory confirmed no references. No shared-storage repair
or broad prune was performed.

Dano now builds a separate `dist/server/bridge/heimdall-worker-tools.js` entry
for this provider interface. It loads the installed Heimdall 0.2.17 through
pi 0.85.1's public extension runner with an in-memory credential store,
project trust disabled, and executable resource discovery disabled. Native file
tools run through its actual `tool_call` and `tool_result` handlers; model Bash
and interactive Shell use its actual sandbox operations. Guard errors stop
execution. The worker uses Dano's headless UI context because an active Heimdall
sandbox renders a status with the theme API during initialization.

Four new tests cover workspace I/O, `.env`/configuration protection, result
filtering, untrusted extension suppression, cancellation before writes, closed
providers and no unsandboxed Shell fallback. These and 14 existing Heimdall and
session tests pass; server type checking and server build also pass.

The actual built provider passed a disposable Linux/Node 22.23.2 run with the
unreleased extension commit `a242afe`: distinct host/worker UIDs, kernel
`NoNewPrivs: 1`, denied private absolute/symlink read/write/edit, absent inherited
synthetic credential, successful file I/O, model Bash and interactive Shell,
streaming, preserved exit code, `.env`/configuration rejection, and cancellation
verified by absence of delayed file writes. This run used no network and the
existing Compose capability/seccomp settings. It did not run Dano's server or
prove its final multi-user launcher, provider-Python capability, or browser flow.

Reproduction fixtures are `fixtures/heimdall-isolated-worker.mjs` and
`fixtures/heimdall-worker-provider.mjs`. Copy them into the extension installation
as `scripts/linux-worker.mjs` and `scripts/provider.mjs`, copy Dano's server build
to `dano-server`, and install the exact Heimdall peer. In the disposable root
container, run `node scripts/linux-worker.mjs 1000 1000 10001 10001
/app/memory-extension/scripts/provider.mjs`. IDs are fixture arguments, not
production defaults. The fixture explicitly enables the sandbox and grants the
canonical workspace write access: because the generic worker sets HOME to its
workspace, Heimdall's automatically added absolute HOME read rule otherwise
overrides the relative `.` write rule. The eventual trusted Dano launcher must
provision this canonical policy and the required bubblewrap environment; the
factory does not rewrite user policy or bypass guards. Protected configuration
remains inaccessible. Cleanup of worker-owned files can require the container's
root; removing the disposable container supplies that cleanup boundary.
The final container inventory is empty, and all 18 image layers created by
these fixture iterations were removed by exact ID; the pre-existing base was
retained.

The host runtime now accepts a `ProtectedSessionTools` profile supplied from
the server's User Context. The same profile reaches the initial backend,
new/resumed sessions, different-workspace resolution and forks. Each runtime
resolves and checks its workspace worker before enabling the protected entry;
native file and Bash definitions become the published extension's worker
proxies. Host Heimdall is omitted in this mode because the actual guards run
inside the worker. The existing provider-Python wrapper remains around the
proxied model Bash tool. User Shell hooks supply isolated operations; Dano's
browser RPC still rejects direct Bash execution commands.

Protected resource loading disables workspace extension discovery/project
trust and retains explicitly supplied trusted Skills. A per-runtime abort
signal invalidates old tool references when sessions are replaced or disposed.
The ordinary mode retains its existing behavior when no protected profile is
supplied. The profile resolver remains a trusted server seam: the future
launcher must validate ownership, protected agent/Skill paths and live kernel
isolation; it is not a browser configuration object.

Real pi runtime tests verify model-triggered Bash routing, file and interactive
Shell routing, no workspace extension execution, initial/new/resumed/forked
bindings, stale tool rejection, foreign workspace rejection, and trusted Skill
reload without duplicate tool registration. These tests use an executor double
to prove routing/lifecycle, not kernel isolation. A separate two-user registry
test verifies distinct profile binding from server User Context. The latest
full regression passes **111 files / 1442 tests**, with one existing skipped
test; full type/Svelte checks and production build pass. Multi-user process
allocation, provider-Python file permissions across UIDs, actual memory factory
composition and browser acceptance still remain before this profile can be
enabled for the final Dano integration.

The protected profile now requires a trusted `providerPythonModuleDirectory`
when a credential broker is present. Provider Python can use these installed
read-only modules without creating a host-owned `0700` directory in the worker
workspace. It validates absolute paths and regular module files, never deletes
the shared installation, and retains the ordinary temporary-copy mode outside
the protected profile. Actual Python/HTTP tests cover concurrent separate
login capabilities against shared read-only modules, unchanged module content,
failure cleanup and rejection of missing/relative module paths without fallback.
The 39 provider/runtime tests, server type check and server build pass. This
does not yet prove cross-UID output-file redaction or the final worker mount.

A real disposable Linux probe also established a required process-information
boundary: without procfs restrictions, another UID can read a synthetic Shell
capability from `/proc/<pid>/cmdline`, including through a filesystem symlink.
`fixtures/linux-proc-isolation.cjs` verifies that remounting the container's
procfs with `hidepid=2,gid=<trusted-host-group>` denies both cross-worker paths
while retaining same-UID access and trusted-host inspection. Worker groups must
be distinct and must never include the exempt host group; otherwise this
protection is bypassed. Host inspection is needed for live worker identity
checks. Run only in a disposable root container, supplying host and two distinct
worker identities as the last three arguments (the fixture uses no real keys).
The probe used no network, its container was removed, and no image was created.
The final multi-user launcher must establish and verify this boundary before
loading credentials; the existing single-host worker feasibility test alone
does not prove process-information isolation.

`linux-process-privacy.ts` now provides the startup preparation and live worker
check. The root launcher must call preparation inside a dedicated mount
namespace before loading credentials. It remounts procfs if necessary, checks
all procfs aliases, and uses a credential-free `setpriv` child to prove the
configured host identity can inspect its root parent. The tool provider checks
kernel metadata before initialization and on tool execution: consistent
non-root identities, no foreign supplementary groups, zero permitted/effective/
ambient capabilities, and `NoNewPrivs`. It also verifies that the worker cannot
read its actual host parent's process status. Closing the provider during an
asynchronous check prevents subsequent tool execution.

The runtime check deliberately uses access tests rather than comparing raw
procfs exemption GIDs with process-local GIDs. In the tested rootless Podman
namespace, mounting with group 1000 reported `gid=100999` in mountinfo. The
[kernel procfs documentation](https://docs.kernel.org/filesystems/proc.html)
describes the group exemption, and [user_namespaces(7)](https://man7.org/linux/man-pages/man7/user_namespaces.7.html)
describes identity mappings. This observed mapping was not hardcoded.

The actual built privacy module passed `fixtures/linux-process-privacy-artifact.cjs`
in a disposable Linux container: root was rejected, setup was idempotent, a
distinct nonprivileged worker passed, the exempt group and missing NoNewPrivs
were rejected, removing hidepid invalidated a subsequent check, and restoring
the policy passed again. Parser/guard/lifecycle tests pass; their development-host
Heimdall tests mock only the kernel check and do not claim Linux isolation.

The updated full Heimdall fixture also passed with the new built privacy check
enabled. It prepares procfs before privilege drop and uses distinct host and
worker groups; the workspace is owned by the host with the worker's group
allowed to access it. Workspace I/O, model/interactive Shell, streaming,
cancellation and private-path denial still pass with live process privacy
checks. The runtime Dockerfile explicitly installs `mount` and `util-linux`
for the startup helper's commands. The 15 focused tests, server type check and server build pass. All test
containers and the eight image layers created by this iteration were removed;
the pre-existing base image remains. This is executable launcher preparation
and worker protection, not the still-outstanding multi-user supervisor or Dano
browser acceptance.

With the published dependency installed, the host entry and native lock binding
load successfully. Full type/Svelte checks report zero diagnostics and the
production build passes. The full regression rerun passes 109 test files and
1431 tests (one existing skipped test). The first run exposed a delayed dialog
scroll-lock cleanup after happy-dom teardown; the lightbox fixture now awaits
unmount and the actual scroll-lock release before teardown, and both its
focused rerun and the full rerun pass without unhandled errors.

At extension commit `fb7f2c7`, 38 automated tests pass: owner-bound private file
state, eight concurrent processes, killed-writer recovery, durable source
deduplication, response-loss reconciliation, concurrent delivery, pause,
reconfirmation after enable, bounded recall and real pi 0.85.1 loading/reloading
of both entry modules. No second pi kernel is bundled.

The protected Linux CLI now invokes pi's public `main` with the standard
extension factory after directory/code validation, worker startup and host
UID/GID drop. A real model turn invoked Bash to write a synthetic file and
read to retrieve it; filesystem ownership confirmed the separate tool UID.
A planted workspace extension was not evaluated, and print mode exited
normally. `no_new_privs` is verified from kernel metadata. Trusted host modules
and optional Skills must remain in the protected installation; administrator
profiles and ancestors cannot be group/other-writable. This run deliberately
kept memory disabled; the separate enabled consent/save/recall run is recorded below.

The standard pi CLI/RPC entry has now passed the real OpenViking path: default
consent off, explicit confirmation, model-triggered save, background `ready`,
content/source inspection, new-session recall and pause. Automatic collection
remained unapproved, and captured RPC events/stderr contained no USER key. A
fresh-account rerun passed on pi 0.85.1 after the earlier 0.82.1 run. The
[extension acceptance record](https://github.com/josephyoung/pi-openviking/blob/fb7f2c7/docs/acceptance-2026-09-18.md)
records both operations and tokenizer parity evidence. Interactive TUI evidence,
Dano browser acceptance and product dual-user integration remain outstanding.

The release candidate now pins pi 0.85.1: 0.82.1's bundled shrinkwrap retained
vulnerable dependencies despite root overrides. The new exact install resolves
undici 8.9.0 / brace-expansion 5.0.9 and currently has zero npm audit findings.
The fixed-image Linux worker regression passed again. Dano's pi-ai and
pi-coding-agent dependencies are now aligned to this same release. The root
product version is 0.2.28. `pnpm run check` reports no diagnostics, the full
build passes, and the final Vitest run with two workers reports 105 files /
1405 tests passing and one existing skipped test. The local Python test PATH
uses the isolated environment containing httpx. The Pi-owned default-model
fixture now refreshes the complete availability snapshot before initial
selection, while retaining its original default-model assertions.

The 0.1.0 npm tarball includes both entry points, the CLI and Apache-2.0 text;
its 33 files contain no bundled pi kernel, config credentials or test state.
The user completed npm second-factor approval and publication succeeded.
Registry metadata confirms version 0.1.0, and the downloaded registry tarball
matches the inspected candidate byte digest. Dano now pins that exact registry
version and integrity in its package manifest and pnpm lockfile. The native
file-locking dependency compiles locally; the image build stage now includes
its compiler prerequisites, with clean-image validation still pending.
Package digest:
`sha512-VFsiYHDA5lLJjz6nciIoJI/eR3QYQ3VlHQ6z59HdZqQB1lf+i59mQVMIHqaHUpZ5j7QJAUmdTxzrB6YDvpWvpQ==`.

The actual extension adapter also saved a synthetic preference through real
OpenViking 0.4.20 and SDK 0.1.0: the source-bearing archive, completed matching
task, memory diff, current content and successful retrieval established `ready`
in 22.8 seconds. Every delivery step recreated the adapter from durable state.
This single run is functional evidence, not the PRD latency/quality benchmark.

The owner-level background scheduler now persists backoff and attempt counts,
recovers queued work on startup, bounds per-tick work and shutdown waits, and
reports exhausted reconciliation as blocked without repeating a mutation.
A fresh actual-service background run reached `ready` in 30.3 seconds; a
subsequent query retrieved the preference without foreground delivery calls.
These are functional samples, not completion of the release benchmark.

The resource-profile helper disables project trust before package resolution,
disables automatic extension discovery, and retains explicitly supplied trusted
Skills. Real pi reload tests show that a workspace extension is suppressed;
the same fixture executes when project trust is enabled as a positive control.
The launcher must apply this profile and protect all supplied installation and
Skill paths. `noExtensions` alone does not skip package resolution.

The extension's actual IPC worker also passed an isolated Linux/Node 22.23.2
run with pi 0.82.1. It streamed tool updates and cancelled a long Bash command;
workspace read/write succeeded, while absolute and symlink read/write/edit
against the trusted host's private credential failed. The worker did not
inherit the synthetic memory key. Its UID is checked against Linux process
metadata. The test container had no network and was removed. The worker
primitive and ordinary CLI path are implemented; this does not yet prove the full memory-enabled startup profile or
the absence of all host-executable resource discovery paths.

The standard entry now registers isolated proxies for read/write/edit/bash/
grep/find/ls and routes interactive `!`/`!!` Shell commands through that same
worker. The Linux run exercised the proxies, streamed output and preserved exit
codes. Cancellation was checked by the absence of delayed file writes for both
model Bash and interactive Shell, not only by observing a rejected promise.

Standard-entry management includes confirmed enable, pause, status and saved
content viewing. These handlers are tested, but the final interactive CLI
launcher flow has not yet passed acceptance. Automatic collection stays off.
Before first data access, the real service's authenticated `/health` identity
must match the configured account/user and USER role; missing identity or an
administrator key is rejected. Actual-service checks verified saved-content
reading and rejection of wrong credential binding and foreign references.

Remaining #474 gates include the multi-user Dano Linux worker launcher,
runtime/session integration, authenticated settings/status UI, interactive
ordinary-pi evidence and in-app Browser flows. Publication and exact dependency
installation are now evidenced above; they do not complete these integration
gates. #465 and #474–477 remain open.

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

### Final boundary feasibility follow-up

The updated worker prototype cleared supplementary groups and dropped the
trusted host to UID/GID 1000 after spawning the separate tool worker. It
repeated all file/environment/workspace checks and connected to real
OpenViking 0.4.20: trusted authenticated request 200, worker without key 401.
A VM-loopback SSH reverse forward reached the macOS-loopback service; no fake
HTTP endpoint substituted for it. Temporary container, tunnel and connection
files were removed. The [selected host boundary](openviking-host-boundary-decision.md)
defines the supported Linux profile and concrete standard-pi/Dano obligations.

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

### Simultaneous commit requests

On 2026-09-18, `fixtures/openviking-commit-race.py` released six simultaneous
HTTP commit requests for a fresh Session containing one synthetic source.
One returned `accepted` with a task ID; five returned HTTP 200 with
`skipped/no_messages`. Public task listing contained exactly one extraction
task. It completed, its public archive contained the source ID, and search
recalled the synthetic preference. Thus neither HTTP 200 nor a skipped reply
is an operation-specific ready receipt; the adapter must reconcile the
accepted task and archive. This sample does not cover interleaving new message
appends with commit or multiple server instances writing the same storage.

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
`Apache-2.0`; installed pi coding-agent `0.82.1` declares `MIT`. The
[distribution review](openviking-distribution-review.md) selects separate
unmodified server delivery, a published HTTP SDK dependency and pi peer
compatibility, with explicit source/notice release actions. Actual image and
tarball contents must still be checked before publication.

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
The official `openviking-0.4.20-cp310-abi3-macosx_14_0_arm64.whl` was
downloaded from its PyPI release metadata and verified as SHA-256
`f3ca10af7bb93f69e00b21e188d2d0c2b8d1e49d505da3fbd9a29265b2735d7d`.
All 1,365 installed distribution files outside installer metadata matched that
wheel byte-for-byte, confirming the tested server was not privately patched.
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

### Public derived-content and export audit

On 2026-09-18, `fixtures/openviking-derived-content-audit.py` traversed the
deletion-barrier account's USER tree through paginated public per-directory
listing. It visited nine directories and read all 18 enumerable files,
including 15 hidden summary/overview files. No file contained the forgotten
goals/non-goals literal; the separate Simplified Chinese fact remained in the
memory body. A USER export ZIP passed integrity checks; all 20 UTF-8 entries
were scanned, decoding JSON escapes, with no forgotten literal found.

This strengthens the earlier search-only sample with public derived-file and
export coverage. It does not prove absence of every semantic paraphrase or
inspect private service storage. Executed behavior and required adapter
sequencing are mapped in [the executed contract](openviking-executed-contract.md).

### In-flight correction follow-up

Running `openviking-deletion-barrier.py` with the `correction` argument repeated
the three-process experiment while an old real extraction was pending/running.
Recovery drained that task, replaced the old goals/non-goals fact with the
requirement to list risk mitigations, retained Simplified Chinese and removed
the old source Session. After another process restart, the new fact remained
in direct reads/search, the old fact was absent, unrelated language preference
remained, and the revoked old source could not submit another extraction.
This closes the tested old-extraction/new-correction sequencing gap for the
synthetic sample; a generic semantic editor is later implementation work.

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

### Stopped full-state snapshot and later revocations

On 2026-09-18 the supervisor confirmed the isolated source service process
had exited before copying its entire run directory to a private snapshot.
All 630 regular data files matched SHA-256 between source and copy. Host
operation-state files were copied with the run directory. The source resumed
on port 19337; an independent snapshot instance started on loopback 19339.
No private database records were edited; only the copy's workspace/port config
changed. This is an offline filesystem backup, distinct from USER export and
the public content-only backup endpoint.

`fixtures/openviking-snapshot-recovery.py` verified that the original USER key
worked in both instances, and the same completed task and source archive were
present. It then deleted a synthetic memory and rotated its user's key in the
source after the snapshot. As expected, the old snapshot still accepted the
old key and exposed the old file. Reapplying the external account/user
revocation and deletion record through public APIs invalidated that key in
the copy and removed the file and fact from direct reads/search. The copy was
stopped after verification.

The experiment proves an offline identity/content/completed-task recovery
path and the need for post-backup revocation replay. It does not prove a live
atomic snapshot, recovery of a pending queue from that snapshot, every crash
point in replay, or production upgrade/rollback. The source root task listing
contained two terminal records; that list is not evidence that all historical
account tasks were enumerated. The stopped-process copy supplies the snapshot
consistency boundary.

## Reproducibility fixes after gate review

Secret-bearing fixture state now uses a shared atomic writer that creates
mode-0600 files before writing, including when replacing a preexisting
mode-0644 file. A permissive-umask experiment verified both cases. Clean
recovery derives its expected content and query from the selected actual ZIP
member, removing a hidden dependency on the earlier checklist-message probe.
The checked-in `fixtures/openviking-offline-snapshot.py` provides explicit
listener/PID validation, optional graceful stop, process-exit verification,
copying, a per-file digest manifest and destination config generation. Running
it against the stopped research snapshot verified all 655 copied run files.
The worker supervisor now registers child output/exit listeners immediately
after spawning, before awaiting network requests; its Linux probe passed again.

## Environment observations

- Podman machine is running. `podman images` fails with
  `readlink .../containers/storage/overlay: invalid argument` both inside and
  outside the Codex sandbox. VM storage has approximately 25 GiB free. No
  containers, images or user data were removed to work around it.
- An isolated Python 3.12 virtual environment under `/private/tmp` successfully
  installed and started the unmodified OpenViking `0.4.20` distribution. The
  authentication-only service was stopped after its probes; the separate
  real-model environment supports ongoing governance experiments.

## #473 acceptance audit and handoff

| Original gate requirement | Evidence and outcome |
|---|---|
| Dual pi entries, events, stable sources, Runtime replacement, versions/licenses | Real model SDK/CLI lifecycle, fork IDs, fixed package metadata and distribution review; passed |
| USER registration/rotation and account/user/Session/Peer isolation | Real concurrent matrix plus direct read/write/search/export and forged identity rejection; passed |
| Lost/partial responses and restart reconciliation; actual ready evidence | Lost append/commit responses, client exit, six writers, six commits and real server interruption; completed task/archive/content/recall chain; passed |
| Correction/deletion, shared facts, old in-flight work and source suppression | Three-process correction/deletion, source revocation, public hidden-file/export audit; passed for feasibility samples |
| Enforced credential/state boundary across file, symlink, Shell, environment and HTTP | Selected Linux host/worker profile, bootstrap privilege drop, real OpenViking authenticated 200 versus worker 401, real native tools denied; passed |
| Export versus consistent disaster recovery and later revocations | Public content recovery plus stopped full-state copy, original identity/task/archive recovery and post-snapshot deletion/key revocation replay; passed |
| Executable operation contract | [Executed contract](openviking-executed-contract.md) links principals, requests, pre/postconditions, retry rules and fixtures; passed |

Separate Standards and Spec review found no remaining blocking issues after
fixes. Secret-file creation, backup-derived expectations, reproducible snapshot
supervision and a child-exit listener race were corrected. A nonblocking helper
duplication suggestion remains. Final artifact publication, complete adapter
state machines, generic semantic editing, launcher/IPC integration and the
full #465 browser/evaluation/deployment audit remain #474–477 deliverables.
No test result here substitutes for those later acceptance requirements.
