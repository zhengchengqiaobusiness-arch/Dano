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

## Implementation rules

- Do not hardcode. Never complete a specific case by writing variable values,
  decisions, or behavior directly into the implementation.

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
- When browser validation temporarily changes the theme, record the initial theme before testing and restore it before handoff. Do not leave a test theme preference in the user's browser, including when the current UI does not expose the theme selector.
- For Podman/deploy/runtime/Heimdall/bash/upload validation, `smoke:deploy` alone is not enough. Also verify in a browser: plain text chat, image upload with model read/description, and a model-triggered `bash ls` tool call.
- If `podman ps` works but `podman compose` or `podman machine list` fails with `podman-machine-default.lock: operation not permitted` or `could not find a matching machine`, treat it as local Podman machine metadata being blocked by the sandbox, not a Dano bug. Re-run the same Compose command outside the sandbox/escalated instead of changing Dano code.
- After Podman-based deployment or smoke tests, stop and remove the test containers and pods, then remove Dano temporary images/tags and dangling build layers after confirming no containers reference them; keep reusable base images unless explicitly asked.

## Model tool argument compatibility

- Treat model-generated tool arguments as best-effort input. Normalize supported aliases and safely coercible value types when the intended behavior remains unambiguous.
- Silently ignore or default unknown, misplaced, or malformed optional arguments when doing so does not prevent the requested capability from rendering, executing, or returning the correct result. Do not reject the tool call or trigger a model retry solely for those non-functional errors.
- Keep strict validation only when recovery would be ambiguous or could cause an incorrect submission, incorrect field mapping, data loss, or another materially wrong result.
- Encode runtime behavior in parser code, runtime defaults, tool prompt metadata, and tests; do not rely on this file alone to enforce model-facing behavior.
- When adding or changing a collection- or object-shaped model parameter, update the executable compatibility matrix and the sanitized captured model-deviation fixtures that exercise it. A schema, prompt, or prose-only change is not complete evidence.
- Review such changes with [the model argument compatibility checklist](docs/agents/model-argument-compatibility-review.md), including canonical input, safe JSON strings, aliases, malformed or ambiguous input, partial-valid input, fallback, isolation/leakage, and the canonical browser projection.

## Frontend component library

- Frontend feature components must use the project's shadcn-svelte components
  through `apps/dano/web/src/components/ui`. Do not import `bits-ui` directly
  from feature components; low-level primitive imports belong only inside the
  shadcn component wrappers under `components/ui`.

## Tool activity presentation

- Every new non-interactive tool must define user-facing Activity Trail copy for
  its pending, completed, and failed states. Until that copy exists, use the
  generic task-processing fallback and never expose the raw tool name.
- Treat the collapsed summary and expanded details as user-facing product UI.
  Do not expose complete paths or URLs, command arguments, full commands,
  scripts, code, raw output, or other implementation details during normal tool
  activity. For conservatively recognized simple Bash command lists, details may
  identify only executable basenames with copy such as `执行了 <name> 命令`; strip
  directories and arguments. Complex or ambiguous Shell syntax must use a generic
  localized script detail instead of guessing command names. For an unresolved
  failure that has no reliable user-facing explanation, keep the collapsed row
  non-technical and allow the expanded details to show the original failure
  information instead of inventing a classification.
- Keep tools that require user input, such as `ask_user_question`, in their
  dedicated interactive presentation. Their unresolved invocation failures may
  use the Activity Trail with the tool's matching icon and user-facing failure
  copy; hide transient failures after a successful retry.

## GitHub Workflow

- Run every `gh` command outside the sandbox with escalated permissions, using
  `/opt/homebrew/bin/gh` explicitly. Do not first retry `gh` through the sandbox
  or rely on the shell `PATH` to find it.
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
