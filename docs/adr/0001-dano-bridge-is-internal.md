# ADR 0001: Make Dano Bridge an internal server subsystem

Status: Accepted
Date: 2026-06-26

## Context

Dano Bridge is an internal HTTP/SSE and RPC subsystem of the Dano server. It has no second real product consumer, no independent release lifecycle, and the production server build should not pay for a workspace package boundary that it immediately bundles back into `@dano/app`.

## Decision

Move Dano Bridge from `packages/bridge` to `apps/dano/src/bridge`. Delete bridge package build and verification commands instead of replacing them with aliases. Drop the private `@dano/app` package version so the root `package.json` remains the only product version.

During the move, WebSocket-coded adapter names such as `ws-rpc-adapter.ts`, `WsRpcAdapter`, and `WsClient` become `bridge-rpc-adapter.ts`, `BridgeRpcAdapter`, and `BridgeClient`.

## Consequences

- `packages/bridge` will be removed.
- Bridge source tests remain as directory-level tests under `apps/dano/src/bridge`.
- Dano production build will not run or depend on a bridge package build.
- `readDanoPackageInfo` must read the root product version from the dev checkout or packaged runtime metadata, not from `apps/dano/package.json`.
- Re-extracting bridge as a package requires a second real consumer or independent release requirement.
- The naming cleanup must not change HTTP/SSE paths, EventSource behavior, wire envelopes, or browser store connection logic.
