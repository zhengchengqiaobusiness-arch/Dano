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

### Local development lifecycle

For browser validation, start the backend and frontend in separate terminals with
these exact commands:

```sh
DANO_RUNTIME_DIR="$(mktemp -d /private/tmp/dano-runtime.XXXXXX)" pnpm run dev:server
```

```sh
pnpm run dev:web
```

- Open `http://localhost:5173` in the Codex in-app Browser. The Vite dev server
  proxies `/api` to the backend on `http://localhost:8080`.
- Do not append `--host` or replace `localhost` with `127.0.0.1` unless the
  standard command has failed and the actual listening address has been checked.
- Stop both dev processes when validation is complete. Remove only the temporary
  runtime directory created for that validation run after confirming the backend
  has stopped.

If the shell cannot find `pnpm` or `node`, use the Codex bundled Node runtime by prepending:

```sh
PATH=/Users/joseph/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH
```

## Validation

- Use `pnpm run check` for type and Svelte diagnostics.
- Use `pnpm test` for Vitest coverage.
- Use `pnpm run build` before validating the built server.
- For UI changes, verify the rendered app in a browser against the relevant flow and capture a screenshot as validation evidence.
- For browser validation, use the Codex in-app Browser against the relevant flow. Use another browser surface only when the user explicitly requests it or the in-app Browser cannot exercise the required flow.
- For Podman/deploy/runtime/Heimdall/bash/upload validation, `smoke:deploy` alone is not enough. Also verify in a browser: plain text chat, image upload with model read/description, and a model-triggered `bash ls` tool call.
- If `podman ps` works but `podman compose` or `podman machine list` fails with `podman-machine-default.lock: operation not permitted` or `could not find a matching machine`, treat it as local Podman machine metadata being blocked by the sandbox, not a Dano bug. Re-run the same Compose command outside the sandbox/escalated instead of changing Dano code.
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
- Before Dano reaches `1.0`, do not add compatibility migrations for old runtime layouts unless the user explicitly asks for them; prefer updating the shipped default layout directly.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues; external PRs are not a triage request surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-label triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Use the repo's multi-context domain docs. See `docs/agents/domain.md`.
