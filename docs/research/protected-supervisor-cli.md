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

Host configuration and credentials remain in the trusted launch environment or
private host storage. They are not written into the supervisor JSON or copied
into the worker environment. SIGINT and SIGTERM abort the supervisor operation;
its existing shutdown path stops the HTTP host and reclaims worker processes.
The normal `start` command is unchanged; this entry does not switch an existing
deployment or bypass memory release gates.

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
