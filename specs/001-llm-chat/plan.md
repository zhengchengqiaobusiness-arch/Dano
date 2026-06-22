# Implementation Plan: P0 LLM Chat

**Branch**: `001-llm-chat` | **Date**: 2026-06-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-llm-chat/spec.md`

## Summary

Adapt `references/pi-web-main/` into a browser-only Dano assistant that keeps the standalone web runtime and server-side LLM session path, removes Pi extension mode and Electron mode, and replaces browser WebSocket RPC with HTTP command endpoints plus EventSource streaming. Package the resulting app with Docker and place nginx in front as the browser-facing reverse proxy. Use `references/dano-assistant.svg` as the product icon.

## Technical Context

**Language/Version**: TypeScript on Node.js `>=20.6.0`; Svelte 5 browser client built by Vite.

**Primary Dependencies**: Keep the server-side agent runtime dependency used by `pi-web-main`; keep Svelte/Vite client dependencies; remove Pi extension package dependencies and Electron packaging/runtime dependencies from the target app. The browser transport target is EventSource/SSE, not WebSocket. LLM access credentials are loaded only from server-side environment configuration: local `.env` for development and Docker environment variables or Docker secrets for container deployment.

**Storage**: No database for P0. Conversation state is process/runtime session state only; long-term conversation persistence is outside the P0 spec.

**Testing**: Vitest for server/client unit and integration tests; Svelte check/type check for UI compile correctness; Docker smoke validation through nginx; manual browser validation for the five P0 scenarios.

**Target Platform**: Modern desktop browser, Node.js app container, nginx reverse proxy container.

**Project Type**: Web application with browser frontend, Node.js standalone backend, Docker deployment.

**Performance Goals**: First visible processing feedback after send within 1 second on a healthy local deployment; normal messages produce a visible LLM answer or clear failure within 30 seconds in at least 95% of controlled P0 verification attempts.

**Constraints**: P0 is chat-only; no enterprise form submission, approval, business record creation, Pi extension mode, or Electron desktop mode. Browser must not receive, accept, persist, or display model credentials or runtime secrets. Browser-to-server event stream must use EventSource/SSE rather than WebSocket.

**Scale/Scope**: Single standalone web app for P0 pilot validation; one active browser user/session is sufficient for P0 acceptance, with implementation not intentionally blocking additional browser clients.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

PASS. `.specify/memory/constitution.md` still contains placeholder principles only, so there are no enforceable project-specific gates. General project instructions still apply: read before writing, surgical changes, simple design, and at least five validation cases.

## Project Structure

### Documentation (this feature)

```text
specs/001-llm-chat/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── http-sse.md
└── tasks.md
```

### Source Code (repository root)

```text
package.json
pnpm-lock.yaml
pnpm-workspace.yaml
Dockerfile
docker-compose.yml

deploy/
└── nginx/
    └── default.conf

apps/
└── dano/
    ├── package.json
    ├── tsconfig.json
    ├── tsdown.config.ts
    └── src/
        ├── __tests__/
        ├── backend.ts
        ├── dev-reload.ts
        ├── main.ts
        ├── runtime.ts
        ├── runtime-entry.ts
        └── server.ts

packages/
├── bridge/
│   ├── package.json
│   └── src/
│       ├── server.ts
│       ├── sse-event-bus.ts
│       ├── http-command-adapter.ts
│       ├── credential-config.ts
│       └── __tests__/
└── svelte/
    ├── package.json
    ├── public/
    │   └── dano-assistant.svg
    └── src/
        ├── App.svelte
        ├── composables/
        │   └── bridgeStore.svelte.ts
        └── components/

web-dist/
dist/
```

**Structure Decision**: Keep the reusable bridge and Svelte client under `packages/`, and place the runnable standalone backend in `apps/dano`. Target code removes `packages/bin/`, `packages/electron/`, Pi extension registration, Electron scripts, Electron dependencies, and WebSocket transport. Target code adds nginx deployment files, Docker packaging, and EventSource-compatible HTTP/SSE bridge endpoints.

## Phase 0: Research

See [research.md](./research.md).

## Phase 1: Design & Contracts

See [data-model.md](./data-model.md), [contracts/http-sse.md](./contracts/http-sse.md), and [quickstart.md](./quickstart.md).

## Post-Design Constitution Check

PASS. Design keeps scope narrow, avoids speculative business workflow work, defines five P0 validation cases, and removes unused Pi extension/Electron paths rather than preserving dead modes.

## Complexity Tracking

No constitution violations.
