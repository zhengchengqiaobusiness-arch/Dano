# ADR 0002: Share browser/server protocol types

Status: Accepted
Date: 2026-06-26

## Context

The Svelte web client and Node server must agree on browser/server wire protocol shapes. Duplicating those types would create drift, but letting the web client import from server bridge internals would keep the old package boundary problem under a different name.

## Decision

Keep browser/server wire protocol types in `apps/dano/types/protocol.ts`. This file contains only cross-boundary protocol and runtime-config DTO shapes. Server-only bridge config, lifecycle state, client tracking, and internal bridge events stay inside `apps/dano/src/bridge`.

`BridgeConfig` as a whole is server-only and stays in bridge internals. Browser-observed config DTOs serialized into `window.__PI_WEB_CONFIG__`, such as empty state and quick action shapes, may live in `apps/dano/types/protocol.ts`.

Imports from `@dano/types/*` should be type-only for types. Runtime imports are allowed only for JSON-safe protocol constants, and only when Vite, Vitest, and the server build all resolve the alias at runtime.

## Consequences

- The web client must not import from `apps/dano/src/bridge`.
- Protocol types must remain JSON-serializable and browser-safe.
- Shared protocol constants such as `ASK_USER_QUESTION_TOOL_NAME` may live with the protocol DTOs when all runtime resolvers support the import path.
- Runtime protocol field names, command types, response shapes, and SSE envelopes must not change as part of this move.
- Protocol extraction may start with only the web client’s currently needed type set if moving every type would create broad churn.
- Server-only lifecycle and client-tracking types stay in bridge internals.
