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

At this checkpoint, automatic approval review prevented the image build from
launching because of an account usage limit. This temporary build blocker was
subsequently resolved: the images and real-model checks documented below include
the SYSTEM.md/settings fixes. OAuth, rendered browser and the remaining release
gates are still outstanding.

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

## Published CLI metadata and scoped cleanup (2026-09-20)

GitHub Actions run `35336512204` reports successful publication of `0.1.1`.
Although its npm log warns that the CLI bin was invalid and removed, direct
registry metadata still maps `pi-openviking` to `dist/cli.js`. The downloaded
published tarball also contains the bin mapping (`./dist/cli.js`) and the target
file with a Node shebang. This inspection resolves the suspected missing-entry
metadata defect; it does not replace an installed CLI execution check. Both
`pi-package` and `pi-extension` are present in registry keywords.

After confirming there were no containers and matching the recorded image IDs,
the four superseded acceptance tags (`acceptance-20260920`,
`acceptance-db2e1c4d`, `acceptance-worker-pool`, `acceptance-d816b948`) were
removed successfully. A subsequent inspect confirms the current
`acceptance-offline-store` image still resolves to
`ef36dec85897d4c7170b581a94ad2a2439b93efbf777a43a630059fef1698ada`.
No shared storage repair or global prune was performed.

## MiMo tokenizer preparation (2026-09-21)

The requested browser model is now `mimo-v2.5`. Official public tokenizer assets
from `XiaomiMiMo/MiMo-V2.5` were pinned to revision
`63651580ca774f8504f676040460aed3e1244ac1`:

- `tokenizer.json`: 7,033,572 bytes, SHA-256
  `633518aad78f9f61bae2ae420d621215754a4424c918b052cd8c22a3b59e99d2`.
- `tokenizer_config.json`: 15,167 bytes, SHA-256
  `fd34b805f75a890a5c123d79a2982bbe240b3b6efb156d22401bd619484d9bd2`.

A network-disabled disposable container using the retained `ef36dec8...` image
loaded these assets through the actual built `MemoryTokenizers` service. Empty,
Chinese, English, mixed symbol/emoji and XML-like quoted text yielded counts
`0, 10, 11, 18, 9`, matching Python Hugging Face tokenizers 0.23.2 on the same
assets with special tokens disabled. This proves asset/runtime compatibility
for those samples, not API model identity or provider billing-token equivalence.
Artifacts are under `/private/tmp/dano465-browser-nibutlhc/tokenizer`, with the
probe script at the run root. The disposable container exited zero and was
removed. No credentials were involved.

The initial credential transfer was rejected by automatic approval review.
The user then explicitly authorized persistence under `~/tmp/`. The allowlisted
OAuth/MiMo configuration was saved to
`/Users/joseph/tmp/dano465-acceptance-secrets/production-input.json`, with a
0700 directory and 0600 files. This directory is excluded from temporary
acceptance cleanup. Production services were not changed.

## Isolated Compose startup and MiMo probes (2026-09-21)

Project `dano465-browser-nibutlhc` now runs the retained protected image, nginx
and a fixed-destination OpenViking TCP relay. The scoped config/data volumes are
`dano465-browser-nibutlhc-config` and `dano465-browser-nibutlhc-data`; runtime
workspaces use Linux volumes. Mac-to-VM forwarding exposes only
`localhost:18710` and `localhost:18711`. The relay binds the VM's private
interface and is reached from the rootless app via `host.containers.internal`;
it forwards only to the existing VM-loopback OpenViking tunnel.

Startup exposed two local configuration errors: the generated isolated OAuth
encryption key initially used the wrong encoding, and a VM-host relay cannot
bind an address belonging to the separate rootless bridge namespace. The key
now uses unpadded base64url for exactly 32 random bytes. The relay address and
app route were verified independently. After app recreation, nginx's upstream
resolution was reloaded to clear the observed 502 response.

