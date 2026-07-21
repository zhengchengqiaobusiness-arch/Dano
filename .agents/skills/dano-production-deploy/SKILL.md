---
name: dano-production-deploy
description: Safely update and release Dano production on 1.15.173.22 from the latest upstream/main, preserving runtime data, secrets, TLS, nginx routing, skills, and adjacent services, then complete API/SSE and real in-app-browser acceptance. Use for requests such as "update deploy", "更新部署", "部署最新 upstream/main", "发布 Dano 到生产", or equivalent Dano production update/release requests.
---

# Deploy Dano Production

Deploy only when the user explicitly requests a production update. Treat completion as: the exact latest `upstream/main` commit is running, every required acceptance check passes, cleanup is safe, and the evidence report is complete.

## Non-negotiable boundaries

- Target only `root@1.15.173.22`. Use this SSH prefix exactly:

  ```sh
  ssh -i ~/.ssh/id_rsa -o BatchMode=yes -o IdentitiesOnly=yes root@1.15.173.22
  ```

- Keep diagnostics structured and allowlisted. Never print, copy off-host, or directly inspect the values of API keys, provider tokens, secret files, `.env`, raw runtime sessions, or user runtime configuration. Use presence, permissions, hashes of non-secret artifacts, redacted status, and repository checkers that emit only acceptance markers.
- Keep `/root/Dano-source`, `/opt/dano/deploy`, and `/opt/dano/runtime-data` separate. Never use a source checkout as the runtime directory.
- Preserve `/opt/dano/runtime-data`, Compose named volumes, runtime skills, agent config/workspaces, TLS material, environment-owned nginx routes, secret files, and neighboring services—especially `dano-site` and `/web/`.
- Never run `docker compose down -v`, remove named volumes, broadly delete runtime files, or prune an image/layer referenced by any container.
- Keep Heimdall protection enabled. Stop if `HEIMDALL_PROTECT_CONFIG_OVERLAY=0` would reach the app. Do not weaken Bubblewrap, sandbox, runtime mounts, or secret filtering to make acceptance pass.
- Do not overwrite user runtime config. Defaults in `deploy/runtime-defaults/` are copied only when missing by the current entrypoint. Synchronize `SYSTEM.md`, `settings.json`, or `heimdall.json` into an existing runtime only when the release explicitly requires it and the effective path has been proven. Do not add pre-1.0 layout migrations unless explicitly requested.
- Do not substitute HTTP, `agent-browser`, API smoke, or screenshots for the required Codex in-app Browser run against `https://1.15.173.22/`.
- Do not commit, push, open a PR, or change source code as part of a deploy request.

## Phase 1: Re-read the current contract

Before touching local or remote state, read the current versions of:

- `AGENTS.md` and `deploy/AGENTS.md`
- `deploy/README.md`, root `package.json`, `Dockerfile`, `.env.example`, and `docker-compose.yml`
- `deploy/docker-entrypoint.sh`, `deploy/runtime-defaults/*`, `deploy/compose/*`, and `deploy/nginx/*`
- `scripts/deploy-release.mjs`, `scripts/deploy-compose.mjs`, `scripts/deploy-exposure.mjs`, `scripts/smoke-dano-deploy.mjs`, and relevant acceptance helpers
- this skill's `scripts/summarize-logs.mjs` and `scripts/summarize-compose-config.mjs` before production log or resolved-Compose diagnostics

Apply this precedence rule: explicit host invariants in this skill identify the authorized production target and required directory boundaries; the current repository defines shipped build/runtime behavior; live read-only inventory defines environment-owned topology and configuration. Stop on an unexplained conflict instead of choosing one source silently. Prefer repository scripts when they preserve the inventoried production environment. Compare `deploy:release` staging behavior with the live layout before using it so environment-owned routing or adjacent-service configuration remains intact.

Record the local worktree status before switching branches. Stop rather than stash, discard, or overwrite unrelated user changes.

## Phase 2: Resolve the release manifest

1. Switch the local checkout to `main` and run `git sync-upstream` as required by `AGENTS.md`.
2. Resolve and record full SHAs for local `main`, `origin/main`, and `upstream/main`. Lock the release target to the current `upstream/main` SHA after synchronization and call it `target_sha`. Confirm local `main` and fork `main` are at `target_sha`; diagnose any mismatch before continuing.
3. Read the root `package.json` version at the target commit. Do not reuse a remembered version.
4. Identify the currently deployed immutable image, commit, product version, and frontend asset names using non-secret container/image inspection and HTTP HTML. If the old image tag does not prove a commit, correlate it with server Git history and image contents; label the result uncertain if it cannot be proven.
5. Query merged PRs between the proven previous production commit and `target_sha`. For each, record PR number, title, merge commit, linked Issue(s), and a concise shipped-change summary. Read its PR body and linked Issue(s), extract every release-specific acceptance criterion, and classify each as applicable or not applicable with a reason.
6. Define an immutable target tag from the full target commit, normally `dano-app:<short-sha>`. Never use an old tag or mutable `latest` as release identity.
7. Confirm the live CI/check state for `target_sha`; stop on a required failing or pending check unless the user explicitly authorizes proceeding with the recorded risk.

