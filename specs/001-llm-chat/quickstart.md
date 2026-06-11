# Quickstart: P0 LLM Chat Validation

## Prerequisites

- Node.js `>=20.6.0`
- pnpm
- Docker with Docker Compose
- Server-side LLM runtime credentials configured only through local `.env`, Docker environment variables, or Docker secrets

## Local Build Checks

```bash
pnpm install
pnpm run check
pnpm run test
pnpm run build
```

Expected result: type checks, tests, and production build all complete without errors.

## Server-Side Credential Configuration

Local development:

```bash
cp .env.example .env
```

Edit `.env` with the required server-side LLM provider settings. The browser UI must not expose fields for API keys.

Docker deployment:

```bash
docker compose --env-file .env up --build
```

Expected result: the app container receives model credentials from server-side environment configuration only. Browser-visible HTML, JavaScript config, and network responses contain no LLM API keys or model access secrets.

## Docker Smoke Run

```bash
docker compose up --build
```

Expected result:

- nginx listens on the configured browser port.
- `GET /api/health` returns `{"status":"ok"}` through nginx.
- Browser can load the Dano web UI.
- The UI uses `dano-assistant.svg` as the visible product icon or favicon.

## EventSource Contract Smoke

Create a conversation:

```bash
curl -s -X POST http://localhost/api/conversations \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Open the event stream in another terminal:

```bash
curl -N http://localhost/api/conversations/<conversationId>/events
```

Send a message:

```bash
curl -s -X POST http://localhost/api/conversations/<conversationId>/messages \
  -H 'Content-Type: application/json' \
  -d '{"clientMessageId":"smoke-1","text":"Hello, introduce yourself briefly."}'
```

Expected result: the stream emits `message.accepted`, `assistant.started`, one or more `assistant.delta` events, and either `assistant.completed` or `message.failed` within 30 seconds.

## Required P0 Validation Cases

1. **Normal first message**: Open browser UI, send a normal message, confirm the user message and assistant answer appear in order.
2. **Multi-turn follow-up**: Send a follow-up referring to the prior answer, confirm the new exchange appends to the same conversation.
3. **Empty input**: Try to send whitespace only, confirm the browser blocks submission or shows immediate validation.
4. **Failed LLM response**: Run with invalid or unavailable server-side LLM runtime credentials, send a message, confirm a visible failure state appears and prior messages remain visible.
5. **Business-action request boundary**: Ask the assistant to submit leave, approve a workflow, or create a business record. Confirm no enterprise form, approval, record, or external workflow is executed.
6. **Secret exposure check**: Inspect browser-visible HTML, JavaScript config, and network responses. Confirm no model credentials or service secrets are visible.
7. **Credential config check**: Change the server-side LLM credential configuration, restart the server, and confirm model access behavior changes without any browser-side secret input.

## Ready Criteria

P0 is ready for implementation review when all build checks pass, Docker smoke run succeeds through nginx, and all required validation cases produce the expected visible outcomes.
