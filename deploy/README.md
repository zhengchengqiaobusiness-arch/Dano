# Dano Deployment

This directory contains deployment-specific defaults and proxy config.

## Product Site Sidecar

`apps/sites` is released independently from the Dano application and is served
at `/web/`. Its source of truth is the current repository commit; production
must not deploy a copied or separately maintained site tree.

Build a revision-labelled image from the repository root:

```bash
revision="$(git rev-parse HEAD)"
short_revision="$(git rev-parse --short=12 HEAD)"
version="$(node -p "require('./package.json').version")"

docker build --no-cache \
  --build-arg DANO_SITE_BASE_PATH=/web \
  --build-arg DANO_SITE_REVISION="$revision" \
  --build-arg DANO_SITE_VERSION="$version" \
  -t "dano-site:$short_revision" \
  -f apps/sites/Dockerfile .
```

`deploy/compose/site.yml` is an optional overlay. Starting only its
`dano-site` service leaves the Dano app, runtime volumes, nginx, TLS, and
adjacent services untouched:

```bash
export DANO_SITE_IMAGE="dano-site:$short_revision"
docker compose \
  -f docker-compose.yml \
  -f deploy/compose/site.yml \
  --env-file .env \
  up -d --no-build --no-deps dano-site
```

The shared nginx proxy keeps `/web` as a permanent redirect to `/web/`, routes
the product site to the optional `dano-site` container, and strips `/web` only
for Vinext's generated `/assets/` files. Runtime DNS resolution lets nginx and
the Dano app start when the optional site is absent.

Before switching images, retain the previous healthy site container or image
tag as the rollback point. Validate `/web/`, every emitted JS/CSS resource,
the logo and four case screenshots, then verify anchor navigation in the real
production browser. Once accepted, remove only obsolete site rollback
containers/images and temporary build artifacts. Never remove Dano runtime
volumes as part of a site release.

## Runtime Layout

- Source runtime defaults live in `deploy/runtime-defaults/`.
- The runtime root is `${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}`.
- The Pi agent config directory is
  `${PI_CODING_AGENT_DIR:-$DANO_RUNTIME_DIR/.pi/agent}`.
- Deployment-managed runtime skills stay under
  `/opt/dano/runtime-data/.agents/skills`.
- The image activates `open-websearch` under Pi's native global skill directory
  `/opt/dano/runtime-data/.pi/agent/skills`.
- The image globally installs the pinned `open-websearch` CLI and runs the
  upstream `skills` installer during the image build to seed the matching skill
  under `/app/open-websearch-skill-seed/.agents/skills`.

`productName` in `dano.config.json` is the default assistant name used by the
browser title, empty state, composer prompt, and the initial system-prompt
render. A deployment may set `DANO_PRODUCT_NAME` explicitly to override that
single configured value; Compose does not define a second product-name default.

- Production deployment keeps three directories separate:
  - `/tmp/dano-build-*` is the disposable source checkout and image build dir.
  - `/opt/dano/deploy` stores Compose, `.env`, secrets, and nginx config.
  - `/opt/dano/runtime-data` is mounted at `/opt/dano/runtime-data` for runtime state.
- Docker Compose mounts
  `${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}:/opt/dano/runtime-data` for
  host-visible runtime state such as sessions and skills. The `.pi` and
  `workspaces` subtrees are Compose named volumes, mounted at
  `/opt/dano/runtime-data/.pi` and `/opt/dano/runtime-data/workspaces`.
  Model-triggered bash mounts Runtime Workspaces as writable and runtime skills
  as read-only, but does not mount `/opt/dano/runtime-data/.pi` or its contents.
  Agent config, Runtime Workspaces, and uploads still survive container recreation.
  Do not run Compose with `-v` unless you intend to remove those volumes.

On container startup, `deploy/docker-entrypoint.sh` creates:

```text
/opt/dano/runtime-data/.pi/agent/SYSTEM.md
/opt/dano/runtime-data/.pi/agent/settings.json
/opt/dano/runtime-data/.pi/agent/heimdall.json
```

The entrypoint initializes those files from `deploy/runtime-defaults/` only when
the runtime file is missing. It renders a missing `SYSTEM.md` with the effective
product name. It never overwrites an existing runtime file, so the host
persistence location may be edited directly. The release workflow explicitly
synchronizes `SYSTEM.md` once per deployment, before starting the new app image;
ordinary application and container restarts still preserve the current file.
Manual Compose deployments can perform the same explicit synchronization with:

```bash
docker compose --env-file .env run --rm --no-deps app \
  node ./deploy/render-system-prompt.mjs --replace \
  /app/deploy/runtime-defaults/SYSTEM.md \
  /opt/dano/runtime-data/.pi/agent/SYSTEM.md
```

The shared renderer uses the mature `atomically` package for complete
temporary-file writes followed by atomic publication. Missing-file
initialization additionally uses a no-clobber hard-link publication so a
concurrent host-created file is preserved. The entrypoint does not copy defaults
into a Runtime Workspace `.pi` directory.

The entrypoint copies a missing image-seeded skill into Pi's persistent global
skill directory. Pi discovers it natively without a `settings.skills` entry, and
the startup performs no download. Existing settings and an operator-managed
skill with the same name are preserved. Heimdall exposes this directory to model
tools as read-only while keeping the rest of the Agent Config Directory hidden.

