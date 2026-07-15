# Dano Deployment

This directory contains deployment-specific defaults and proxy config.

## Runtime Layout

- Source runtime defaults live in `deploy/runtime-defaults/`.
- The runtime root is `${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}`.
- The Pi agent config directory is
  `${PI_CODING_AGENT_DIR:-$DANO_RUNTIME_DIR/.pi/agent}`.
- Runtime skills stay under `/opt/dano/runtime-data/.agents/skills`.
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

The entrypoint copies those files from `deploy/runtime-defaults/` only when the
runtime file is missing. It does not overwrite user-modified runtime files.
It does not copy defaults into a Runtime Workspace `.pi` directory.

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

```bash
cp .env.example .env
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

Do not use a plain `podman run` as a Compose-equivalent secret test. Compose
loads `.env` and passes variables such as `XIAOMI_TOKEN_PLAN_CN_API_KEY`; a
manual `podman run` only receives the environment values explicitly passed with
`-e`, so it can produce a false `No API key found` error.

## Production Server Run

The release script builds from a temporary source checkout, copies only deploy
inputs to `/opt/dano/deploy`, starts the prebuilt image, runs the smoke test,
and removes `/tmp/dano-build-*` even when a step fails:

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

The default is `http`, so existing deployments do not gain a TLS port or
certificate requirement after upgrading.

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
- `GET /api/clients/<id>/events`
- `POST /api/clients/<id>/messages`
- a matching SSE `response` or `event`
- client disconnect
