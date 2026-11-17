---
name: dano-production-deploy
description: Deploy a prepared Dano image to the existing service on 1.15.173.22, finish health checks and machine smoke, and report completion. Browser acceptance is separate and runs only when explicitly requested.
---

# Deploy Dano

Deployment and feature acceptance are separate operations. Default deployment
completion means the intended immutable image is running, health and machine
smoke pass, the receipt is saved, and the deployment lock is released. Do not
open a browser, send model prompts, upload images, or wait for acceptance as part
of a deployment-only request.

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
5. Deployment is now complete. Ask whether the user wants acceptance of this
   change, with **不验收** as the default/first option. Use the async question
   tool when available. This question does not keep the worker or lock alive.
   Silence means no acceptance is started; it does not undo deployment.
6. Only an affirmative answer starts [feature acceptance](references/acceptance.md).
   Acceptance failure or cancellation never triggers a delayed deployment timer.
   A confirmed product regression is handled as a new repair/rollback operation.

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