On every normal Dano app start, the entrypoint starts `open-websearch serve` on
`127.0.0.1:3210`, waits for `open-websearch status` to succeed, and then starts
the Dano server. The daemon is not published by Compose. The app and daemon are
one runtime lifecycle: app exit stops the daemon, while daemon exit stops the
app so the container restart policy can recover both together.

The default search engine is DuckDuckGo with `SEARCH_MODE=auto`. Deployment
operators can set the `OPEN_WEBSEARCH_*` values documented in `.env.example` to
restrict engines or configure an explicit runtime proxy. Package-install proxy
or registry settings remain separate from these runtime network settings.

## Production Authentication

Production runs one OAuth confidential client and does not inject a fixed User
or authentication Cookie. The required deployment values are:

```text
DANO_OAUTH_ISSUER
DANO_OAUTH_AUTHORIZATION_ENDPOINT
DANO_OAUTH_TOKEN_ENDPOINT
DANO_OAUTH_IDENTITY_ENDPOINT
DANO_OAUTH_API_ORIGIN
DANO_OAUTH_CLIENT_ID
DANO_OAUTH_CLIENT_SECRET
DANO_OAUTH_SCOPE
DANO_OAUTH_REDIRECT_URI
DANO_OAUTH_CREDENTIAL_KEY
DANO_OAUTH_CREDENTIAL_KEY_VERSION
```

All server-to-server provider endpoints, the API origin, and the callback must
use trusted HTTPS. If the provider exposes only its browser-facing authorization
UI over HTTP, set its real URL in `DANO_OAUTH_AUTHORIZATION_ENDPOINT` and opt in
with `DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT=true`. This exception does
not relax transport validation for the issuer, token, identity, revocation, API,
or callback endpoints. Do not replace the provider origin with a Dano same-origin
proxy: provider redirects such as `/login` must continue to resolve against the
provider's origin.

If the provider exposes its issuer, token, identity, revocation, or API endpoint
only over HTTP, set each endpoint to the provider's real URL and explicitly opt
in with `DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS=true`. This accepts
plaintext server-to-provider transport; it does not add a proxy, relay, or
alternate network path. The Dano callback remains trusted HTTPS, and the browser
authorization UI must continue to use the provider's real origin.

`DANO_OAUTH_CLIENT_AUTH_METHOD` is optional and defaults to
`client_secret_post`. Set it to `client_secret_basic` when the provider requires
HTTP Basic client authentication at the token endpoint.

If the provider requires fixed transport headers, configure them as a JSON
object in `DANO_OAUTH_PROVIDER_HEADERS_JSON`. Dano applies them only inside the
server-side OAuth provider adapter; protocol-generated headers take precedence.
For providers that require callback state again during code exchange, set
`DANO_OAUTH_SEND_STATE_TO_TOKEN_ENDPOINT=true`; the adapter keeps state scoped
to the corresponding exchange.

Provider credential revocation is optional. Use
`DANO_OAUTH_REVOCATION_TRANSPORT=rfc7009` with an explicit
`DANO_OAUTH_REVOCATION_ENDPOINT` for the standard protocol, or
`delete-query-basic` for providers that require a DELETE request with Basic
client authentication. The latter defaults to the configured token endpoint.

### OAuth browser-session synchronization boundary

Do not use a successful Authorization Code exchange or Provider Credential
revocation as proof that the provider's browser login state changed. The
provider browser origin, one-time Authorization Code, Dano Login Session, and
server-side Provider Credential have different owners:

| State or artifact | Owner | What ends it |
| --- | --- | --- |
| Provider Browser Session | Provider browser origin | Provider logout or a configured OIDC session/logout contract |
| Authorization Code | Provider, then one server-side Dano exchange | One successful exchange or provider expiry |
| Dano Login Session | Dano | Dano logout, expiry, or verified provider logout notification |
| Provider Credential | One Dano Login Session | Provider expiry, refresh replacement, or configured token revocation |
| Logout Propagation | Provider and Dano protocol configuration | Delivery and validation defined by the selected session/logout standard |

OAuth 2.0 token revocation and introspection are token contracts, not browser
session contracts. Full bidirectional login/logout synchronization requires a
verified provider capability such as OpenID Connect Session Management,
RP-Initiated Logout, Front-Channel Logout, or Back-Channel Logout. Record the
provider discovery metadata, required signed identifiers, registered logout
URIs, and browser acceptance evidence before enabling such a capability.

For an OAuth-only provider, the supported semantics are narrower:

- an explicit Dano login may reuse an existing Provider Browser Session;
- Dano logout removes only the current Dano Login Session and may revoke only
  its Provider Credential;
- provider browser login does not passively create a Dano Login Session;
- provider browser logout does not notify Dano.

Do not fill these gaps with authorization-page polling, copied browser tokens,
client secrets in browser code, or a Dano same-origin provider proxy. See
[`ADR 0007`](../docs/adr/0007-do-not-equate-oauth-token-revocation-with-browser-session-logout.md).