Checkpoint: do not build unless `target_sha`, product version, previous release boundary, release PR list, every extracted acceptance criterion, target CI state, and rollback image are accounted for. If the previous boundary is uncertain, say so and use a conservative commit range rather than inventing precision.

## Phase 3: Inventory production without exposing secrets

Use read-only SSH checks first. Confirm:

- host identity/time and Docker/Compose availability;
- `/root/Dano-source` exists, is the expected Git checkout, and its current branch/status/HEAD;
- Git remote names, branch tracking, and ahead/behind state without printing credential-bearing remote URLs;
- running containers, health, image IDs/tags, Compose project/service labels, networks, and mounts;
- `/opt/dano/deploy` and `/opt/dano/runtime-data` remain separate;
- runtime, `.pi`, workspaces, uploads, and skills mounts match current Compose/entrypoint documentation;
- `.env`, secrets, TLS files, and nginx config exist with appropriate permissions, checking presence only;
- effective exposure mode and published ports using allowlisted non-secret keys only;
- `dano-site` and its `/web/` route are healthy before the update;
- disk space and Docker disk usage are sufficient for a no-cache build while retaining the rollback image.

Before rollback capture or any production mutation, open one long-lived SSH session and acquire a non-blocking exclusive `flock` on `/var/lock/dano-production-deploy.lock`. Fail closed if another deployment owns the lock. Under the lock, repeat the mutation-relevant inventory—current container/image IDs, Compose services/mounts/networks, deploy-file state, runtime paths, adjacent-service health, and disk capacity—and require it to match the pre-lock inventory. Hold the same file descriptor through source update, build, switch, acceptance disposition, rollback if needed, and cleanup. If the SSH session drops, treat the kernel-released lock as an interrupted deployment: acquire a new lock, repeat the full inventory, and reconcile live state before resuming.

```sh
exec 9>/var/lock/dano-production-deploy.lock
flock -n 9 || exit 75
```

After the locked recheck passes, save a root-only on-host rollback copy of the deployment control files that will change, including `.env` if necessary, without displaying their contents. Record the old container/image IDs and exact Compose invocation. Do not copy secrets or runtime data into temporary build directories.

Use structured commands such as `docker inspect --format`, Compose status output, HTTP status/headers, Git status/counts, and filesystem metadata. Keep raw logs on the host. Before each diagnostic log window, record its RFC3339 UTC start. Request Compose logs with `--timestamps --since <window-start>` and pipe them entirely on-host through `scripts/summarize-logs.mjs` with `DANO_DIAGNOSTIC_SINCE` set to the same timestamp. Run the filter inside the current Dano image or another already-present Node image when the host lacks Node. Execute the remote pipeline with `bash -o pipefail`; require the log producer and filter to exit zero, `truncated=false`, and `unscopedLines=0`. Treat `emptyWindow=true` as `no log lines returned`, never as proof of clean logs. Return only the per-service JSON counts. If those counts and structured diagnostics cannot establish the cause, report a safe-diagnostics gap and stop rather than retrieving raw lines.

Checkpoint: stop if the locked inventory differs from the pre-lock inventory, the live topology differs materially from the current repo, any required mount/volume cannot be explained, adjacent services are already unhealthy, the rollback path is incomplete, or disk capacity cannot safely hold both releases.

## Phase 4: Fast-forward server source

In `/root/Dano-source`, confirm a clean checkout, switch to `main`, run normal Git diagnostics, and then run `git pull --ff-only` on its tracked upstream. Confirm the resulting HEAD equals the target SHA.

If pull fails or appears hung, inspect the exact command, current directory, branch/upstream, dirty state, remote configuration, ahead/behind counts, Git process state, and concrete connectivity error. Use non-TTY BatchMode SSH for diagnostics. Do not call it a server-network problem without evidence and do not retry blindly. Use an alternate transfer such as a verified Git bundle only after the root cause is established and the resulting commit can still be proven byte-for-byte equal to target.

