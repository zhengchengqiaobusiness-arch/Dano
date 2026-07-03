# Dano Deployment

This directory contains deployment-specific defaults and proxy config.

## Runtime Layout

- Source runtime defaults live in `deploy/runtime-defaults/`.
- The runtime root is `${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}`.
- The Pi agent config directory is
  `${PI_CODING_AGENT_DIR:-$DANO_RUNTIME_DIR/default-settings/.pi/agent}`.
- Production deployment keeps three directories separate:
  - `/tmp/dano-build-*` is the disposable source checkout and image build dir.
  - `/opt/dano/deploy` stores Compose, `.env`, secrets, and nginx config.
  - `/opt/dano/runtime-data` is mounted at `/opt/dano/runtime-data` for runtime state.
- Docker Compose mounts
  `${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}:/opt/dano/runtime-data`.
  That host directory must be writable by UID/GID `1000:1000`, so runtime
  sessions and user-modified agent config files survive container recreation
  without writing into a source checkout.

On container startup, `deploy/docker-entrypoint.sh` creates:

```text
/opt/dano/runtime-data/default-settings/.pi/agent/SYSTEM.md
/opt/dano/runtime-data/default-settings/.pi/agent/settings.json
/opt/dano/runtime-data/default-settings/.pi/agent/heimdall.json
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
container's existing `/dev` and `/proc` instead of asking Bubblewrap to mount
nested kernel filesystems. It sets `HEIMDALL_BWRAP_BIND_ROOT=/opt/dano` because
non-root Bubblewrap cannot remount the bind-mounted
`/opt/dano/runtime-data` subtree directly, while binding the container-owned
parent keeps Runtime Workspace paths usable.

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

For local runs, point `DANO_RUNTIME_DIR`, `DANO_NGINX_CONF`, and
`DANO_SECRETS_DIR` in `.env` at local host paths. The app container still uses
`/opt/dano/runtime-data` internally; the host `DANO_RUNTIME_DIR` only selects
what is mounted there. `deploy:up` runs Compose with
`--no-build`; build the image first or use `deploy:release`. `deploy:stop`
preserves containers and runtime data. `deploy:down` removes the containers and
Compose network; the bind-mounted runtime directory remains intact.

The app container listens on `8080`; nginx publishes `${DANO_NGINX_PORT:-80}`.

### Full Local Podman Acceptance

For changes that affect model runtime, uploads, Heimdall, bash, container
permissions, or runtime directories, `smoke:deploy` is not enough. Run this
minimum acceptance sequence against the Podman Compose deployment:

1. Build and start the image with Podman Compose.
2. Run `smoke:deploy` against nginx.
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
   pnpm run deploy:check-bash -- /path/to/runtime-data/workspaces/<workspace>/.dano/sessions/<session>.jsonl
   ```

   It reports whether a `bash` tool call occurred, whether a successful
   `DANO_BASH_OK` tool result was recorded, and whether any `bwrap` error text
   appeared in session JSONL.

   For diagnostics only, scan the full mounted runtime directory explicitly:

   ```bash
   DANO_RUNTIME_DIR=/path/to/runtime-data DANO_BASH_ACCEPTANCE_SCAN_ALL=1 pnpm run deploy:check-bash
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
DANO_IMAGE=dano-app:local docker compose --env-file .env up -d --no-build
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

This Compose deployment is HTTP-only. Open it with:

```text
http://host/
```

For HTTPS, terminate TLS in front of Dano with a reverse proxy, load balancer,
or CDN. Point that proxy at the nginx service on port `80`.

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