The redirect URI must be the fixed `/api/auth/callback` URL. Provider
server-to-server endpoints, the API origin, and the production callback must
use trusted HTTPS. URL credentials, fragments, callback query parameters, a
non-origin API value, global TLS verification bypass, incomplete configuration,
and any residual fixed Demo/JWT authentication setting stop production startup.
The Credential key is exactly 32 random bytes encoded as base64url; rotate its
version together with its deployment-managed key. Do not put client secrets,
Credential keys, tokens, account identifiers, or provider payloads in source,
image layers, command arguments, logs, or test snapshots.

Login Session defaults are idle eight hours, absolute seven days, and hourly
cleanup. They can be set with positive integer milliseconds:

```text
DANO_LOGIN_SESSION_IDLE_TTL_MS
DANO_LOGIN_SESSION_ABSOLUTE_TTL_MS
DANO_LOGIN_SESSION_CLEANUP_INTERVAL_MS
DANO_ANONYMOUS_IDLE_TTL_MS
DANO_ANONYMOUS_CLEANUP_INTERVAL_MS
```

The absolute Login Session TTL must be greater than its idle TTL. Anonymous
User data defaults to 24 hours idle with hourly cleanup.

Release Build runs `node ./dist/server/main.js --validate-config` inside the
new image with the container entrypoint bypassed before replacing containers.
This Production Authentication Gate uses the same parser and provider TLS
validation as normal production startup, and exits before Agent Config, local
search, Runtime, or listener initialization. Failure leaves the running
deployment unchanged. After a successful gate, the release atomically removes
only legacy fixed-Demo keys from the Deploy Control Directory and starts the
OAuth-only Compose configuration. Nginx forwards `/api/auth/*` Cookies and
Origin; the exact callback location disables both access and error logs so code
and state query values are not logged even when the upstream fails.

The app container runs as the non-root `node` user (`1000:1000`) with
`HOME=/home/node`. The image installs `/usr/bin/bwrap` setuid (`4755`) because
the verified production Docker host rejects non-setuid Bubblewrap with `bwrap
must be installed setuid`, even when container capabilities are added. Compose
adds `cap_add: ALL` and `security_opt: seccomp=unconfined`; this is broader than
the default container profile, but narrower than `privileged: true`, and is the
verified working combination for model-triggered Heimdall `bash`. The app
process still runs as `node`, not root.

The image also sets `HEIMDALL_BWRAP_BIND_KERNEL_FS=1` so Heimdall binds the
container's existing `/dev` instead of asking Bubblewrap to mount nested device
filesystems. It sets `HEIMDALL_BWRAP_BIND_PROC=0` so chat-triggered bash cannot
reach the outer container filesystem through `/proc/<pid>/root`. It also sets
`HEIMDALL_BWRAP_BIND_ROOT=/opt/dano/runtime-data/workspaces` so non-root
Bubblewrap can keep Runtime Workspaces writable without exposing sibling runtime
state such as `.pi`. The sandbox replaces Heimdall's default `/opt` mount with
the exact read-only runtime skills path.

## Local Compose Run

For repeatable local Podman runs, use the stable commands below. Build the
reusable local image after source changes, then start or stop the existing
Compose deployment on `http://localhost:18082`:

```bash
pnpm run container:build
pnpm run container:up
pnpm run container:down
```

These commands use Podman and keep local runtime data outside the source
checkout under the system temporary directory. Start the Podman machine first
when it is not already running. Run `smoke:deploy` and the browser acceptance
steps below after `container:up` when validating a change.

The HTTP-only local example below validates Anonymous User, chat, upload, SSE,
and deployment behavior. OAuth browser acceptance additionally requires a
trusted HTTPS callback and a TLS-capable exposure mode (or an environment-owned
TLS terminator); do not treat the HTTP example as OAuth acceptance. In the
HTTPS browser flow, verify the login entry in the left Menu, clean callback
return, authenticated Menu, logout, and the new usable Anonymous User.

### Real OAuth User isolation release gate

Use one Dano callback for both controlled test accounts. Authenticate slot A in
the Codex in-app Browser and slot B in Chrome against the same running Dano
origin; a second callback address or second Dano frontend is not part of this
gate.

Start the live capture producer before the browser run:

```bash
pnpm run test:auth-real-users -- capture /path/to/real-user-evidence.json \
  --origin http://localhost:5173
```

The producer prints one run-specific module URL for slot A and one for slot B.
Import slot A in the authenticated Codex in-app Browser and slot B in
authenticated Chrome, then call the module's `run()` export. The modules use
the current browser's HttpOnly login session indirectly through same-origin
Dano requests; they never read or forward a Cookie. They create and inspect
their own Client, Agent Session, transcript, Runtime Workspace, upload, and
preference, exchange the exact in-memory resource targets through the local
collector, and run the cross-User probes in both directions.

The collector emits `LIVE HTTP/SSE/Pi COLLECTOR PASS` only after both modules
finish and its in-memory terminal validation succeeds. This proves the observed
HTTP/SSE/Pi behavior and resource relationships, not which browser surface sent
the requests: application-layer requests cannot distinguish the Codex in-app
Browser, Chrome, or another compatible client. Keep the manifest's IAB/Chrome
mapping as the operation contract and attach the external actual-browser
acceptance record as the browser-surface provenance. Raw resource identifiers are used in memory for
the cross probes but are fingerprinted before persistence. The JSON file is a
redacted audit record only; it does not prove browser provenance and cannot be
used to reconstruct a live collector PASS. Provider
addresses, credentials, Cookies, raw identifiers, private payloads, response
bodies, and response headers are never persisted.

