# Protected supervisor CLI

The built Linux root entry is:

```sh
node /path/to/dano/dist/server/protected-main.js /etc/dano/supervisor.json
```

The repository also exposes `pnpm start:protected /etc/dano/supervisor.json`.
Run inside the dedicated container/mount namespace required by the supervisor.
The configuration file and its canonical ancestors must be root-owned and not
writable by group or others. The launcher reads at most 1 MiB of JSON, validates
all configuration fields, and never logs its contents on failure.

The JSON shape is `ProtectedSupervisorOptions`: separate runtime, session,
host-state and identity roots; an explicit non-reused UID/GID allocation range;
worker count and RPC/resource limits; fixed installation and privilege-guard
paths; and the non-root host profile. There are no implicit default identities
or paths. The executable search path must contain only absolute canonical
entries. Environment or credential fields in the profile are rejected. The
existing root supervisor validates installation ownership, path separation,
NSS collisions and procfs privacy before starting the host.

`maxWorkers` bounds resident worker processes, not the number of users who have
ever connected. At capacity, the supervisor closes the least recently used
idle process before starting a replacement. An in-flight tool operation pins
its process until completion. Logical IPC tokens retain their owner/workspace
and reacquire a verified process when needed; eviction never replays a command.
User state, consent and memory delivery remain in the HTTP host and continue
independently of worker residency. Failed cleanup prevents replacement.

Host configuration and credentials remain in the trusted launch environment or
private host storage. They are not written into the supervisor JSON or copied
into the worker environment. SIGINT and SIGTERM abort the supervisor operation;
its existing shutdown path stops the HTTP host and reclaims worker processes.
The normal `start` command is unchanged; this entry does not switch an existing
deployment or bypass memory release gates.

Protected sessions keep their resource/settings directory separate from model
configuration. Unless a model runtime is explicitly supplied, the HTTP host
loads `models.json` and `auth.json` from its deployment agent directory
(`getAgentDir()`, including `PI_CODING_AGENT_DIR`). Provision that directory as
host-private storage. Credentials are not copied into per-user agent directories
or tool workers. The startup/reload regression checks model discovery and
configured authentication without making a provider request; real model calls
remain a separate acceptance requirement.

## Validation scope

Configuration tests cover complete input, nested secret/environment rejection,
missing limits, inconsistent host IDs and relative/noncanonical paths. The
Linux fixture `fixtures/protected-supervisor-http.mjs --cli` exercises the
actual built command through a root-owned file, real HTTP requests and two
worker identities. `--cli --crash-host` exercises unexpected HTTP-host death.
These are process and HTTP contract checks, not browser/model acceptance.

The optional top-level `memoryConfigDirectory` names a pre-provisioned canonical
directory owned by the HTTP host UID/GID with mode 0700. It must be separate from
the installation, runtime, session, identity and host-state roots. Only this path
goes into the supervisor profile; put the private `memory-service.json` inside
that directory with mode 0600. The supervisor derives the service-state directory
as `hostStateRoot/memory-service`; callers cannot inject it into the host profile.

After dropping privileges, the host reads and validates the private configuration,
starts the configured tokenizer workers and composes owner-bound memory with the
supervised tool profiles. Startup does not provision remote users or enable
memory. Missing configuration leaves memory unavailable; invalid configuration
fails startup. Runtime disposal drains user deliveries before shared tokenizers
close. These paths now use the published and exactly pinned extension `0.1.1`.
Do not enable production memory from these CLI checks.

### Actual Linux result — 2026-09-18

The newly built entry completed both fixture modes in image
`8676dcca98e653fe726cfc3362d092d3436c7888ac6c6761c1ce2fd1234e505f`.
The container used no network and published no ports. Both runs proved a
non-root HTTP host, two separate worker identities and exclusive-supervisor
locking. Normal SIGTERM shutdown returned zero; forced host death returned one.
Both modes found no remaining HTTP host, broker or worker processes.

