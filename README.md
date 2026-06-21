# Dano

Dano is a P0 web LLM chat app. It provides a browser UI backed by a server-side
Pi runtime, using HTTP POST for commands and EventSource/SSE for streaming
assistant events.

The browser never receives LLM credentials. API keys are loaded only by the
server process through environment variables, `.env`, or Docker-style secret
files.

## Structure

```text
packages/bridge   HTTP API, SSE event bus, and Pi runtime bridge
packages/svelte   Browser chat UI
deploy/nginx      nginx reverse proxy config for container deployment
deploy/runtime-defaults
                  Source defaults copied into the runtime workspace on startup
```

This repo intentionally does not include the old Pi extension target or
Electron target.

## Requirements

- Node.js `>=20.6.0`
- pnpm
- Docker Compose or Podman Compose
- A server-side LLM credential for the selected model provider

## Model

Default Pi settings live in
[deploy/runtime-defaults/settings.json](deploy/runtime-defaults/settings.json):

```json
{
  "defaultProvider": "xiaomi-token-plan-cn",
  "defaultModel": "mimo-v2.5",
  "defaultThinkingLevel": "high",
  "defaultProjectTrust": "always"
}
```

To switch model, edit that file and restart the server/container. The current
headless web bridge treats `/model` as normal chat text.

## System Prompt

The default assistant instruction lives in
[deploy/runtime-defaults/SYSTEM.md](deploy/runtime-defaults/SYSTEM.md). On
startup the container copies it to `/tmp/dano/.pi/SYSTEM.md` only when that
runtime file does not already exist:

- answer in a detailed and friendly tone
- proactively use tools to help users handle OA-related workflows

Edit `deploy/runtime-defaults/SYSTEM.md` to change the source default for new
runtime workspaces. Edit `/tmp/dano/.pi/SYSTEM.md` inside the persistent runtime
workspace to change an already initialized deployment.

## Credentials

Copy the example file for local runs:

```bash
cp .env.example .env
```

Set only server-side credentials in `.env`, for example:

```bash
XIAOMI_TOKEN_PLAN_CN_API_KEY=...
```

Supported environment names include:

```text
AGENT_WORLD_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
DEEPSEEK_API_KEY
GOOGLE_API_KEY
GOOGLE_GENERATIVE_AI_API_KEY
GROQ_API_KEY
OPENCODE_API_KEY
OPENROUTER_API_KEY
XAI_API_KEY
XIAOMI_API_KEY
XIAOMI_TOKEN_PLAN_CN_API_KEY
```

Docker secret-style file variables are also supported for:

```text
OPENAI_API_KEY_FILE
ANTHROPIC_API_KEY_FILE
DEEPSEEK_API_KEY_FILE
```

## Local Commands

Install dependencies:

```bash
pnpm install
```

Run checks:

```bash
pnpm run check
pnpm run test
pnpm run build
```

Run the standalone server after building:

```bash
pnpm run build
pnpm run start:bridge:standalone -- --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080
```

For Svelte-only UI development:

```bash
pnpm run dev:web
```

## Container Run

Deployment details live in [deploy/README.md](deploy/README.md).

Docker:

```bash
pnpm run deploy:up
```

Podman:

```bash
DANO_COMPOSE=podman pnpm run deploy:up
```

The nginx browser port defaults to `80`. `.env.example` uses `18082` for local
smoke runs. Override it with `DANO_NGINX_PORT`:

```bash
DANO_NGINX_PORT=18082 pnpm run deploy:up
```

Open:

```text
http://127.0.0.1:18082
```

Stop:

```bash
pnpm run deploy:stop
```

Remove the stopped containers and Compose network with `pnpm run deploy:down`.

Production servers should prefer pulling a prebuilt image instead of building
with pnpm on the target host:

```bash
DANO_IMAGE=ghcr.io/your-org/dano:latest pnpm run deploy:up
```

The published app is HTTP-only unless you put TLS in front of it. Use
`http://host/` directly, or terminate HTTPS at a reverse proxy/load balancer.

## HTTP/SSE API

Health:

```bash
curl -s http://127.0.0.1:18082/api/health
```

Create client:

```bash
curl -s -X POST http://127.0.0.1:18082/api/clients \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Open events:

```bash
curl -N http://127.0.0.1:18082/api/clients/<clientId>/events
```

Send command:

```bash
curl -s -X POST http://127.0.0.1:18082/api/clients/<clientId>/messages \
  -H 'Content-Type: application/json' \
  -d '{"type":"command","payload":{"id":"smoke-1","type":"get_state"}}'
```

SSE message types:

```text
response
event
: heartbeat
```

## UI Behavior

- Enter sends the message.
- Shift+Enter inserts a newline.
- The composer starts as one line and grows automatically for multiline input.
- Empty messages are blocked.
- Assistant and user messages render Markdown, including tables, highlighted
  fenced code blocks, and Mermaid diagrams.
- Runtime tool calls and tool results render as inline expandable blocks.
- LLM failures show a visible error state and retry control.
- Requests for business actions remain chat-only; no external workflow is
  executed by P0.

## Verification

Known-good verification used for the P0 implementation:

```bash
pnpm run check
pnpm run test
pnpm run build
DANO_NGINX_PORT=18082 pnpm run deploy:up
curl -s http://127.0.0.1:18082/api/health
DANO_SMOKE_BASE_URL=http://127.0.0.1:18082 pnpm run smoke:deploy
```

Expected health response:

```json
{"status":"ok"}
```

The deployment path supports Docker Compose and Podman Compose. For production,
prefer a prebuilt image through `DANO_IMAGE` so the target server does not build
with pnpm.