Optionally check the redacted record's structure. This deliberately prints
`AUDIT ONLY (NOT LIVE COLLECTOR PASS)`:

```bash
pnpm run test:auth-real-users -- audit /path/to/real-user-evidence.json
```

### Anonymous User cleanup live behavior gate

The Anonymous User cleanup harness exercises two distinct Cookie bindings over
public Dano HTTP/SSE and immediately checks the live runtime for cross-User
session, transcript, Runtime Workspace, file, upload, and preference isolation;
idle cleanup; active SSE and Assistant Turn protection; and authenticated User
retention. Its `PASS live HTTP/SSE/runtime` result proves those live transport
and runtime behaviors only. The harness cannot distinguish the Codex in-app
Browser from Chrome over HTTP, and its transport fingerprints prove only that
the two Cookie bindings differ. Browser-surface provenance must be recorded by
the external acceptance run that actually operates slot A in the Codex in-app
Browser and slot B in Chrome; the harness and offline audit do not prove it.

### Real provider Skill/Broker release gate

Use the test-only `provider-broker-release-gate` Skill to prove that a real Pi
Turn reaches the configured provider through `provider_request`. Choose a
read-only provider path whose response is safe for an acceptance transcript;
the path stays in runtime configuration and is not part of Dano core.

Install the Skill into the disposable Agent Config Directory used by the test
runtime **before starting Dano**. Start the backend with the same
`PI_CODING_AGENT_DIR` and provider-path environment; that directory must also
contain the real model configuration and credential used by the acceptance
runner:

```bash
PI_CODING_AGENT_DIR=/path/to/test-agent-config \
DANO_PROVIDER_ACCEPTANCE_PATH=/configured/read-only/status \
node scripts/check-provider-skill-release-gate.mjs install
```

Start the live collector after installing the Skill and starting the test Dano
runtime. The producer reads the selected model through public `get_state`; both
browser slots must report the same non-empty provider/model selection. It
prints one run-specific module URL for Login Session A in the Codex
in-app Browser and one for Login Session B in Chrome:

```bash
pnpm run test:auth-real-provider-skill -- capture \
  /path/to/provider-skill-evidence.json \
  --origin http://localhost:5173
```

Use the single configured Dano callback and one running Dano origin for both
Login Sessions. Create Login Session A in the Codex in-app Browser and Login
Session B in Chrome by signing the same test account into each browser context;
do not add a second callback address or start a second Dano frontend. Open the
same Agent Session from both. The B producer calls `switch_session` once and
then remains continuously subscribed as a viewer; do not reload or switch B
again while A's question is pending. This keeps the real Pi runtime and its
open `ask_user_question` alive when A disconnects. The collector does not let A
logout until B's persistent viewer has observed that held question. Invoke the
Skill from A with a unique
`gate-a-before` marker. The Skill asks one controlled `ask_user_question`
single-choice question; answer `continue` from A and confirm its subsequent
`provider_request` succeeds.

Invoke it from A again with `gate-a-after` and leave that question pending. Log
out A and confirm its old client is disconnected. From B's view of the same
Agent Session, answer A's pending question with `continue`. This resumes the
already-started Assistant Turn, whose Credential remains bound to A; its
subsequent `provider_request` must return `authentication_required`. Finally
invoke the Skill in a new Turn from B with `gate-b-after`, answer its question
from B, and confirm the provider request still succeeds. The controlled test
accounts are recorded in
`apps/dano/src/__tests__/fixtures/real-oauth-acceptance.json`.

Import each printed module in its matching authenticated browser and call
`run()` without awaiting it in the console so both slots can run concurrently,
for example `void import("<SLOT_A-or-SLOT_B>").then(module => module.run())`.
Start A first; after it has created the shared Agent Session, start B. The
modules use only public Dano HTTP/SSE commands and the browser's
HttpOnly Login Session implicitly; they never read or forward Cookies. The
operation contract assigns A to the Codex in-app Browser and B to Chrome, with
both on the same Dano callback without recording its address. The collector
cannot prove those browser surfaces from application-layer requests; retain the
external actual-browser acceptance record for that provenance:

- `aStatus` and `bStatus` come from each authenticated auth DTO.
- Client fingerprints are SHA-256 hashes of the two `/api/clients` response
  Client IDs. A's two prompts must use the same Client; B must be different.
- User ID fingerprints are SHA-256 hashes of the authenticated auth DTO's
  browser-safe, opaque `user.id`. They must match; do not record the raw ID.
  `defaultWorkspacePath` is runtime data and must never be used as User
  identity. Set the User's preference to the collector-provided `yellow` marker through A
  and read it through B, then restore the initial preference after capture.
- Session fingerprints are SHA-256 hashes of the `sessionPath` returned through
  the Bridge. B must successfully `switch_session` to A's path; record only the
  hash, never the path.