The image used the existing development extension fixture, not a new npm
release. These process-lifecycle results do not establish published dependency
compatibility, browser behavior or model/memory acceptance. The test containers
were disposable; production deployment was not changed.

### Memory-enabled startup result — 2026-09-18

Image `d386be62f0e3ec47f9de861eafdae761bcb901628dc1913486e53fe2f4f3d56c`
contains the installed npm `0.1.1` extension, the current built server and
`@huggingface/tokenizers@0.2.0`. Both `--cli --memory` and
`--cli --memory --crash-host` passed with no network or published ports.
The fixture starts real tokenizer workers using synthetic fixed assets and
points OpenViking at an unavailable loopback endpoint. Both authenticated users
start with memory and automatic collection disabled. Enabling/pausing Alice
does not enable Bob; Bob's request for Alice's settings returns 403. The actual
host runs without root privileges and tools use separate UIDs. Graceful and
forced-host shutdown both reclaim host/broker/worker children.

This demonstrates private configuration and local settings wiring without a
remote service. It does not demonstrate save/recall, real model tokenization,
browser behavior or the #474 end-to-end acceptance gate.

The associated type check and server build pass. Full regression passes all
132 test files: 1549 tests passed, one skipped. The two test containers, test
image and its four new layers were removed; the reusable base image remains.

### Repository Dockerfile image result — 2026-09-20

The actual repository Dockerfile `protected-runtime` target built successfully
as image `ce5fda72f0f76e1c387f2457637591d2072b855a052496c9027780543a2e0daf`.
Image inspection confirms the root supervisor entrypoint
`node ./dist/server/protected-main.js`, with no inherited command arguments.
An ephemeral container with networking disabled successfully imported the
published extension's `host` and `bootstrap` exports. Its installed package is
`@josephyoung/pi-openviking@0.1.1` and includes both `pi-package` and
`pi-extension` keywords. The built supervisor entry file exists.

The selected deployment Dockerfile regression passed (one test; 60 unrelated
tests skipped). The image is retained for subsequent isolated acceptance.
These checks establish image construction and module loading only; they do
not establish Compose provisioning, model calls, real OpenViking delivery or
browser acceptance. The default Dockerfile target still inherits the ordinary
non-root runtime entrypoint.

The lifecycle fixture can also target this repository image layout using
`DANO_FIXTURE_INSTALLATION=/app` and `DANO_FIXTURE_SERVER=/app/dist/server`.
Mount `fixtures/protected-supervisor-http.mjs` read-only and invoke it with
`node /fixture.mjs --cli --memory` in a disposable, network-disabled container
with the supervisor's required capabilities. No host ports or credential mounts
are needed for this synthetic contract check.

On the same image above, this check passed for both graceful shutdown and
forced HTTP-host death (`--crash-host`): real HTTP
startup, two distinct worker UIDs, exclusive supervisor locking, default-off
memory settings, per-user enable/pause isolation, cross-user HTTP 403 and child
process reclamation. Both ephemeral containers were removed. This image predates the host-model configuration fix in
`f3031b46`, so it is not evidence for that fix. Rebuilding the newer source
completed application compilation but failed fetching the open-websearch Skill
from GitHub with `gnutls_handshake() failed: An unexpected TLS packet was
received`. The newer image and real model/browser gates remain unverified.

### Updated image and regression — 2026-09-20

After providing a temporary proxy forward bound only to Podman VM loopback,
the full repository Dockerfile build succeeded as
`8b016bdf2ac77ccef5ba0e8feab56fac2f2533d59022ab86847d3fec08ee24f8`.
This image includes the host-model configuration fix. Inspection confirms its
root supervisor entrypoint. The actual `--cli --memory` fixture passed on this
image: non-root HTTP host, separate worker UIDs, exclusive supervisor lock,
default-off/per-user memory settings and graceful shutdown reclaiming children.
The fixture container was automatically removed. This run did not exercise
forced-host death on the updated image or real model/browser flows.

