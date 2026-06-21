# Dano Project Instructions

This project is the Dano P0 browser-only LLM chat app.

## Project Context

- Frontend: Svelte 5 browser client built by Vite under `packages/svelte`.
- Backend: TypeScript/Node standalone bridge under `packages/bridge`.
- Origin: this project is derived from `references/pi-web-main/` and customized for the Dano browser-only chat use case.
- Runtime target: browser frontend + Node backend + optional nginx/container deployment.
- P0 intentionally excludes Pi extension mode and Electron mode.
- Browser transport uses HTTP command endpoints plus EventSource/SSE, not WebSocket.
- Model credentials and runtime secrets are server-side only. Do not expose them to the browser.
- Conversation state is process/runtime session state only; no database is used for P0.

## Common Commands

- Install dependencies: `pnpm install`
- Full check: `pnpm run check`
- Type check only: `pnpm run check:type`
- Svelte check only: `pnpm run check:web`
- Unit/integration tests: `pnpm test`
- Full build: `pnpm run build`
- Web build only: `pnpm run build:web`
- Bridge build only: `pnpm run build:bridge`
- Standalone backend dev: `pnpm run dev:bridge:standalone`
- Svelte dev server: `pnpm run dev:web`
- Built standalone backend: `pnpm run start:bridge:standalone`

If the shell cannot find `pnpm` or `node`, use the Codex bundled Node runtime by prepending:

```sh
PATH=/Users/joseph/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH
```

## Validation

- Use `pnpm run check` for type and Svelte diagnostics.
- Use `pnpm test` for Vitest coverage.
- Use `pnpm run build` before validating the built standalone server.
- For UI changes, verify the rendered app in a browser against the relevant flow.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/001-llm-chat/plan.md
<!-- SPECKIT END -->