- The public `get_state` response must contain a selected Pi provider/model in
  both slots, and both selections must match. Model credentials and model
  configuration are not copied into the audit record.
- Record `aBeforeBrowser` as `codex-in-app-browser`; record both
  `aAfterBrowser` and `bAfterBrowser` as `chrome`. These are expected operation
  labels, not collector-observed browser provenance. The external acceptance
  record identifies which browser submitted each public `answer_question`
  command without recording a Cookie or Login Session ID.
- Sequence timestamps surround each public prompt, question tool call,
  `answer_question` command/result, logout POST, old-A Client request, B auth
  DTO read, and provider result. Logout must return 200 after A's second
  question call and before B answers it; the old A Client request must
  return 404, and B must remain `authenticated` before its post-logout Skill
  Turn succeeds.

The collector retains raw Client, Session, and selected-model mappings only in
memory, reads the live Pi transcript itself, and emits
`LIVE HTTP/SSE/Pi COLLECTOR PASS` only
after all three ordered phases pass. This proves the behavior and resource
relationships, while browser-surface provenance remains part of the external
IAB/Chrome acceptance record. The written JSON is a redacted audit record and cannot reconstruct
the live result. Its structure can be checked separately without printing
response bodies, headers, credentials, Cookies, Login Session IDs, or provider
addresses; this prints `AUDIT ONLY (NOT LIVE COLLECTOR PASS)`:

```bash
DANO_PROVIDER_ACCEPTANCE_PATH=/configured/read-only/status \
DANO_PROVIDER_GATE_SESSION=/path/to/shared-session.jsonl \
pnpm run test:auth-real-provider-skill -- audit \
  /path/to/provider-skill-evidence.json
```

The producer waits for each Turn's settled transcript snapshot instead of
advancing on an intermediate tool-result event. The verifier requires the
Skill's exact canonical `ask_user_question` call and its answered `continue`
result before each `provider_request`, so the held A
Turn is portable and reproducible through Dano's public HTTP/SSE command seam,
without a hidden acceptance endpoint, shell sandbox, copied credential, or
direct Broker call. Remove the test Skill after acceptance:

```bash
PI_CODING_AGENT_DIR=/path/to/test-agent-config \
node scripts/check-provider-skill-release-gate.mjs remove
```

### Real provider refresh release gate

Run the refresh producer with the normal disposable OAuth runtime environment
and the same read-only `DANO_PROVIDER_ACCEPTANCE_PATH` used by the Skill gate:

```bash
pnpm run test:auth-real-refresh:run
```

The producer keeps credentials in process memory and observes the real OAuth
adapter, encrypted Login Session record, Broker retry, public auth HTTP/SSE
flow, and Pi transcript directly. It does not write an evidence JSON file or
expose an acceptance route. For an HTTP-only controlled test provider, opt in
only for this process with `DANO_REFRESH_ACCEPTANCE_ALLOW_INSECURE=true`.

Create the target Login Session in the Codex in-app Browser and the peer Login
Session in Chrome by signing the same controlled account into the single Dano
callback. Keep the peer authenticated throughout every phase. After both live
Clients are observed, send the indicated signal to the producer process, then
invoke `provider-broker-release-gate` from the target with the printed marker:

- `SIGUSR2`: real refresh succeeds; call `/api/auth/current` and keep the same
  Login Session.
- `SIGURG`: the real token endpoint rejects refresh; refresh the Dano page to
  confirm `reauth_required`, then click **继续匿名使用** and wait for the new
  Anonymous Client.
- Log in again, then `SIGWINCH`: repeat the rejected refresh, refresh Dano to
  display the AlertDialog, and click **重新登录**. Keep the producer running
  until it prints `confirm: PASS`.

After each target Skill result, refresh the peer Dano page once so its public
`/api/auth/current` state is observed. The phase cannot pass unless the peer
remains authenticated with the same encrypted Login Session Credential.

The failure phases use a random incorrect confidential-client secret only for
the real refresh request. Neither the secret nor any provider token, address,
Cookie, raw User ID, response body, or response header is printed or persisted.
For every phase, the producer atomically replaces only the target Login
Session's encrypted access token with a one-time invalid value while preserving
its real refresh token. The configured business API must reject that access
token before the Broker attempts refresh. This avoids provider revocation,
which can invalidate the whole grant and make a successful refresh impossible.
The success phase then requires the real token endpoint grant, identity
validation, encrypted Credential record rotation, accepted retry, and unchanged
peer Credential. The failure phases require the real token endpoint to reject
the incorrect client secret before `reauth_required` and the selected UI path.

```bash
cp .env.example .env
# Fill the required DANO_OAUTH_* values in .env before startup.
docker build -t dano-app:local .
DANO_NGINX_PORT=18082 pnpm run deploy:up
DANO_SMOKE_BASE_URL=http://127.0.0.1:18082 pnpm run smoke:deploy
pnpm run deploy:logs
pnpm run deploy:stop
pnpm run deploy:down
```

For local runs, point `DANO_RUNTIME_DIR` and `DANO_SECRETS_DIR` in `.env` at
local host paths. The deploy wrapper selects the nginx template and shared
proxy configuration for `DANO_EXPOSURE_MODE`. The app container still uses
`/opt/dano/runtime-data` internally; the host `DANO_RUNTIME_DIR` only selects
what is mounted there. `deploy:up` runs Compose with
`--no-build`; build the image first or use `deploy:release`. `deploy:stop`
preserves containers and runtime data. `deploy:down` removes the containers and
Compose network; the bind-mounted runtime directory remains intact.

