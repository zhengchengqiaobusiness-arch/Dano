# Private memory service configuration

`readMemoryHostConfig(privateConfigDirectory)` reads only `memory-service.json`
from a canonical, host-owned directory with no group/other permissions. The
file must be a host-owned, single-link regular file with no group/other
permissions, at most 1 MiB. Symlinks, shared permissions, invalid JSON and
invalid settings produce a generic error without configuration values.
Only a missing file in an otherwise valid private directory means unconfigured.

Keep this configuration directory separate from the runtime data, identity
registry, user workspaces and session roots. In particular, do not pre-populate
a new supervisor's host-state root with configuration: fresh worker identity
initialization checks that associated data roots are empty. The configuration
path belongs in an administrator launch profile, not in model parameters.

The version-1 schema requires all of the following; there are no secret or
policy defaults:

- OpenViking origin, account ID and management key.
- A 32-byte credential encryption key encoded as 64 hexadecimal characters,
  plus its key version. Retain the key/version when restoring encrypted USER
  credentials; do not regenerate them on each startup.
- Request/shutdown deadlines, maximum displayed content bytes and policy version.
- Save payload limit, recall timeout, token budget, result count and score threshold.
- Scheduler polling, backoff, retry and per-tick operation limits.
- Tokenizer asset/input limits, startup deadline and one or more explicit model
  bindings to local tokenizer/config paths and SHA-256 hashes.

Unknown fields, duplicate model bindings, malformed keys, unsafe asset paths,
nonpositive limits and inverted retry bounds are rejected. Tokenizer functions,
environment overrides and arbitrary module paths cannot be provided in JSON.
The parsing result contains secrets and must remain confined to the trusted
HTTP host; never serialize it into argv, worker RPC, browser state or logs.

## Current scope

The private reader and parser have automated coverage for normal reads,
configuration absence, corrupt input, permission violations, symlinks and hard
links. Runtime service construction and protected-host startup wiring are still
pending the model-aware extension release. This configuration module does not
turn memory on, does not create remote identities and does not establish a
production deployment or completed #465 acceptance.