Stop if the server checkout is dirty, diverged, or not at the target SHA after the update. Never reset away server changes without explicit approval.

## Phase 5: Build the immutable image

Build from the proven target checkout and current Dockerfile. Prefer a no-cache build for production updates. Pass registry selection only through the current supported build arguments; follow the Dockerfile's current npm and Debian apt mirror order exactly. Do not patch mirrors on the server or inject secrets into build arguments or logs.

Capture the build exit status and structured stage results without returning raw output that may contain credentials. Reject a pre-existing target tag unless its image ID and proven provenance already match this exact run. Then verify inside the built image:

- root package version equals the release manifest;
- packaged server and web output exist;
- frontend JS/CSS asset names are freshly enumerated from the image;
- entrypoint/runtime defaults are from the target checkout;
- image architecture and configured non-secret runtime user/entrypoint are expected.

When the current Dockerfile supplies an OCI revision label, require it to equal the full `target_sha`. Otherwise bind provenance by recording the clean build-context HEAD immediately before the no-cache build, exact immutable tag creation, resulting image ID, package version, and asset inventory as one checkpoint. If this evidence cannot prove the image came from `target_sha`, stop and report that a repository-level OCI revision label is required.

Record the immutable tag and image ID. Do not switch traffic if the image version, assets, source SHA evidence, or structured build results disagree. A cached-looking build or old Vite asset is a build failure until explained and rebuilt.

## Phase 6: Stage and switch with Compose

Use the current repository deployment scripts where they safely match the inventoried production layout. Otherwise use the exact current Compose files and the minimal equivalent staging steps documented by those scripts; do not recreate their logic from memory.

Before changing anything, diff new Compose/nginx/deploy inputs against `/opt/dano/deploy`. Classify each file as repository-managed or environment-owned. Preserve custom `/web/`, TLS, secret, network, and adjacent-service wiring. Update only repository-managed Dano inputs and the allowlisted `DANO_IMAGE` entry; never print the rest of `.env`. Keep `.env` mode `600`.

Resolve Compose as JSON entirely on-host and pipe it through `scripts/summarize-compose-config.mjs` with `bash -o pipefail`. After classifying repository-managed and environment-owned nginx inputs, set `DANO_NGINX_HASH_ROOTS_JSON` to the exact approved source and deploy nginx directories. If the filter runs in a container, mount only those approved roots at identical paths and read-only; never mount broad `/root`, `/opt`, deploy, or runtime trees for hashing. The filter resolves real paths, rejects sources outside the allowlist or through a symlink escape, and must report `rejectedHashSources=0`. Return only the filtered projection; never return full `docker compose config` output because it can contain resolved environment values. Require both the Compose producer and filter to exit zero. Validate the filtered model before `up`. Prove that:

- app/nginx use the new immutable image/config;
- runtime bind and named volumes are unchanged;
- TLS and secret mounts still resolve read-only as intended;
- external networks and `dano-site` remain present;
- Heimdall protection is not disabled;
- `expose`, `depends_on`, `cap_add`, `security_opt`, `privileged`, container user, and read-only state match the target Compose contract;
- every repository-managed nginx template/shared-config mount has a readable content hash matching the target checkout, while TLS/secret material is checked only by mount identity and permissions;
- no unexpected service, port, volume, or route will be removed.

Immediately before switching traffic, fetch and compare `upstream/main` with `target_sha`. Treat `target_sha` as the release locked by this run. If upstream advanced, restart manifest/build preparation for the new SHA by default; deploy the locked older SHA only with explicit user direction.

Capture an RFC3339 UTC `switch_timestamp` immediately before Compose mutation. Run Compose `up -d --no-build` for only the Dano app/nginx services required by the current topology. Do not recreate or restart adjacent services. Wait for the app healthcheck and nginx dependency to settle. On failure, collect structured status and log counts scoped to `switch_timestamp`, then either correct the proven cause or execute the recorded rollback.

Checkpoint: the running container image ID must equal the built image ID before acceptance begins.

## Phase 7: Machine acceptance

Run the repository's current deployment smoke script against the production HTTPS base URL unless the script's documented exposure behavior requires an additional HTTP run. Confirm its actual checks, currently including:

- homepage;
- `/api/health`;
- client creation;
- SSE events endpoint with `Content-Type: text/event-stream`;
- posting to the returned `messagesUrl`, HTTP accepted status, and matching SSE success/failure response;
- client disconnect.