The app container listens on `8080`; nginx publishes only the ports selected by
`DANO_EXPOSURE_MODE`.

### Full Local Podman Acceptance

For changes that affect model runtime, uploads, Heimdall, bash, container
permissions, or runtime directories, `smoke:deploy` is not enough. Run this
minimum acceptance sequence against the Podman Compose deployment:

1. Build and start the image with Podman Compose.
2. Run `smoke:deploy` against nginx.
   For exposure-mode changes, also run the isolated four-mode acceptance against
   the current prebuilt image. It generates a disposable self-signed certificate
   with Compose-significant filename characters, verifies the served certificate,
   published protocols, redirect path/query, and application health, then removes
   its containers, volumes, network, and temporary files:

   ```bash
   DANO_COMPOSE=podman \
   DANO_IMAGE=dano-app:local \
   pnpm run deploy:check-exposure
   ```
3. In the browser, send a plain text chat and confirm the model replies.
4. In the browser, upload an image and confirm the model can read it.
5. In the browser, ask the model to run exactly this safe command and not read
   files, environment variables, runtime data, or secrets:

   ```text
   Use the bash tool to run: printf DANO_BASH_OK
   ```

6. Run the bash acceptance checker against the JSONL file or session directory
   created by this browser run:

   ```bash
   pnpm run deploy:check-bash -- /path/to/runtime-data/.dano/sessions/<workspace-session>/<session>.jsonl
   ```

   If the server host does not have Node or pnpm, run the same checker through
   a read-only Node container:

   ```bash
   DANO_RUNTIME_DIR=/path/to/runtime-data \
   sh scripts/check-bash-acceptance-container.sh /path/to/runtime-data/.dano/sessions/<workspace-session>/<session>.jsonl
   ```

   It reports whether a `bash` tool call occurred, whether a successful
   `DANO_BASH_OK` tool result was recorded, and whether any `bwrap` error text
   appeared in session JSONL.

   For OA gateway changes, distinguish the host shell, app container shell, and
   model-triggered bash environment. `/opt/dano/deploy/.env` is read by Docker
   Compose or Podman Compose when `--env-file .env` is used; it does not make
   `DANO_URL` or `DANO_TENANT_KEY` available to an interactive host shell. The
   Compose service maps those values into the app container environment
   (`dano-app-1` for the default project name). Model-triggered `bash` then runs
   through Heimdall's sandbox env filter, so it is a third environment boundary,
   distinct from both the host shell and a direct container shell.

   Use presence markers only; never print `KEY=value` pairs or secret values.
   Secret redaction can make `KEY=value` output ambiguous, while markers such as
   `TENANT_PRESENT` / `TENANT_MISSING` prove presence without exposing values.

   Host shell check:

   ```bash
   cd /opt/dano/deploy
   test -n "${DANO_URL:-}" && echo HOST_URL_PRESENT || echo HOST_URL_MISSING
   test -n "${DANO_TENANT_KEY:-}" && echo HOST_TENANT_PRESENT || echo HOST_TENANT_MISSING
   ```

   App container shell check:

   ```bash
   podman compose --env-file .env exec app sh -lc 'test -n "${DANO_URL:-}" && echo APP_URL_PRESENT || echo APP_URL_MISSING; test -n "${DANO_TENANT_KEY:-}" && echo APP_TENANT_PRESENT || echo APP_TENANT_MISSING; /opt/dano/runtime-data/.agents/skills/dano-a-oa-qingjia/scripts/submit.sh --list-options 请假类型'
   ```

   The direct app-container command proves Compose injected the variables and
   the OA leave skill can reach the gateway from `dano-app-1`; it does not prove
   the model-triggered bash tool received the same environment.

   Browser model bash prompt:

   ```text
   Use the bash tool to run this exact command. Do not print secret values:
   printf '%s\n' OA_ENV_CHECK
   test -n "${DANO_URL:-}" && echo URL_PRESENT || echo URL_MISSING
   test -n "${DANO_TENANT_KEY:-}" && echo TENANT_PRESENT || echo TENANT_MISSING
   /opt/dano/runtime-data/.agents/skills/dano-a-oa-qingjia/scripts/submit.sh --list-options 请假类型
   ```

   Then check the model-triggered bash session:

   ```bash
   DANO_BASH_ACCEPTANCE_MARKER=OA_ENV_CHECK \
   DANO_BASH_ACCEPTANCE_REQUIRED_MARKERS=URL_PRESENT,TENANT_PRESENT \
   DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS='URL_MISSING,TENANT_MISSING,DANO_URL/DANO_TENANT_KEY 未设置' \
   pnpm run deploy:check-bash -- /path/to/runtime-data/.dano/sessions/<workspace-session>/<session>.jsonl
   ```

   Without host Node or pnpm:

   ```bash
   DANO_RUNTIME_DIR=/path/to/runtime-data \
   DANO_BASH_ACCEPTANCE_MARKER=OA_ENV_CHECK \
   DANO_BASH_ACCEPTANCE_REQUIRED_MARKERS=URL_PRESENT,TENANT_PRESENT \
   DANO_BASH_ACCEPTANCE_FORBIDDEN_MARKERS='URL_MISSING,TENANT_MISSING,DANO_URL/DANO_TENANT_KEY 未设置' \
   sh scripts/check-bash-acceptance-container.sh /path/to/runtime-data/.dano/sessions/<workspace-session>/<session>.jsonl
   ```

   This OA check is required because `smoke:deploy`, upload checks, host shell
   checks, and direct app-container shell checks do not prove the filtered
   model-triggered bash tool environment.

   For diagnostics only, scan the full mounted runtime directory explicitly:

   ```bash
   DANO_RUNTIME_DIR=/path/to/runtime-data DANO_BASH_ACCEPTANCE_SCAN_ALL=1 pnpm run deploy:check-bash
   ```

   Without host Node or pnpm:

   ```bash
   DANO_RUNTIME_DIR=/path/to/runtime-data DANO_BASH_ACCEPTANCE_SCAN_ALL=1 sh scripts/check-bash-acceptance-container.sh
   ```
