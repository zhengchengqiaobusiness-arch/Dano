# AGENTS.md

This file provides guidance to coding agents when working with code in this
repository.

## Monorepo Structure

This is a pnpm workspace monorepo with the following packages:

- `@pi-web/bridge` (`packages/bridge/`) — WebSocket RPC bridge server
- `@pi-web/bin` (`packages/bin/`) — Pi extension entry point
- `@pi-web/svelte` (`packages/svelte/`) — Svelte 5 web client, current release
  mainline

## Commands

- `pnpm run check` — type-check with `tsgo`
- `pnpm run build` — build everything (bridge → bin → published web client)
- `pnpm run build:bridge` — build bridge package
- `pnpm run build:bin` — build bin package (Vite library mode)
- `pnpm run build:svelte` — build Svelte client to `web-dist/`
- `pnpm run build:web` — build the published web client (`packages/svelte/`)
- `pnpm run dev:web` — start the published web client dev server
  (`packages/svelte/`)
- `pnpm test` / `pnpm run test:watch` — run Vitest test suite
- `pnpm fmt` / `pnpm run fmt:check` — format/check with `oxfmt`
- `pnpm lint` / `pnpm run lint:fix` — lint/fix with `oxlint`

## Architecture

- `packages/bin/` — Pi extension entry point, registers `/web` command
  - Bundled with Vite (library mode) → `dist/bin/index.js`
- `packages/bridge/` — HTTP server, WebSocket RPC bridge, auth, terminal log
  view
  - Compiled with tsc → `dist/bridge/`
- `packages/svelte/` — Svelte 5 client (Vite + vitest), published to `web-dist/`

## Important Tips

- Read the source code of @mariozechner/pi-coding-agent, @mariozechner/pi-ai
  carefully, especially the wire protocol of pi
- Do not add thin wrapper functions around existing functions unless the wrapper
  adds real value beyond renaming.
- Use git conventional commits specification when commit
- Do not use `nl -ba $file | rg -n $pattern`, use `cat $file | rg -n $pattern`
  instead
- If you apply any edits on the published Svelte UI in `packages/svelte/src`,
  run `pnpm run build:web`
