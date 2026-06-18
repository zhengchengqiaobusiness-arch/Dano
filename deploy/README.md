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
```

The entrypoint copies those files from `deploy/runtime-defaults/` only when the
runtime file is missing. It does not overwrite user-modified runtime files.

## Local Compose Run

```bash
cp .env.example .env
DANO_NGINX_PORT=18082 pnpm run deploy:up
DANO_SMOKE_BASE_URL=http://127.0.0.1:18082 pnpm run smoke:deploy
pnpm run deploy:logs
pnpm run deploy:down
```

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

CentOS 7 and older Docker storage stacks may have pnpm/Corepack write issues
during Docker build. A CI-built image avoids that entire class of failures.

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