7. Confirm the app container still runs as `node`, Heimdall is the expected
   package version, and `bwrap` can enter the Runtime Workspace.
8. Stop the Compose stack and remove temporary Dano test images/layers.

### Local Podman Notes

If `podman compose` fails with `could not find a matching machine`, check the
first error line before debugging Dano. On macOS, `podman compose` may fail
while listing machines if it cannot create or update the machine lockfile, for
example:

```text
open ~/.config/containers/podman/machine/applehv/podman-machine-default.lock:
operation not permitted
```

`podman info` can still work in that state because the remote socket is valid;
the failure is in Compose's machine enumeration. Fix the lockfile permission or
run Compose from a shell that can write Podman's machine state.

On macOS, keep the shell that started the Podman machine alive until the build
and Compose acceptance finish. Some local setups keep the API forwarding
process attached to that shell; closing it mid-run can surface misleading
overlay-storage errors on the next build.

When GitHub access requires the host proxy, pass the proxy into the image build
explicitly. The container cannot reach the host proxy through `127.0.0.1`; use
`host.containers.internal` and keep the configured package mirrors out of the
proxy path:

```bash
podman build --http-proxy=false \
  --build-arg HTTP_PROXY=http://host.containers.internal:7897 \
  --build-arg HTTPS_PROXY=http://host.containers.internal:7897 \
  --build-arg http_proxy=http://host.containers.internal:7897 \
  --build-arg https_proxy=http://host.containers.internal:7897 \
  --build-arg NO_PROXY=localhost,127.0.0.1,::1,mirrors.cloud.tencent.com,mirrors.aliyun.com \
  --build-arg no_proxy=localhost,127.0.0.1,::1,mirrors.cloud.tencent.com,mirrors.aliyun.com \
  -t dano-app:local .
```

Podman's automatic HTTP proxy injection can override explicit lowercase
variables from the machine configuration. Keep `--http-proxy=false`, then pass
both uppercase and lowercase arguments so package mirrors bypass the proxy while
GitHub downloads use the verified host proxy port. These build arguments affect
dependency installation only; configure `OPEN_WEBSEARCH_*` separately when the
running search daemon itself needs a proxy.

Interrupting the host `podman build` client does not always stop the matching
Buildah RUN process inside the Podman machine. Before retrying an interrupted
build, check the VM for abandoned `pnpm install` or `buildah-oci-runtime`
processes; otherwise multiple installers can keep retrying in parallel and make
the new build look hung. Clean only the abandoned build process, or restart the
machine after confirming that no required containers are running.

Do not use a plain `podman run` as a Compose-equivalent secret test. Compose
loads `.env` and passes variables such as `XIAOMI_TOKEN_PLAN_CN_API_KEY`; a
manual `podman run` only receives the environment values explicitly passed with
`-e`, so it can produce a false `No API key found` error.

## Production Server Run

The release script builds from a temporary source checkout, copies only deploy
inputs to `/opt/dano/deploy`, initializes or verifies the persisted Demo
authentication pair, starts the prebuilt image, runs the smoke test, and removes
`/tmp/dano-build-*` even when a step fails:

```bash
DANO_REPO_URL=git@github.com:zhengchengqiaobusiness-arch/Dano.git \
DANO_GIT_REF=main \
pnpm run deploy:release
```

Dependency installs use `https://mirrors.cloud.tencent.com/npm/` by default.
Set `NPM_REGISTRY` to use npmjs.org or a private registry for a release build.

To start from an already-built local or pulled image in `/opt/dano/deploy`:

```bash
cd /opt/dano/deploy
DANO_IMAGE=dano-app:local docker compose \
  -f docker-compose.yml \
  -f docker-compose.exposure.yml \
  --env-file .env up -d --no-build
```

`scripts/deploy-compose.mjs` uses the same `--no-build` path.

如果绕过 `scripts/deploy-release.mjs` 手动运行 Compose，需要确认持久化运行目录
可被容器内 `node` 用户写入：

```bash
mkdir -p /opt/dano/runtime-data
chown -R 1000:1000 /opt/dano/runtime-data
```

