# Build and recovery routing

Read this before a release payload. Select one path, record why, and retain the
selection across reconnects. These are observed constraints from 2026-10-16,
not immutable host facts: the capability probe and current Dockerfile decide.

## Cheap checks first

- Use the already-working local Node/Pnpm environment. Before tests/builds,
  run `node -e 'require("./apps/dano/node_modules/fs-ext")'` to catch a native ABI mismatch. Prepend the
  bundled Node path only if Node/Pnpm is missing; changing Node on an installed
  dependency tree caused a failed test/build run. A repeatable full-suite timeout
  can use `pnpm run test --maxWorkers=2` with the same assertions, not a reinstall.
- The server had Git 1.8 without `-C`/`--show-current`, no host Node, and Python
  using an ASCII default. Consume probe capabilities: `cd` plus plain Git,
  public helpers in the accepted Node image, and explicit UTF-8 file encoding.
- Pin the exact inventoried Compose file list once. Bind every receipt and
  optional acceptance fixture to target SHA/image ID/container ID; resume the unfinished
  stage. Use the normalized `deploy-build-context.mjs archive` output for test
  source mounted under `/app`; root-only or writable raw checkout metadata can
  correctly fail the installation guard. Keep that guard unchanged.

## Source transfer

For this host, a retained failure receipt can select the verified local bundle
directly; it is not necessary to reproduce the same network failure each release.
Try a noninteractive fetch bounded to 60 seconds only when selecting or repairing
the direct route. On transport failure, create a local bundle from the
server's proven HEAD to the synchronized target, transfer it, check SHA256 and
`git bundle verify`, then `git fetch <bundle> refs/heads/main:refs/remotes/origin/main`
and `git merge --ff-only origin/main`. A dirty/diverged server checkout still
blocks. Refresh the local authenticated upstream proof immediately before switch;
pass it to the waiting worker without repeating a failed server network route.

## Build decision

1. Reuse an already built **same candidate** only with matching target/tree,
   image ID, version and artifact inventory. Evaluate the planner below before
   dependency downloads. With an eligible application-only diff, a proven runtime
   base and this host's documented download failures, select artifact recovery
   directly. Otherwise use a normal cached Dockerfile build; only uncached
   dependency stages need the external-source probes.
2. Probe the Dockerfile's apt/npm/pip **and pinned Git Skill source** before a
   dependency build; the previous run reached the last Git clone only after
   spending time on earlier downloads. Try supported mirrors/proxy settings
   once per changed route; do not repeatedly rebuild unchanged failed stages.
3. If network access blocks a full build, evaluate application-only recovery:

   ```sh
   node .agents/skills/dano-production-deploy/scripts/runtime-artifacts.mjs plan BASE_FULL_SHA TARGET_FULL_SHA
   node .agents/skills/dano-production-deploy/scripts/runtime-artifacts.mjs build BASE_FULL_SHA TARGET_FULL_SHA /private/tmp/dano-artifacts-UNIQUE
   ```

   The planner rejects dependency, Dockerfile, runtime helper/default/config,
   unknown build input changes and non-version root package changes. It permits
   compiled server/web changes and documentation. Confirm the unchanged
   Dockerfile still consumes server/web from `/app/dist` and that no permitted
   source is directly copied into the runtime stage. The builder requires a
   clean target checkout and matching native ABI, runs the real build, rejects
   links/special files, normalizes public output and produces a tarball,
   per-file hashes, source/tree provenance and a minimal Docker recipe.

   Transfer/check the archive SHA. Verify every file against `provenance.json`
   before building, and bind BASE_FULL_SHA to the already accepted **immutable
   Linux image ID** using the previous release receipt. Pass that ID as
   `RUNTIME_BASE`; replace complete `/app/dist` plus root product-version metadata,
   with the target OCI revision/version labels. Never transfer local
   `node_modules`, overwrite native dependencies, or reuse an unproven base.
   Repeat the candidate's built config/sandbox/PR-specific preflights and machine smoke. Report this as artifact recovery, not a full Dockerfile
   source build. A blocked planner selects a compatible Linux builder or reports
   the dependency/source blocker; do not widen the allowlist for convenience.

## Rollback and completion

Compose the shipped `deploy/system-prompt.mjs backup/restore/discard` commands
under the deployment lock and effective app user. Keep the transaction through
machine smoke. A `docker cp` round trip was observed to change SYSTEM to
root ownership; use the shipped atomic restore and verify UID/GID/mode/content.
Derive route checks from the preserved port projection (the observed deployment
published only 443). Finish with one candidate-bound evidence export and scoped
cleanup; preserve reusable base images/cache and referenced uploads.
