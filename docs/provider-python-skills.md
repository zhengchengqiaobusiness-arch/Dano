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

`request(method, path, headers=None, body=None)` returns the existing Broker
response envelope (`ok`, HTTP `status`, sanitized `headers`, text `body`). For JSON
writes, pass an object as `body`; the Broker serializes it. Relative paths retain
query strings. The configured provider origin and Authorization header are
server-owned. OA HTTP/business failures must be handled by the caller; they are
not always login failures. Broker authentication and validation errors raise
`ProviderError` with its stable `code`.

Dano injects `dano_provider` on PYTHONPATH only while a bash call runs. The client
uses a loopback HTTP listener with a random 256-bit execution capability. The
capability is not an OA credential and cannot select an identity. The listener
accepts at most 1 MiB per request and does not accept requests without that
capability. The client ignores HTTP proxy environment variables and follows no
redirects. No public listener, global user-token environment, or token export
endpoint is introduced.

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

Through the in-app Browser, log in to the target Dano deployment and ask it to
run the adapted Skill's `doctor`, then `query --page 1 --page-size 1`. Acceptance
requires doctor exit 0, HTTP 200 and business code 0 for all four endpoints,
plus valid query results (including a genuinely empty page). Record the actual
counts; they are not fixed test expectations. Retain sanitized script/response
and browser evidence and the implementation/Skill versions. Direct model
`provider_request` calls, mocks, or manually populated tokens do not meet this
gate. See Issue #456 for the required target and full acceptance contract.

No `ask_user_question` capability or model argument schema is changed.