Current regression covers all 132 test files: 1550 tests passed, one skipped.
The first run had nine failures: six Python tests lacked `httpx`, two deployment
tests inherited the externally supplied Pi directory instead of their own
runtime paths, and one collector test timed out. Revalidation used the isolated
Python environment with httpx and limited concurrency to four workers. The 131
non-deployment files passed (1489 tests, one skipped) with an empty private Pi
directory; `deploy-compose.test.ts` passed separately (61 tests) without an
inherited `PI_CODING_AGENT_DIR`. No product behavior or timeout was changed to
make these checks pass. Compose overlay configuration was separately validated
with synthetic values; actual Compose/browser and release gates remain open.

### Real Dano HTTP/SSE memory flow — 2026-09-20

The updated image above passed `--cli --memory --real-service` using the real
OpenViking server and model gateway. This mode reads private fixture inputs
from `DANO_FIXTURE_MEMORY_CONFIG` and `DANO_FIXTURE_MODELS`; an explicit
`NODE_EXTRA_CA_CERTS` path supplies the existing trusted CA bundle. The model
configuration is copied into the HTTP host's private agent directory, outside
both users' tool workspaces. The service uses a fresh test account's ADMIN key
for provisioning, with a fresh encryption key for its per-user credentials.

`fixtures/protected-memory-http-flow.mjs` drives Dano's actual HTTP command and
SSE event protocol: select the configured model, ask it to save the synthetic
report-title/language preference, poll the operation until `ready`, verify
source IDs/time and actual content, reject Bob's read of Alice's content with
HTTP 403, create a new session and verify the model recalls both facts. The
surrounding fixture verifies default-off settings, separate unapproved automatic
collection, per-user settings, pause and complete process reclamation. The
container completed successfully and was automatically removed.

This is a supplemental real-model/service integration test with synthetic JWT
identities. It did not perform OAuth login, rendered browser interaction,
Compose deployment, image upload or quantitative quality/cost acceptance.
Those gates remain required before #474/#465 can close.

## Search startup validation (2026-09-20)

The image built from `db2e1c4d` passed the disposable Linux
`protected-supervisor-http.mjs --cli --memory` fixture. It verified the real
search daemon and HTTP host run under the host UID, two separate worker UIDs,
memory settings isolation and graceful shutdown without remaining children.
The log is `/private/tmp/dano465-search-startup.log`. This run did not yet verify
the subsequent process-group cleanup or idle-worker eviction changes. Their
new image build and abnormal-exit checks remain pending; browser/model gates
are not implied by this lifecycle fixture.

## Idle capacity and abnormal-exit validation (2026-09-20)

Image `238a04213dda7ea8766d6667e3371ff8e07cb29ca1bb4e91b2515b7d1114cad5`
contains the worker-pool and process-group cleanup changes. Three disposable
Linux runs of `protected-supervisor-http.mjs --cli --memory` passed:

- `--worker-capacity`: two retained clients plus six sequentially connected and
  disconnected users, with at most two resident worker identities. Existing
  users' memory settings remained accessible and isolated.
- `--crash-host`: SIGKILL of the HTTP host produced exit code 1 and no remaining
  host, search daemon, broker or worker processes.
- `--crash-search`: SIGKILL of the search daemon stopped Dano with exit code 1
  and reclaimed the same process set.

Logs: `/private/tmp/dano465-worker-capacity.log`,
`/private/tmp/dano465-crash-host.log`, `/private/tmp/dano465-crash-search.log`.
All three containers used `--rm`. These are real process/HTTP checks with
synthetic JWTs and offline memory configuration, not model, OAuth or browser
acceptance. Old-token reacquisition and no command replay are separately
covered by the supervisor RPC tests. The later SYSTEM.md fix is not in this
image and requires its own updated-image validation.

## Latest configuration regression (2026-09-20)

After the deployment SYSTEM.md/settings fixes through `b0f135ba`, the first
parallel regression run passed 1,502 tests and skipped one, but the anonymous
release-gate fixture failed its fixed 150ms idle-cleanup observation. Its six
tests passed when rerun alone. The fixture now waits up to three seconds for
actual cleanup while checking that the active SSE user's resources remain;
production cleanup timing and behavior were not changed.

