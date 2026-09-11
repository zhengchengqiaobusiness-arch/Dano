# Existing OA Python Skills

Run the original Skill unchanged inside Dano's controlled bash tool. During an
authenticated Assistant Turn, standard Python `urllib.request` calls to the
configured OA business origin are routed through the Credential Broker. Its
server-side Authorization header replaces the script's old header. Scripts do
not import a Dano library or receive the current OA access/refresh token.

## Supported runtime

Python 3.9+ with normal site initialization: `urlopen`, `build_opener` and
`OpenerDirector.open`, including HTTP and HTTPS JSON/text APIs. Dano stages a
`sitecustomize` module in a temporary execution directory and prepends it to
PYTHONPATH. Existing site customization is retained. Skill files and the global
Python installation are not modified.

Other HTTP clients (including requests, urllib3 and raw sockets), Python
`-S`/`-I`/`-E`, and environments that remove or override the injected startup path
are not covered. This is execution integration, not an OS-wide egress firewall.
Requests currently require buffered UTF-8 bodies; streamed/binary request bodies
fail explicitly rather than silently using the script's old identity. Responses
are intended for OA JSON/text APIs.

The implementation uses Python's documented [site customization](https://docs.python.org/3/library/site.html#sitecustomize)
and [urllib opener](https://docs.python.org/3/library/urllib.request.html#openerdirector-objects)
interfaces. Hooks run only inside Dano-controlled executions.

## Target and identity

Configure Dano's business API origin to match the OA page origin and the Skill's
existing target. OAuth token and identity endpoints are independent settings.
Compare parsed scheme, hostname and effective port exactly, not hostname
substrings, DNS IP equality or arbitrary subdomains. Non-matching requests retain
their ordinary behavior and never receive Dano credentials.

An anonymous execution retains the original script's authentication behavior.
A captured authenticated execution cannot switch to another Login Session or
fall back to a package token when its binding expires. Credential refresh uses
the existing Broker. OA redirects return an HTTP error to urllib; neither Dano
credentials nor a restored package identity follow a redirect. HTTP error status
and body are available through `HTTPError`; authentication/transport failures
surface through `URLError` with a stable Broker error code.

Per-execution local capability secrets are output-redacted and released when the
bash invocation ends. They are not OA tokens. The existing `provider_request`
model tool remains available, but is not a replacement for original-Skill tests.

## Original PointLion acceptance

Use the supplied `pointlion-todo-query-token-inline.zip` without modifying any
original file. The repository fixture contains a rejected placeholder token,
not a verified usable credential. Its SHA-256 is
`2685a04b163adfe490740390dd351a02d297786e898ff7abb5181e2b58abf02a`.

The package validates its token before making requests. Use its existing
`--token INVALID-ORIGINAL-TOKEN` CLI option to pass that local placeholder check.
This marker is deliberately invalid; it is not the logged-in credential. Do not
change its config, use a real token argument, or edit its request imports/URLs.
Run `doctor` and `query --page 1 --page-size 1` in the logged-in Dano execution.

Automated tests use the package's existing `--base-url` option to target a local
fake OA. This isolates tests from the real OA and is not real-domain acceptance.
Real OA acceptance must retain the original business URL without that override.

Require unchanged per-file hashes, doctor exit 0, all four HTTP 200/business
code 0 results, query exit 0 and directly parsed list/total. Tool audit contains
`providerRequests`: method, path without query, HTTP/error outcome, and `sends`
with `targetMatched` and `authorizationMatched` evaluated at final server send.
`loginSessionBound` is derived from those send checks, not the HTTP outcome.
No raw credential, Login Session identifier or private business payload belongs
in public evidence. These are Dano-side observations; they do not claim an
independent OA-side token comparison. Preserve sanitized evidence before cleanup.

See the [full acceptance contract](specs/oa-skill-transparent-login-token.md).
The earlier adapted example has been removed because modifying the Skill did
not satisfy that contract.

## Why this interception boundary

Installing a global default opener alone misses Skills that call `build_opener`
themselves. A standard `BaseHandler` is therefore added to each new
`OpenerDirector` (and any existing default opener). Interception happens at its
HTTP/HTTPS transport stage, after native/custom request processors; successful
responses continue through the existing response processors and audit events.
Only the constructor is wrapped to install that handler. Replacing `open` itself
was rejected because it bypasses business headers and the normal handler chain.

A process HTTP proxy alone cannot replace encrypted HTTPS headers. TLS
interception would introduce an additional trust and deployment boundary, so it
is not used. The handler routes matching requests to the existing server-side
Broker instead. Normal HTTPS verification remains enabled. Startup import or
hook installation failure terminates Python rather than reverting to an old
package credential.
