# Research: P0 LLM Chat

## Decision: Adapt `pi-web-main` standalone path, not Pi extension or Electron paths

**Rationale**: The reference project already has the useful P0 shape: a standalone Node bridge and a Svelte browser UI. The user explicitly asked to remove `pi-extension` mode and Electron mode while keeping web standalone mode. Keeping only `packages/bridge` and `packages/svelte` gives the shortest path to a browser chat that talks to a server-side LLM runtime.

**Alternatives considered**:

- Keep Pi extension mode: rejected because P0 must be browser + server LLM, not Pi TUI extension.
- Keep Electron mode: rejected because P0 deployment target is web standalone and Docker.
- Rebuild from scratch: rejected because `pi-web-main` already contains working chat/session UI patterns and runtime wiring.

## Decision: Keep server-side runtime dependency

**Rationale**: `references/pi-web-main/packages/bridge/src/standalone/backend.ts` creates a detached agent session using the runtime dependency and exposes session actions. P0 requires the LLM to run on the server side. Keeping that runtime dependency prevents model credentials and execution state from moving into the browser.

**Alternatives considered**:

- Browser calls LLM directly: rejected because credentials would be exposed and FR-009 forbids browser-visible secrets.
- Mock-only backend: rejected because P0 needs real browser-to-server LLM communication, not only a UI demo.
- Remove runtime dependency entirely: rejected because that would remove the server-side LLM execution path.

## Decision: Configure LLM credentials through server-side environment only

**Rationale**: P0 must keep model access secrets out of the browser. Development uses a local `.env` file consumed by the server process. Docker deployment supplies the same values through environment variables or Docker secrets. The browser UI never asks for, stores, displays, or receives an LLM API key.

**Alternatives considered**:

- Browser-entered API key: rejected because it exposes secrets to browser storage and UI flows.
- Committed config file: rejected because secrets must not be versioned.
- External secret manager only: rejected for P0 because Docker env/secrets are enough and simpler to validate.

## Decision: Replace WebSocket with HTTP commands plus EventSource/SSE

**Rationale**: The current reference client creates `new WebSocket(.../ws)` and the server uses `ws` for bidirectional RPC. The user asked to change browser API access from WebSocket to EventSource. EventSource is server-to-browser only, so browser commands need normal HTTP POST endpoints while assistant deltas, completion, failures, and heartbeats flow through an SSE stream.

**Alternatives considered**:

- Keep WebSocket: rejected by explicit requirement.
- Use EventSource for both directions: rejected because browsers cannot send request bodies through EventSource.
- Use one-shot `fetch` only: rejected because it weakens streaming/progress behavior and makes reconnect/error states less visible.

## Decision: Add nginx as reverse proxy in front of the app container

**Rationale**: nginx gives the browser a single origin and production-like routing. It also lets deployment set SSE-specific proxy behavior, especially disabled buffering and long-lived read timeouts for `/api/*/events`.

**Alternatives considered**:

- Expose the Node app directly: rejected because user requested nginx reverse proxy.
- Serve static assets from nginx and API from Node: possible, but initial P0 can keep static serving in the Node app and let nginx proxy all traffic to reduce moving parts.
- Run nginx and Node in one container: rejected because Docker Compose with separate services is simpler to operate and inspect.

## Decision: Docker Compose for P0 packaging

**Rationale**: A multi-stage Dockerfile can build the TypeScript/Svelte app and produce a small runtime image for the standalone Node server. Docker Compose can pair that app image with nginx without introducing a multi-process container.

**Alternatives considered**:

- Local-only `pnpm` run mode: useful for development but not enough for the requested Docker packaging.
- Desktop packaging: rejected with Electron mode.
- NPM global package install: rejected because P0 delivery target is Docker.

## Decision: Use `dano-assistant.svg` as browser-visible product icon

**Rationale**: The provided SVG is already a standalone product icon with accessible label. It should be copied into the web client's public assets and referenced from HTML/favicon and visible app branding.

**Alternatives considered**:

- Keep pi-web default branding/icons: rejected because the product should present as Dano.
- Generate a new icon: rejected because the user supplied the icon asset.

## Decision: Validation uses five required P0 cases

**Rationale**: The spec requires at least five verification cases before readiness. These cases directly prove the chat loop, conversation continuity, validation, failure handling, and non-execution boundary.

**Alternatives considered**:

- Only run unit tests: rejected because they cannot prove the browser-to-nginx-to-server path.
- Only manually click the happy path: rejected because one case is insufficient and misses failure/non-execution risks.