The same four-worker regression then passed all 132 files: 1,503 tests passed,
one skipped (`/private/tmp/dano465-observed-full-regression.log`). The separately
run deployment suite passed 61 tests with `PI_CODING_AGENT_DIR` unset
(`/private/tmp/dano465-latest-deploy-regression.log`). Thus 1,564 distinct tests
passed and one remained skipped. Type checking and the 12 targeted protected/
detached-session tests passed before this fixture-only adjustment.

The latest image build was not executed: automatic approval review reported an
account usage limit before launching Podman. The verified image above therefore
still excludes the SYSTEM.md/settings fixes. Updated-image, OAuth, rendered
browser, model and remaining release gates are outstanding.

## Memory-only initialization failure (2026-09-20)

Image `b2cc3dc54f6f99c622be70698f7436b423e4cea078848e381166e3d659288ea2`
completed `--cli --memory --real-service --memory-failure`. After host startup,
the fixture writes a malformed identity record for Alice in the disposable
host state. Alice's client creation succeeds, her memory settings return 503,
Bob's settings remain available, and Alice completes a real model turn through
HTTP/SSE. The corrupted record remains byte-for-byte unchanged. Shutdown
reclaims the host, search and worker processes. Evidence:
`/private/tmp/dano465-memory-failure.log`. This proves the memory-only failure
boundary with a real model; synthetic JWTs still do not prove OAuth/browser
acceptance.

## Deployment dependency store reuse (2026-09-20)

The network retry built the image above, but also confirmed that deployment
packaging used an empty store instead of installation's `/tmp/pnpm-store`.
A disposable `--network none` probe successfully ran pnpm 9.15.9 deployment
with that existing store and `--offline`, reusing 385 packages with zero
downloads. Dockerfile now uses the same options. The full resulting image is
`ef36dec85897d4c7170b581a94ad2a2439b93efbf777a43a630059fef1698ada`;
its build log confirms zero downloads during deployment packaging.
Logs: `/private/tmp/dano465-deploy-store-probe.log` and
`/private/tmp/dano474-protected-offline-store-build.log`.

## Fresh-user real-service regression and review (2026-09-20)

The first capacity-plus-real-service run timed out waiting for ready. A reduced
two-user run reached `failed` with the host-only code
`MEMORY_NO_EXTRACTED_FACT`. Both reused the same remote account/user identities
and the same fact from earlier successful runs, while discarding local state.
The fixture now assigns fresh authenticated Dano identities for each real run,
so prior remote facts cannot suppress extraction or satisfy recall assertions.
It also fails immediately on a terminal failed receipt instead of polling it
until the ready timeout. Production delivery behavior was not changed.

On image `ef36dec85897d4c7170b581a94ad2a2439b93efbf777a43a630059fef1698ada`,
both fresh-user runs passed: ordinary two-user save/ready/source/content/foreign
denial/new-session recall, then the original capacity scenario with six extra
sequential users and two worker slots. Both reclaimed all child processes.
Logs: `/private/tmp/dano465-fresh-isolated.log` and
`/private/tmp/dano465-fresh-worker-capacity.log`. Temporary diagnostic logging
was removed after verification. These remain synthetic-JWT HTTP/SSE checks.

Independent review of fixes through `0b612c18b`:

- **Standards:** original missing-search-daemon finding resolved; zero new
  proven hard violations. Duplicated ordinary/protected search supervision is
  a maintainability observation, not a new release failure.
- **Spec:** original memory-failure/chat and worker-capacity findings resolved;
  zero new deterministic findings. Active operations, failed cleanup and old
  logical-token reacquisition were reviewed.

Neither review substitutes for the remaining browser/OAuth/Compose gates.
The configured gateway lists only `qwen35`; an actual synthetic image request
returned HTTP 400 with `qwen35 is not a multimodal model`. A vision-capable
configuration and the real OAuth/OA configuration are still needed for the
required browser image and authenticated-memory acceptance. No credentials or
real user images were included in these diagnostic artifacts.