HTTPS smoke then passed homepage, health, anonymous cookie, client creation,
SSE connection, command response and disconnect. Evidence:
`/private/tmp/dano465-browser-nibutlhc/smoke.log`. Inspection of the actual
`protected-host-entry.js` process shows UID/GID 1000, zero effective capabilities
and `NoNewPrivs: 1`; an unrelated `podman exec` process is not this evidence.

A real `mimo-v2.5` API image probe identified the synthetic red circle, blue
square and yellow triangle (HTTP 200, 5.28 seconds). A tool-call probe returned
the expected function and exact fact after the prompt explicitly delimited
that fact; the initial ambiguous prompt included the trailing instruction in
its argument. These probes do not execute a Dano tool or prove browser upload.
Evidence: `mimo-vision-result.json` and `mimo-tools-result.json` in the same run
root.

The in-app Browser failed to open the HTTPS origin with
`ERR_CERT_AUTHORITY_INVALID`. Certificate trust was handed off to the user
under the browser policy; no certificate validation was disabled. OAuth login,
rendered chat/image/bash, authenticated memory and screenshots remain unproven.
The stack is retained while this handoff is pending.

## Reused persistent localhost certificate (2026-09-21)

The certificate blocker was caused by generating a fresh temporary self-signed
leaf instead of using the machine's existing trusted local CA. The persistent
assets already exist under `~/.local/share/dano/localhost-tls/`: `localhost.pem`,
`localhost-key.pem`, and `rootCA.pem`. System SSL verification succeeds for
localhost. The existing leaf expires September 11, 2027; its CA expires
September 8, 2036. The isolated nginx now mounts that existing leaf/key.

A fresh in-app Browser tab opened `https://localhost:18711` and rendered Dano
without a certificate warning. No new trust installation or certificate-check
bypass was needed. The previous request for user certificate trust is withdrawn.
AGENTS.md now requires reuse and retention of these persistent assets, and
renewing leaves under the same CA. This resolves TLS navigation only; OAuth
and the memory/browser flow remain separate acceptance gates.

## Actual MiMo browser image and bash acceptance (2026-09-21)

The persisted model configuration initially supplied the environment variable
name as a literal API key. Pi 0.85.1 requires `$XIAOMI_TOKEN_PLAN_CN_API_KEY`
for environment resolution. Correcting the persisted and mounted configuration
resolved the observed browser authentication error; the real configured auth
key was compared to the process environment without logging either value.

The in-app Browser then received `DANO465_MIMO_BROWSER_OK` from MiMo V2.5.
A real upload of the fixed synthetic `shapes.png` produced the correct ordered
description: red circle, blue square, yellow triangle. A subsequent model turn
actually invoked bash with `ls`; the rendered executed-command card and reply
showed `uploads`. Screenshots are `mimo-image-browser.png` and
`mimo-bash-browser.png` under `/private/tmp/dano465-browser-nibutlhc`.

A direct bwrap bind/write/read/ls preflight used the active broker's actual
workspace, Linux xfs mount, and worker UID 10002 and exited zero. The initial
fixture unnecessarily requested fresh proc/devpts mounts, which the container
denied. The minimal fixture uses only the workspace, read-only executable
libraries and `/dev/null`; no application sandbox policy, capabilities or
Compose security settings were relaxed. The actual model-triggered bash turn
separately proves the shipped integration.

Opening Long-term Memory in that anonymous session displayed the login-required
message and exposed no enable/save controls. Its screenshot is
`anonymous-memory-browser.png` in the same run directory.

These browser turns use the existing anonymous session. OA autofill was visible
in a screenshot and login was submitted, but OA presented a slider CAPTCHA.
Action-time confirmation for that challenge remains pending; no CAPTCHA was
bypassed. Authenticated OAuth callback and authenticated memory save/read/recall
remain unproven. The isolated stack and both browser tabs are retained for
continued acceptance. Persistent credentials and trusted TLS assets remain
excluded from cleanup.