Separately verify both production HTTP and HTTPS behavior for `/` and `/api/health` according to the configured exposure mode, including redirects. Verify `/web/` returns 200 and `dano-site` remains healthy. Confirm Compose/container health and restart counts. Summarize app/nginx logs separately by service from `switch_timestamp`; the gate passes only when the producer/filter checks pass and every nonzero error, warning, timeout, health, permission, sandbox, or HTTP 5xx category has a structured explanation and an explicit non-regression or rollback disposition.

Do not treat smoke success as full acceptance.

## Phase 8: Real production browser acceptance

Use the Codex in-app Browser on exactly `https://1.15.173.22/`. Record the initial theme and restore it after testing if changed. Reuse or reclaim the correct in-app tab; do not switch browser surfaces just because a navigation or connection times out.

Complete all of these on the new deployment:

1. Send a harmless plain-text prompt with a unique per-run marker and confirm a real model response.
2. Ask the model to invoke the `bash` tool and run `ls` in the Runtime Workspace without reading file contents, environment variables, secrets, or runtime/session data. Confirm the UI shows the actual tool call and a successful result. When possible, run the repository's current bash acceptance checker against the exact new session scope without printing session content.
3. Create or select a non-sensitive per-run test image, upload it through the UI, ask the model to read and describe it, and confirm an actual `read` tool call plus a correct description. Do not use a secret-bearing screenshot or a stale uploaded hash.
4. Inspect the loaded document and network resources. Confirm the JS/CSS asset names match the assets enumerated from the new image.
5. Inspect the Dano page console and confirm there are no deployment-related errors or warnings.
6. Execute any PR-specific UI/mobile acceptance in addition to this baseline, preserving necessary screenshots or browser evidence.

If a previously working step fails, inspect the final URL, TLS state, active tab, browser-control connection, visible DOM, model chain, network requests, loaded static assets, container state, and safe structured diagnostics. Recover and retry the same path. Do not lower the bar or silently replace it with API checks. If in-app Browser access is unavailable or any item remains incomplete, deployment acceptance is incomplete.

Classify incomplete acceptance before leaving the new release live:

| State | Required disposition |
| --- | --- |
| Confirmed release regression | Roll back immediately, then repeat machine health checks. |
| Browser-control or external provider blockage with machine checks healthy | Retry the exact path within a bounded window; if still blocked, keep the previous image available and ask the user whether to hold or roll back. |
| Ambiguous failure | Prefer rollback to the last accepted image; hold only with explicit user direction. |
| Rollback failure | Stop all cleanup, preserve evidence, report current production health immediately, and provide recovery commands. |

End the run in exactly one reported state: `accepted`, `rolled back`, or `held pending explicit user direction`. Never infer permission to hold an unaccepted release.

## Phase 9: Cleanup and rollback discipline

Keep the previous image until every acceptance item passes. If a release regression is confirmed, restore the previous `DANO_IMAGE` and saved repository-managed deploy config, run the minimal Compose recreate for Dano app/nginx, and repeat health checks. Do not roll back runtime data or user config unless a release-specific migration explicitly requires and documents it.

After success:

- remove only this run's temporary smoke files, local test image, temporary Dockerfiles, build directories, and on-host rollback config copies no longer needed; remove a server upload artifact only when its exact ownership and lack of runtime references are proven, otherwise leave it to the documented upload lifecycle and report that retention;
- list old Dano images and all container references before removing an unreferenced old tag/image;
- prune dangling layers/build cache only after proving no running or stopped container needs them;
- retain reusable base images and all adjacent-service images;
- record post-cleanup filesystem and Docker disk usage.

Never delete an artifact whose ownership or reference status is uncertain.

## Phase 10: Report the evidence

Report:

- target deployment commit and product version;
- immutable image tag and image ID;
- built and browser-loaded JS/CSS asset names;
- previous-to-current PR/Issue list with titles, merge commits, and summaries;
- every extracted PR/Issue acceptance criterion with applicable/not-applicable status and evidence;
- container/Compose health and restart state;
- HTTP/HTTPS homepage, `/api/health`, API client, SSE, accepted/response, and `/web/` results;
- real in-app-browser text, `bash ls`, image upload/read/description, assets, console, and any PR-specific acceptance;
- preservation of runtime/config/TLS/secrets/skills/workspaces and `dano-site`;
- cleanup, filesystem free space, and Docker disk usage;
- locked `target_sha`, its final upstream revalidation, target CI state, and any uncertainty or skipped check;
- final release state: `accepted`, `rolled back`, or `held pending explicit user direction`.

On failure, lead with the confirmed root cause, the exact failing stage, redacted command/status evidence, whether rollback completed, current production health, and executable recovery steps. Never report deployment complete while a required check is missing.
