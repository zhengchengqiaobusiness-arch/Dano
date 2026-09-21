# Dano Project Instructions

This project is the Dano P0 browser-only LLM chat app.

## Project Context

- Frontend: Svelte 5 browser client built by Vite under `apps/dano/web`.
- Backend: TypeScript/Node app under `apps/dano`, with bridge capabilities in `apps/dano/src/bridge`.
- Origin: this project was derived from [woxQAQ/pi-web](https://github.com/woxQAQ/pi-web) and customized for the Dano browser-only chat use case.
- Runtime target: browser frontend + Node backend + optional nginx/container deployment.
- P0 intentionally excludes Pi extension mode and Electron mode.
- Browser transport uses HTTP command endpoints plus EventSource/SSE, not WebSocket.
- Model credentials and runtime secrets are server-side only. Do not expose them to the browser.
- Conversation state is process/runtime session state only; no database is used for P0.

## Implementation rules

- Do not hardcode. Never complete a specific case by writing variable values,
  decisions, or behavior directly into the implementation.

## Code review

- When invoking the `code-review` skill without a user-specified fixed point,
  use `upstream/main`; an explicitly supplied fixed point takes precedence.
- When an implementation under review looks ad hoc, suspiciously fragile, or
  vulnerability-prone, do not accept it at face value. Ask whether an
  industry-standard solution or mature open-source implementation already
  exists, compare the custom approach against those alternatives, and require
  a clear justification before accepting a custom implementation.

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
- Do not add or run repository scripts that launch system Chrome or Chromium headlessly for browser acceptance. Keep deterministic logic and component behavior in Vitest, and use the Codex in-app Browser for rendered interaction and visual evidence.
- When browser validation temporarily changes the theme, record the initial theme before testing and restore it before handoff. Do not leave a test theme preference in the user's browser, including when the current UI does not expose the theme selector.
- For Podman/deploy/runtime/Heimdall/bash/upload validation, `smoke:deploy` alone is not enough. Also verify in a browser: plain text chat, image upload with model read/description, and a model-triggered `bash ls` tool call.
- If `podman ps` works but `podman compose` or `podman machine list` fails with `podman-machine-default.lock: operation not permitted` or `could not find a matching machine`, treat it as local Podman machine metadata being blocked by the sandbox, not a Dano bug. Re-run the same Compose command outside the sandbox/escalated instead of changing Dano code.
- After Podman-based deployment or smoke tests, stop and remove the test containers and pods, then remove Dano temporary images/tags and dangling build layers after confirming no containers reference them; keep reusable base images unless explicitly asked.

### 镜像验收入口、上传与审批

- 本地或 SSH 隧道访问的隔离镜像验收统一使用 `http://localhost:18710` 和
  `https://localhost:18711`，只绑定回环地址；重建镜像或容器时保持浏览器入口不变。
  本约定不改变开发服务器和生产服务的端口。
- 本机 HTTPS 验收复用 `~/.local/share/dano/localhost-tls/localhost.pem`
  和 `localhost-key.pem`，由同目录已信任的 `rootCA.pem` 签发；证书、私钥和
  CA 均为持久资产，排除在临时验收清理之外。启动前验证有效期、localhost SAN
  和系统信任；已有有效证书时直接复用，不因新任务或重启重新生成或要求信任。
  叶证书到期时沿用原 CA 续签，不更换 CA；确实缺失 CA 或信任时才处理首次配置。
  Node/Python 等独立信任库的检查使用该 CA，不关闭 TLS 校验。
- 启动前检查端口占用及所属任务。仅复用已确认属于本次验收的服务；其他任务占用时
  优先协调或串行验收，不擅自终止进程或随机换端口。确需更换时提前说明原因和新入口。
- 使用固定、无敏感信息的合成测试图片，放在代码仓库外的专用验收素材目录，并复用
  浏览器验收标签页。同一轮验收尽量只上传一次；仅在用例要求、上传失败或会话隔离
  必须重新上传时重复操作。图片上传验收仍须包含真实上传和模型识别。
- 验收开始前明确容器、端口、隧道、素材绝对路径、上传目标和清理范围。对确实需要
  审批且能提前申请的操作，先准备具体可审阅的操作，再集中提交审批；沿用会话内已获
  授权的范围，避免重复询问。不把每轮验收本身额外变成一次审批。
- 若工具强制逐次审批上传，提前告知该限制，在调用时遵守审批机制；固定入口和复用
  素材不代表免审批。执行中发现无法预见的新权限需求时，说明原因，仅暂停依赖该权限
  的步骤，继续完成其他已授权工作。

## Model tool argument compatibility

- Treat model-generated tool arguments as best-effort input. Normalize supported aliases and safely coercible value types when the intended behavior remains unambiguous.
- Silently ignore or default unknown, misplaced, or malformed optional arguments when doing so does not prevent the requested capability from rendering, executing, or returning the correct result. Do not reject the tool call or trigger a model retry solely for those non-functional errors.
- Keep strict validation only when recovery would be ambiguous or could cause an incorrect submission, incorrect field mapping, data loss, or another materially wrong result.
- Encode runtime behavior in parser code, runtime defaults, tool prompt metadata, and tests; do not rely on this file alone to enforce model-facing behavior.
- When adding or changing a collection- or object-shaped model parameter, update the executable compatibility matrix and the sanitized captured model-deviation fixtures that exercise it. A schema, prompt, or prose-only change is not complete evidence.
- Review such changes with [the model argument compatibility checklist](docs/agents/model-argument-compatibility-review.md), including canonical input, safe JSON strings, aliases, malformed or ambiguous input, partial-valid input, fallback, isolation/leakage, and the canonical browser projection.

## `ask_user_question` model guide

- Any change to `ask_user_question` capabilities, intended use, `description`,
  `promptSnippet`, `promptGuidelines`, canonical parameters, JSON Schema,
  defaults, validation, controls, field configuration, `dataSource`, results,
  error codes, retry policy, confirmation lifecycle, or canonical Card Request
  projection must check and update
  `docs/skill-generator-ask-user-question-guide.md` in the same change.
- Keep the guide's backend Skill-generator examples and capability coverage
  matrix synchronized with the implementation. Every documented capability
  must remain linked to at least one executable example.
- If an `ask_user_question` change does not require a guide update, state that
  review conclusion explicitly in the pull request validation notes.

## Frontend component library

- Frontend feature components must use the project's shadcn-svelte components
  through `apps/dano/web/src/components/ui`. Do not import `bits-ui` directly
  from feature components; low-level primitive imports belong only inside the
  shadcn component wrappers under `components/ui`.

## GitHub Workflow

- Run every `gh` command outside the sandbox with escalated permissions, using
  `/opt/homebrew/bin/gh` explicitly. Do not first retry `gh` through the sandbox
  or rely on the shell `PATH` to find it.
- Before creating any Dano worktree, use the primary checkout to run
  `git sync-upstream` and verify that `main`, `origin/main`, and `upstream/main`
  point to the same commit.
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
