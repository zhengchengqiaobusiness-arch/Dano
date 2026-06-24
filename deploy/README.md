# Dano Deployment

This directory contains deployment-specific defaults and proxy config.

## Runtime Layout

- Source runtime defaults live in `deploy/runtime-defaults/`.
- The runtime compatibility directory is `$DANO_DEFAULT_WORKSPACE_PATH/.pi`.
- The default workspace is `/tmp/dano`.
- Docker Compose mounts `./runtime-data:/tmp/dano`, so runtime sessions and
  user-modified `.pi` files survive container recreation.

On container startup, `deploy/docker-entrypoint.sh` creates:

```text
/tmp/dano/.pi/SYSTEM.md
/tmp/dano/.pi/settings.json
/tmp/dano/.pi/heimdall.json
```

The entrypoint copies those files from `deploy/runtime-defaults/` only when the
runtime file is missing. It does not overwrite user-modified runtime files.

The app service grants `SYS_ADMIN` and disables its outer seccomp profile so
Heimdall can create the nested Bubblewrap namespace used by guarded bash calls.

## Local Compose Run

```bash
cp .env.example .env
DANO_NGINX_PORT=18082 pnpm run deploy:up
DANO_SMOKE_BASE_URL=http://127.0.0.1:18082 pnpm run smoke:deploy
pnpm run deploy:logs
pnpm run deploy:stop
pnpm run deploy:down
```

`deploy:stop` preserves containers and runtime data. `deploy:down` removes the
containers and Compose network; bind-mounted `runtime-data/` remains intact.

The app container listens on `8080`; nginx publishes `${DANO_NGINX_PORT:-80}`.

## Production Server Run

Prefer pulling a prebuilt image instead of building with pnpm on the target
host:

```bash
DANO_IMAGE=ghcr.io/your-org/dano:latest pnpm run deploy:up
```

When `DANO_IMAGE` is set, `scripts/deploy-compose.mjs` pulls the app image and
runs Compose with `--no-build`.

If building on the target host is unavoidable:

```bash
pnpm run deploy:up
```

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

Do not commit `.env`, `runtime-data/`, or `.secrets/`.

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
chmod 600 .secrets/openai_api_key
OPENAI_API_KEY_FILE=/run/secrets/openai_api_key pnpm run deploy:up
```

Compose mounts `./.secrets:/run/secrets:ro`.

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
