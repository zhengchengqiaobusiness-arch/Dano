# Python Skills with the current OA login

A Dano Login Session owns its Provider Credential. During each Heimdall `bash`
execution Dano provides a local Python client, bound to the Login Session that
started the Assistant Turn. Tokens remain in the Credential Broker.

```python
import json
from dano_provider import request, ProviderError

try:
    response = request("GET", "/admin-api/bpm/task/todo-page?pageNo=1&pageSize=1")
    page = json.loads(response["body"])
except ProviderError as error:
    print(error.code)
```

`request(method, path, headers=None, body=None, timeout=15.0)` returns the existing Broker
response envelope (`ok`, HTTP `status`, sanitized `headers`, text `body`). For JSON
writes, pass an object as `body`; the Broker serializes it. Relative paths retain
query strings. The configured provider origin and Authorization header are
server-owned. OA HTTP/business failures must be handled by the caller; they are
not always login failures. Broker authentication and validation errors raise
`ProviderError` with its stable `code`.

For this OA integration, configure `DANO_OAUTH_API_ORIGIN` to the OA page's
origin. The authorization server's token and identity endpoints may use a
different hostname; they do not determine the business API origin. Do not
replace the Skill's original business hostname with the token endpoint hostname
merely because both resolve to the same IP address.

Dano prepends `dano_provider` to PYTHONPATH only while a bash call runs, preserving
existing module directories. The client retains a configurable 15-second socket
timeout; disconnecting cancels the corresponding Broker request. The client
uses a loopback HTTP listener with a random 256-bit execution capability. The
capability is not an OA credential and cannot select an identity. The listener
accepts at most 1 MiB per request and does not accept requests without that
capability. The client ignores HTTP proxy environment variables and follows no
redirects. No public listener, global user-token environment, or token export
endpoint is introduced.

This transport uses Node HTTP and Python urllib rather than a custom wire protocol
or an additional RPC framework. A Unix-domain socket would avoid a TCP listener,
but requires a custom urllib connection adapter and a socket mount through the
existing sandbox. Loopback HTTP works with the shipped shared-network sandbox
and both standard libraries. A general HTTP proxy would need a broader forwarding
surface; this single endpoint retains the Broker's origin and header validation.

The wrapper delegates execution to the existing Heimdall tool, preserving its
command hooks and sandbox. The shipped sandbox shares the host network, allowing
loopback access; a deployment that intentionally disables sandbox networking
cannot use this client and must not silently weaken its policy. Each execution
stages the Python module in a temporary directory in its Runtime Workspace and
removes it with the listener on completion. The captured Assistant Turn must
still be active; later turns, runtime disposal, cancellation and Login Session
revocation cannot lend new authority to an old script. Already committed OA
business actions cannot be undone by cancellation.

The per-execution capability and listener are an application boundary, not a
sandbox against arbitrary hostile processes with access to the Dano host itself.
The production sandbox hides host procfs. No access to other Runtime Workspaces
is added by this feature.

Streaming output is redacted before publication. Full-output artifact links are
withheld while the underlying bash accumulator is writing, and those files are
sanitized before the final result exposes them. The accumulator can temporarily
hold raw output on the host during execution; this does not provide protection
against other hostile processes with host access.

Bash results include `providerRequests`: method, query-free path, HTTP status or
stable error, and whether the request was authenticated through the bound Login
Session. They contain no Login Session identifier or token. These records, the
script's exit status and real OA response codes support acceptance; a model's
summary is not sufficient evidence.

## PointLion acceptance fixture

The user-supplied original ZIP is preserved under
`apps/dano/src/bridge/__tests__/fixtures/pointlion-todo-query-token-inline.zip`.
Its SHA-256 is
`2685a04b163adfe490740390dd351a02d297786e898ff7abb5181e2b58abf02a`.
The adapted Skill is `examples/skills/pointlion-todo-query`; use its README and
SKILL instructions. It preserves the supplied doctor's four real OA calls,
dynamic enumerations, dictionary decoding and todo query. Authentication now
uses Dano rather than a hand-edited token. The original fixture contains only a
token placeholder.

Deploy the current implementation locally through the shipped Podman/Compose
path, with isolated named runtime volumes and real OA client configuration.
Configure the local HTTPS callback in both Dano and the OA client; no production
deployment is required. Through the in-app Browser, log in and ask Dano to
run the adapted Skill's `doctor`, then `query --page 1 --page-size 1`. Acceptance
requires doctor exit 0, HTTP 200 and business code 0 for all four endpoints,
plus valid query results (including a genuinely empty page). Record the actual
counts; they are not fixed test expectations. Retain sanitized script/response
and browser evidence and the implementation/Skill versions. Direct model
`provider_request` calls, mocks, or manually populated tokens do not meet this
gate. See Issue #456 for the required target and full acceptance contract.

For Issue #456 specifically, the OA page and original Skill use
`http://admin.dianshixinxi.com:90`; the deployed `DANO_OAUTH_API_ORIGIN` must use
that origin before acceptance. The token and identity endpoints remain on their
separately configured origin. Record the actual outbound business request origin
in the sanitized acceptance evidence; success against `h5.dianshixinxi.com` does
not fulfill this gate.

No `ask_user_question` capability or model argument schema is changed.
