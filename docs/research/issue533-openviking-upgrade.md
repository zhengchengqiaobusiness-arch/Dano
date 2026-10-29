# Issue 533: OpenViking latest stable upgrade

## Release selection

The official latest non-prerelease release resolved on 2026-10-29 is
[`v0.5.2.1`](https://github.com/volcengine/OpenViking/releases/tag/v0.5.2.1),
source commit `f5e6203033a13469c3d9a937f2ca39492ea69c51`.
Recheck the official latest release before production switching; this dated
selection does not narrow the issue's requirement to a permanently fixed version.

`deploy/memory-release.json` records the official index and both platform
digests. The amd64 image config digest is
`sha256:dd47f14308549f41f8ecdb32d853e1b7faef5bde609d0693cdae8422c75e408a`.
The current Dano plugin remains `@josephyoung/pi-openviking@0.1.24` with Pi 1.4.0.
This is a server upgrade, not a switch to upstream's Pi extension or Gateway.

## Compatibility boundaries

Review the upstream [0.5.1 migration notes](https://github.com/volcengine/OpenViking/releases/tag/v0.5.1)
and [0.5.2 migration notes](https://github.com/volcengine/OpenViking/releases/tag/v0.5.2)
alongside the selected hotfix.

| Upstream change | Dano disposition |
| --- | --- |
| Session extract endpoint removed | The plugin already uses commit, polls its task, and verifies the source archive and memory diff. Verify this real chain on the candidate. |
| Working Memory defaults off | Dano uses raw source archives and long-term memory, not WM checkpoint summaries. Keep the upstream default; verify source retention and extraction instead of introducing a new summary dependency. |
| `write(create)` becomes replace; wait timeout removed | Dano writes final content with explicit replace and uses client HTTP deadlines. It does not rely on create returning a conflict. |
| Optional Gateway, MySQL and multi-write V2 migrations | Do not enable or change these providers as part of this upgrade. Preserve the configured file storage and account store. |
| External peer ID normalization | Dano must retain its existing peer identity. Do not rename or merge legacy peer directories speculatively. |
| Session privacy discovery false positives | The selected hotfix disables new automatic discovery; registered privacy values still resolve. Keep synthetic probe content exact across the upgrade. |
| LangChain imports and upstream Pi tool names | Neither is Dano's integration surface; no corresponding dependency/tool migration is needed. |
| Native form templates and user namespaces | Run the real upstream schema/rendering checker, old-memory search and cross-user denial probes. |

## Old-volume verification

Use `scripts/check-openviking-upgrade.py` only on a disposable service with an
independent data directory and a configuration copied privately on the host.
Generate a distinct test root key. Do not publish OpenViking ports or change
production credentials, namespaces or data to manufacture test cases.

1. Start the previous official image with that disposable configuration/data.
2. Run `seed` inside it. It creates a random `dano-upgrade-*` account, Alice/Bob
   keys, a synthetic indexed memory and an uncommitted source Session. Keys are
   saved only to a mode-0600 state file in the disposable probe directory.
3. Stop the old service. Back up its complete disposable data, then start the
   candidate with the same configuration/data and run `verify`.
4. Require the exact old content and source ID to survive, the old memory to be
   searchable, anonymous access to fail, and Bob's read/write/delete to be denied.
   Require a new commit to complete with the source archive and memory-diff
   locations expected by the actual Dano plugin.
5. Run `scripts/check-openviking-form-schema.py` in the candidate image without
   credentials, network or production mounts.

The verification script prints only acceptance flags and bounded failure codes;
never export its private state, credentials or raw server logs.

## Production data and rollback

Before switching, stop Dano and OpenViking writers under the production deploy
lock and make a private, restorable snapshot of the full OpenViking data volume.
Keep the old immutable images and deployment control-file backup. Verify backup
readability and preserve ownership/modes. Image rollback alone is insufficient
if the candidate changed on-disk data: restore the compatible snapshot with both
writers stopped before starting the old service. Do not replace adjacent-service
data, user credentials, TLS, nginx routes, embeddings or reranker models.

After machine readiness, accept through the existing production URL with real
text, bash ls, image upload/read/description, loaded asset and page-console checks,
plus the memory workflow. Keep referenced test uploads for their normal lifecycle.

## Executed compatibility evidence (2026-10-29)

The production host's disposable containers used the actual old image
`sha256:9906b9af67c788e7727bf6677811309d6f56a034f1c5b7a6b1db073c270bf238`
and the candidate amd64 config digest recorded above. Neither published a port.
They used an independent config/root key, data directory and synthetic account.

- Old-image seed completed; the candidate preserved exact old content and source
  IDs, and found the old memory through its persisted index.
- Unauthenticated access failed; Bob's read, replace and delete of Alice's
  memory all returned 403; Alice's content remained unchanged.
- The candidate completed a real-model Session commit. Its task result, source
  archive and memory-diff locations met the current plugin's checks. A new post-upgrade preference was
  extracted and actually recalled through search.
- Restoring the old data snapshot and restarting the old image passed the same
  read, source, search and isolation checks.
- The real candidate image passed the native form-schema/rendering check
  without network access, credentials or runtime data.

These are compatibility tests on synthetic data, not production browser acceptance.
