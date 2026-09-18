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

The memory host-service configuration is still outstanding, as is the exact
published extension release required by the worker provider and model-aware
counting contracts. Do not enable production memory from these CLI checks.

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
