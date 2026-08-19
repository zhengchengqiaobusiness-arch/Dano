# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend (run from `back/`)

```bash
# Start dev server
uvicorn dano.gateway.app:app --host 127.0.0.1 --port 8077 --ws-max-queue 2048

# Run all tests
python -m pytest

# Run a single test file
python -m pytest tests/test_recording_live_agent.py -v

# Lint
ruff check .
ruff format .
```

### Frontend (run from `skillfrontend/`)

```bash
npm install
npm run dev          # Vite dev server on :5173, proxies API to :8077
npm run build
```

Override backend address: `DANO_GATEWAY=http://host:port npm run dev`

### Full stack (Windows)

Double-click `start-dano.bat` — clears ports, purges caches, starts both services, opens the browser.

### Node sidecar (run from `back/agent/`)

```bash
npm install
npx vitest run       # unit tests for pi-SDK bridge
```

## Environment

Copy `back/.env.example` to `back/.env`. Key variables:

| Variable | Purpose |
|---|---|
| `DANO_PG_DSN` | asyncpg DSN (default: `postgresql://postgres:111111@localhost:5432/dano_back`) |
| `DANO_PI_API_KEY` | LLM API key for the pi agent |
| `DANO_PI_BASE_URL` | OpenAI-compatible endpoint |
| `DANO_PI_MODEL` | Model name (default: `mimo-v2.5`) |
| `DANO_RUNTIME_CREDENTIALS` | JSON dict of runtime auth tokens (dev only) |
| `DANO_INSECURE_TLS` | Disable TLS cert verification (dev/self-signed only) |

Database schema is managed by hand via `back/migrations/001_init_assets.sql` … `017_tenant_password.sql` — no Alembic. Run new migration files manually in order.

## Architecture

### Three-tier structure

```
skillfrontend/   React 18 + Ant Design admin UI (Vite)
back/            Python backend (FastAPI + asyncpg + uvicorn)
back/agent/      Node.js pi-SDK sidecar (pi agent bridge)
```

The single FastAPI entry point is `back/dano/gateway/app.py`. All routes, startup singletons, and the WebSocket recording gateway live there.

### Tenant isolation

Every `/v1/*` route requires an `x-tenant-key` header. The registry (`InMemoryRegistry` in dev, `PgRegistry` in production) is swapped in the lifespan block of `app.py`.

### Two onboarding paths

**Swagger-based** (`POST /onboarding/*`): user supplies an OpenAPI/Swagger doc. The backend classifies endpoints, generates a FlowSpec, runs sandbox tests, and publishes.

**Page recording** (`WS /onboarding/page/record`): a browser recording session streams events over WebSocket. The pi agent (LLM, running in the Node sidecar) annotates the recording in real time by calling tools defined in `recording_live.py`. After recording, `CanonicalRecordingRuntime` (`onboarding/recording_pipeline.py`) runs: `prepare →` persist stage-six result `→` optional `check → repair → publish`. The eight-stage Skill/code split is documented in [`doc/recording-pipeline.md`](doc/recording-pipeline.md).

### The pi agent and its tool contract

The pi agent (LLM) does **semantic annotation only**. All deterministic, side-effectful work is executed by Python tool implementations in `back/dano/agent_tools/tools.py`. Hard invariants:

- `sandbox_test` / `write_readback` / `health_check` always use `environment=sandbox` — they never touch production.
- `publish_asset` calls `verify_publishable` and only accepts backend-generated evidence; the agent cannot self-report a pass.
- Credentials live in `MaterialContext` (process-internal). They are injected at call time and **never** enter the LLM context or logs.

### FlowSpec DSL

`back/dano/execution/page/flow_spec.py` (~1 MB) is the core data model. A `FlowSpec` describes a recorded or synthesised workflow: goals, steps, parameters (with source/type/enum annotations), dependencies between reads and writes, and verification bindings. `recording_live.py` defines the semantic ops the pi agent calls to annotate a live recording into a FlowSpec.

### Asset lifecycle

Assets (skills, capabilities, drafts) move through a state machine defined in `back/dano/lifecycle/state_machine.py`. `InMemoryLifecycleOutboxStore` is used in dev; `pg_outbox.py` / `pg_store.py` back the production path. `REVIEW_REQUIRED_TYPES` in `back/dano/assets/drafts.py` gates which asset types must pass the three-model review board before publishing.

### Storage defaults

`InMemory*` implementations are wired by default in `app.py`'s lifespan. Swapping to Postgres-backed variants requires only changing those singletons — the interfaces are identical.

### Key large files

| File | What it contains |
|---|---|
| `back/dano/execution/page/flow_spec.py` | FlowSpec DSL (~1 MB) |
| `back/dano/execution/page/request_capture.py` | HTTP request capture engine (241 KB) |
| `back/dano/execution/page/flow_spec_edit.py` | FlowSpec editing ops (largest test file: 336 KB) |
| `back/dano/execution/page/recorder.py` | Browser recorder (162 KB) |
| `back/dano/execution/page/recording_live.py` | Live agent annotation ops (157 KB) |
| `back/dano/catalog/manifest.py` | Skill manifest builder / function-tool builder (49 KB) |
| `back/dano/gateway/app.py` | FastAPI app, all routes, `_publish_canonical_recording` (71 KB) |
| `back/dano/agent_tools/tools.py` | Pi-agent tool implementations (94 KB) |
| `skillfrontend/src/components/PageRecorder.tsx` | Full recording UI (68 KB) |

### Deployment

`deploy/nginx/dano-internal-deny.conf.example` must be active in production — it blocks `/internal/*` from being proxied externally. `deploy/systemd/` contains the token-refresh timer that POSTs to `/internal/runtime-tokens/refresh-due` on a schedule.
