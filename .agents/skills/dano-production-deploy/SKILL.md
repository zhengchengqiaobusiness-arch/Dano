---
name: dano-production-deploy
description: Deploy a prepared Dano image to the existing service on 1.15.173.22, finish machine smoke, then verify real chat and OA login in the Codex in-app Browser. Full feature acceptance remains optional.
---

# Deploy Dano

Machine deployment and browser smoke have separate results. After machine health
and smoke pass, save the receipt and release the lock. Then complete baseline
browser smoke (real chat, OA login, logged-in chat). Full feature acceptance is
optional and starts only when requested.

## Fixed production entry

Use the checked-in entry instead of constructing a release script per task:

```sh
pnpm deploy:production --image <prepared-image-tag-or-digest>
```

The public server profile is `deploy/production/profile.json`. The command
requires clean synchronized main, checks upstream CI and commit, and resolves
the supplied already-built server image to its immutable ID. It preserves the
existing Compose files, nginx/TLS, secrets, runtime data and adjacent services.
It does not build images, clone source on the server, or install dependencies.
Prepare images before deployment using the normal build/release path; a missing
or mismatched image fails before switching traffic. For a new installation or a
Compose/nginx/memory topology change, use the explicit deployment preparation in
`deploy/README.md`; this entry performs image updates to the existing profile.

For a read-only preview with no network or mutations:

```sh
pnpm deploy:production --image <prepared-image> --plan
```

## Execution and completion

1. Inspect the current worktree. Preserve unrelated changes; do not stash or
   discard them. Deploy only a merged target on main. The entry runs
   `git sync-upstream` and verifies main/origin/main/upstream/main equality.
2. Run the entry with the prepared image. Use the repository's file-based SSH
   runner and the fixed profile, not ad hoc SSH build commands. The worker holds
   the production lock only through preflight, SYSTEM transaction, app switch,
   bounded health wait, nginx reload, smoke and receipt.
3. The worker validates image revision and packaged version/assets, effective
   Compose projection and sandbox protection, preserves other containers, and
   changes only DANO_IMAGE plus the normal SYSTEM sync. The previous image is
   retained. Machine failure restores the previous image/env/SYSTEM and reruns
   health/smoke. A cleanup-only problem is recorded without rolling back a
   healthy app. Raw command output and rollback copies remain root-only on host.
4. Require the returned candidate-bound receipt: `status=deployed`, correct
   target/image, `health=healthy`, `smoke=passed`, `acceptance=not_requested`.
   Report version, image, receipt and timings. A transport interruption is
   unconfirmed; inspect that attempt's on-host receipt and lock before retrying.
   Never rebuild an already verified candidate solely to repeat deployment.
5. Complete [baseline browser smoke](references/browser-smoke.md) without another
   permission question. The machine receipt's `browserSmoke=pending` is a handoff,
   not proof that browser smoke passed. Bind browser evidence to its target/image.
   If login needs user input, continue independent checks and hand off that step;
   report the remaining browser check explicitly instead of declaring it passed.
6. After baseline smoke, ask whether the user wants full feature acceptance, with
   **不验收** first/default. Only an affirmative answer starts
   [feature acceptance](references/acceptance.md). Browser blockage does not keep
   the deployment lock or automatically roll back a machine-healthy deployment.

## Boundaries

- Never export secrets, raw sessions/config, `.env` or raw logs. Only allowlisted
  structured receipts and log counts may leave the host.
- Keep runtime, source, help, secrets and deploy-control directories separate.
- Preserve OA, TLS, `/web/`, `dano-site`, memory data, skills, workspaces and volumes.
- Never disable Heimdall, change sandbox isolation, prune referenced images,
  remove volumes, or recursively repair arbitrary runtime permissions.
- Keep the previous image. Do not let a browser timeout, optional acceptance,
  pending UI question, or cleanup-only error roll back a smoke-passed release.

Use [remote execution](references/execution.md) for transport recovery. Use
[build recovery](references/recovery.md) only during explicit image preparation;
its download routing is not part of deployment or a reason to redo known probes.
