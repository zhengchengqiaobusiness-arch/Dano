# Dano Project Instructions

This project is the Dano P0 browser-only LLM chat app.

## Project Context

- Frontend: Svelte 5 browser client built by Vite under `apps/dano/web`.
- Backend: TypeScript/Node app under `apps/dano`, with bridge capabilities in `apps/dano/src/bridge`.
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
- Server build only: `pnpm run build:server`
- Server dev: `pnpm run dev:server`
- Svelte dev server: `pnpm run dev:web`
- Built backend: `pnpm run start`
- Do not set the current project checkout as the Dano runtime directory. Use a separate runtime/workspace path so generated `.dano`, `.pi`, `uploads`, session, and upload files do not land in the repo checkout.

If the shell cannot find `pnpm` or `node`, use the Codex bundled Node runtime by prepending:

```sh
PATH=/Users/joseph/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH
```

## Validation

- Use `pnpm run check` for type and Svelte diagnostics.
- Use `pnpm test` for Vitest coverage.
- Use `pnpm run build` before validating the built server.
- For UI changes, verify the rendered app in a browser against the relevant flow and capture a screenshot as validation evidence.
- For browser validation, use `agent-browser tab new <url>` directly. Do not pass `--auto-connect` or `--cdp 9222`; this repo's agent-browser setup already auto-connects to the persistent Chrome for Testing instance.
- For Podman/deploy/runtime/Heimdall/bash/upload validation, `smoke:deploy` alone is not enough. Also verify in a browser: plain text chat, image upload with model read/description, and a model-triggered `bash ls` tool call.
- After Podman-based deployment or smoke tests, stop and remove the test containers and pods, then remove Dano temporary images/tags and dangling build layers after confirming no containers reference them; keep reusable base images unless explicitly asked.

## GitHub Workflow

- Each time an issue is solved and verified, create a pull request to `upstream`.
- After a pull request merges successfully, delete the remote PR branch by default.
- Before updating the server deployment, switch to `main` and run `git sync-upstream`.

## Versioning

- Treat the root `package.json` version as Dano's only product version.
- Bump the root `package.json` patch version (`A.B.x`) when a PR changes shipped runtime behavior, deployment output, or user-visible functionality.
- Do not bump the product version for docs-only, tests-only, comment-only, or `AGENTS.md`-only changes.
- Do not rely on `AGENTS.md` for runtime model behavior; runtime-facing version behavior belongs in server code, runtime defaults, or tool prompt metadata.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues; external PRs are not a triage request surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-label triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Use the repo's multi-context domain docs. See `docs/agents/domain.md`.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/001-llm-chat/plan.md
<!-- SPECKIT END -->
