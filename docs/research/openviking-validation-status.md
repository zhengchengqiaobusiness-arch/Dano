# OpenViking integration validation status

Parent: [#465](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/465)
Gate: [#473](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/473)

## Current result

2026-09-17: **not passed**. No memory runtime feature has been enabled. The
implementation gate remains open until the real upstream service contracts and
host protections have executable evidence.

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
Heimdall Bash boundary also wraps native file tools.

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

## Environment observations

- Podman machine is running. `podman images` fails with
  `readlink .../containers/storage/overlay: invalid argument` both inside and
  outside the Codex sandbox. VM storage has approximately 25 GiB free. No
  containers, images or user data were removed to work around it.
- An isolated Python 3.12 virtual environment under `/private/tmp` successfully
  installed and started the unmodified OpenViking `0.4.20` distribution. The
  authentication-only service was stopped after its probes; the separate
  real-model environment supports ongoing governance experiments.

## Remaining gate evidence

- Actual service and SDK version combination, dual pi entry points and event
  lifecycle, license/distribution conditions.
- USER authentication, rotation, concurrent account/user/Peer/Session access.
- Message/commit response-loss reconciliation and usable `ready` state.
- Correction/deletion with shared sources and in-flight extraction.
- Protected host state across all tool channels.
- User export and consistent backup/restore including identity and post-backup
  deletion/revocation records.

Static API findings are tracked separately from executed tests. None of the
remaining items is waived by the successful dependency install or file probe.
