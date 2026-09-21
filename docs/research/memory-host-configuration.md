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
- Request deadline, maximum displayed content bytes and policy version.
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
links. Runtime service construction and protected-host startup wiring now use
the published and pinned extension `0.1.3`. The supervisor's optional
`memoryConfigDirectory` supplies only a private path; the non-root host reads
the configuration, constructs owner/credential/provisioning services and starts
the explicitly configured tokenizer workers. It composes these services with
authenticated user runtimes; anonymous users receive no memory extension.
The user runtime now drains the active delivery tick after settling its network
client before releasing the worker. There is no local drain timeout that could
be mistaken for completed persistence. Supervisor-level forced termination must
still be treated as crash recovery, not a successful drain.
Startup does not turn memory on, does not create remote identities and does not establish a
production deployment or completed #465 acceptance.

## Optional automatic collection

Add `collection` only when the protected deployment has a configured selector
model. Omitting it preserves explicit memory without offering a new automatic
grant. Configuration does not grant user consent: both main memory and the
separate automatic switch still default off. Changing `collection.policyVersion`
requires a new user grant. Keep that version tied to the reviewed selection
policy, including any future trusted task-fact adapters.

Example collection block (budgets are explicit administrator choices):

```json
{
  "collection": {
    "policyVersion": "collection-v1",
    "lifecycleTimeoutMs": 5000,
    "model": {
      "provider": "xiaomi-token-plan-cn",
      "id": "mimo-v2.5",
      "maxTokens": 2048,
      "temperature": 0,
      "thinking": "disabled"
    },
    "selector": { "maxInputBytes": 16384, "maxFacts": 5, "timeoutMs": 45000 },
    "scheduler": {
      "pollIntervalMs": 500, "mergeWindowMs": 1000, "maxWaitMs": 5000,
      "workTimeoutMs": 50000, "leaseMs": 110000,
      "initialBackoffMs": 1000, "maxBackoffMs": 5000,
      "maxAttempts": 3, "maxRequestsPerBatch": 10
    }
  }
}
```

The protected host lazily uses the same deployment `getAgentDir()` model/auth
configuration as protected chat, after normal Dano startup has resolved it.
Per-user resources, model parameters and browser requests cannot replace these
paths. Model catalog network refresh is disabled for this selector initialization.
The selection call contains one quoted-data message, the extension's fixed
selection prompt, and no tools. Only text from a completed response is returned;
partial, error or tool-call responses fail with fixed error codes. Optional
`thinking` only changes that provider request field; arbitrary payload overrides
cannot replace messages, model identity, limits or tool declarations.

Input screening includes the configured management/encryption secrets, the
currently resolved model API key or credential-bearing auth headers, and the
current owner's stored USER key. The owner runtime verifies worker isolation
before screening and inference. No remote identity is provisioned merely for
screening. Runtime stop cancels/settles collection before delivery and worker
teardown. Model/provider errors never become browser-visible configuration.

### Confirmed provider task facts

`collection.taskFacts` opts specific provider response contracts into collection.
Keep this configuration in the protected memory file, never a Skill or user
workspace. Without an approved contract, provider results remain excluded.
Add or change a contract only with a new `collection.policyVersion`, so existing
consent is invalidated before the new collector starts.

The following is a **synthetic contract**, not a shipped OA route. Replace it
only after verifying the provider's actual response semantics:

```json
{
  "taskFacts": {
    "maxResponseBytes": 65536,
    "maxFactBytes": 4096,
    "contracts": [{
      "id": "confirmed-report",
      "method": "POST",
      "path": "/reports/submit",
      "success": { "path": ["code"], "equals": 0 },
      "actorPath": ["data", "owner"],
      "fields": [{ "label": "report", "path": ["data", "reference"], "type": "string" }]
    }]
  }
}
```

Contracts match an exact method and pathname; query strings are not projected.
Ambiguous/encoded routes, duplicate routes, executable adapters and arbitrary
object fields are rejected. Field paths are arrays of own-property names;
selected values must match `string`, finite `number`, or `boolean`. Missing,
wrong-type and oversized results produce no candidate. HTTP 2xx is necessary
but insufficient: the configured success field must also match exactly.

`actorPath` identifies the immutable OA/OAuth subject of the business result's
owner. It must map to the authenticated Dano user through the same canonical
subject mapping used at login. It is not a username, credential or request-body
assertion. Results for other people are excluded even when the caller is allowed
to view them. Do not configure a result contract without a verifiable actor.

Both direct `provider_request` and provider HTTP calls intercepted during
Python/bash execution use the initiating login's trusted send evidence. The
host projects allowlisted fields from the actual response; it never extracts
facts from bash stdout. A domain-separated HMAC receipt binds the minimal
projection to the memory owner, tool name/call, policy, grant revision and epoch.
Only these receipts are persisted with the original pi tool result. The
collector verifies them again after restart/fork and after permission changes;
forged worker metadata, failed tool results and stale receipts are excluded.
Raw response bodies and credentials are not copied into a collection receipt.
The extension's existing secret screening still runs before any selected task
fact is sent to the selection model. Ordinary business requests remain usable
when optional memory is unconfigured, revoked, timed out or unavailable.