全新 release 部署会自动处理这一步。

The Dockerfile intentionally uses `node:22-bookworm-slim` instead of
`node:22-alpine`. On the CentOS 7 publish host
(`3.10.0-1160.108.1.el7.x86_64`, Docker 26.1.3, overlay2 on ext4), a minimal
`node:22-alpine` build with only `is-number` reproduces:

```text
EPERM: operation not permitted, write
```

The failure happens after dependency extraction, while pnpm writes temporary
metadata files such as:

```text
/app/pnpm-lock.yaml.<random>
/app/node_modules/.modules.yaml.<random>
/app/node_modules/.pnpm/lock.yaml.<random>
```

The following did not fix that Alpine-based failure on the publish host:
`--package-import-method=copy`, `--ignore-scripts`, `--node-linker=hoisted`,
`--store-dir=/tmp/pnpm-store`, `--virtual-store-dir=.pnpm`, disabling
side-effects cache, or changing pnpm between 8, 9, and 10. The same minimal
pnpm install succeeds with `node:20-alpine` and with `node:22-bookworm-slim`;
the full Dano image also builds successfully with `node:22-bookworm-slim`.

A CI-built image is still the preferred production path because target-host
builds depend on host Docker/kernel/storage behavior.

## Secrets

Do not commit `.env`, runtime data, or `.secrets/`.

Set provider credentials in `.env`:

```bash
printf '%s' "$XIAOMI_TOKEN_PLAN_CN_API_KEY" \
  | pnpm run secret:set -- XIAOMI_TOKEN_PLAN_CN_API_KEY
```

The helper updates the requested env var, sets `.env` to mode `600`, and does
not print the secret value.

The current Compose file passes these `_FILE` variables through for providers
that support file-backed secrets:

```text
OPENAI_API_KEY_FILE
ANTHROPIC_API_KEY_FILE
DEEPSEEK_API_KEY_FILE
```

Example:

```bash
mkdir -p .secrets
printf '%s' "$OPENAI_API_KEY" > .secrets/openai_api_key
chown 1000:1000 .secrets/openai_api_key
chmod 600 .secrets/openai_api_key
OPENAI_API_KEY_FILE=/run/secrets/openai_api_key pnpm run deploy:up
```

Compose mounts `${DANO_SECRETS_DIR:-/opt/dano/deploy/.secrets}:/run/secrets:ro`.

## HTTP and TLS

Release Build accepts `DANO_EXPOSURE_MODE` with these values:

| Mode | Published endpoints | HTTP behavior |
| --- | --- | --- |
| `http` | HTTP only | Serves Dano directly |
| `https` | HTTPS only | No HTTP endpoint |
| `both` | HTTP and HTTPS | Redirects HTTP to the matching HTTPS path and query |
| `both-no-redirect-http` | HTTP and HTTPS | Serves Dano directly on both protocols |

The default exposure mode is `http`, which can remain behind an environment
owned TLS terminator. The public OAuth callback and provider server-to-server
endpoints must still be configured with trusted HTTPS. Direct TLS exposure uses
the modes below.

TLS-capable modes require two environment-owned files:

```bash
DANO_EXPOSURE_MODE=https \
DANO_TLS_CERT_PATH=/etc/example/tls/public-chain.crt \
DANO_TLS_KEY_PATH=/etc/example/tls/private-key.pem \
pnpm run deploy:release
```

The host paths and filenames are arbitrary. Absolute paths are recommended for
production. Relative paths resolve from the Deploy Control Directory. Dano
mounts the files read-only at fixed container paths; it does not copy them into
the image or source checkout.

For local HTTPS on non-default ports:

```bash
DANO_EXPOSURE_MODE=both \
DANO_NGINX_PORT=18082 \
DANO_HTTPS_PORT=18443 \
DANO_TLS_CERT_PATH=/absolute/path/to/test-cert.pem \
DANO_TLS_KEY_PATH=/absolute/path/to/test-key.pem \
pnpm run deploy:up
```

In `both` mode, the HTTP redirect uses `DANO_HTTPS_PORT` and preserves the
request path and query. Use `both-no-redirect-http` when HTTP must remain a
fully usable endpoint.

Certificate issuance, provider selection, ACME configuration, renewal, and
scheduling belong to the deployment environment. Dano does not install
Certbot, systemd units, timers, or certificate lineage conventions. Because a
file-level bind mount can keep referencing an old inode after an atomic
certificate replacement, the environment should recreate the nginx container
after renewal instead of assuming that `nginx -s reload` is sufficient:

```bash
cd /opt/dano/deploy
docker compose \
  -f docker-compose.yml \
  -f docker-compose.exposure.yml \
  --env-file .env up -d --no-deps --force-recreate nginx
```

## Smoke Test

```bash
DANO_SMOKE_BASE_URL=http://127.0.0.1:18082 pnpm run smoke:deploy
```

The smoke test checks:

- `GET /`
- `GET /api/health`
- `POST /api/clients`
- the opaque, host-only Anonymous User Cookie and anonymous authentication DTO
- `GET /api/clients/<id>/events`
- `POST /api/clients/<id>/messages`
- a matching SSE `response` or `event`
- client disconnect
