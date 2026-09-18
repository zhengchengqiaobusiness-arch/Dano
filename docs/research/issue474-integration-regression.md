# Issue #474 integration regression checkpoint

2026-09-18; implementation through `a7aab57f` plus the startup-test deadline
correction recorded alongside this checkpoint.

## Verified

- `pnpm run build`: server and web production builds passed.

- `pnpm test`: 124 test files passed; 1,514 tests passed, 1 skipped.
- `pnpm run check`: server TypeScript check passed; Svelte check reported zero
  errors and zero warnings.

The initial full test run had one 5-second outer test timeout in the startup
failure test. That test already allowed its child process 10 seconds. It passed
in isolation (3.6 seconds of test execution). The outer deadline was changed to
15 seconds, preserving the child deadline and every failure-before-listen
assertion. The subsequent full run passed.

The separate Linux HTTP fixture proves the compiled supervisor/host path,
per-user worker identities, exclusive supervisor lock, and graceful/abnormal
exit cleanup; see [its evidence](protected-supervisor-http.md).

## Remaining gates

These results do not prove issue #474 or #465 complete. In particular:

- The independent extension's provider capability changes still need a real npm
  patch publication and exact Dano dependency update.
- Memory composition still needs the shipped deployment configuration and
  authenticated settings/UI path.
- The host policy still needs the actual active-model token counter. Pi 0.85.1's
  inspected public declarations expose estimation helpers, not an exact text
  tokenizer. The character-count function used in unit fixtures is not suitable
  for the 1,500-token release budget.
- Automatic-collection consent and boundaries, governance, trusted project
  scope, deletion/no-resurrection, and remaining lifecycle work remain open.
- Real OpenViking/model/browser flows, Compose deployment, quantitative quality,
  latency/cost evaluation, backup/restore and upgrade/rollback remain required.

Local logs: `/private/tmp/dano474-integrated-regression-tests-final.log`,
`/private/tmp/dano474-integrated-regression-check.log`, and
`/private/tmp/dano474-integrated-regression-build.log`.
