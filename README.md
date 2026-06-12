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
.pi/settings.json Default Pi model settings copied into the container
.pi/SYSTEM.md     Custom Dano system prompt copied into the Pi runtime
```

This repo intentionally does not include the old Pi extension target or
Electron target.

## Requirements

- Node.js `>=20.6.0`
- pnpm
- Docker Compose or Podman Compose
- A server-side LLM credential for the selected model provider

## Model

Default Pi settings live in [.pi/settings.json](.pi/settings.json):

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

The default assistant instruction lives in [.pi/SYSTEM.md](.pi/SYSTEM.md). It is
copied into the runtime settings directory so Dano starts with the project
system prompt:

- answer in a detailed and friendly tone
- proactively use tools to help users handle OA-related workflows

Edit `.pi/SYSTEM.md` and restart the server/container to change the system
prompt.

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

Docker:

```bash
docker compose --env-file .env up --build
```

Podman:

```bash
podman compose --env-file .env up --build
```

The nginx browser port defaults to `80`. Override it with
`DANO_NGINX_PORT`:

```bash
DANO_NGINX_PORT=18082 podman compose --env-file .env up --build
```

Open:

```text
http://127.0.0.1:18082
```

Stop:

```bash
DANO_NGINX_PORT=18082 podman compose down
```

## HTTP/SSE API

Health:

```bash
curl -s http://127.0.0.1:18082/api/health
```

Create conversation:

```bash
curl -s -X POST http://127.0.0.1:18082/api/conversations \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Open events:

```bash
curl -N http://127.0.0.1:18082/api/conversations/<conversationId>/events
```

Send message:

```bash
curl -s -X POST http://127.0.0.1:18082/api/conversations/<conversationId>/messages \
  -H 'Content-Type: application/json' \
  -d '{"clientMessageId":"smoke-1","text":"Hello, introduce yourself briefly."}'
```

Retry a failed message:

```bash
curl -s -X POST http://127.0.0.1:18082/api/conversations/<conversationId>/messages/<messageId>/retry \
  -H 'Content-Type: application/json' \
  -d '{}'
```

SSE event names:

```text
conversation.ready
message.accepted
assistant.started
assistant.delta
assistant.blocks
assistant.completed
message.failed
heartbeat
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
DANO_NGINX_PORT=18082 podman compose --env-file .env up --build -d
curl -s http://127.0.0.1:18082/api/health
```

Expected health response:

```json
{"status":"ok"}
```

The implementation was verified with Podman Compose. Docker Compose should use
the same compose file, but Docker CLI was not available in the local test
environment.
