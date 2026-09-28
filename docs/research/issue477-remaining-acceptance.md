# #465 remaining acceptance

Updated 2026-09-28. This records the unfinished original PRD/Spec gates; it
does not add release requirements. #473–476 are closed; #477, #465 and Draft
[PR #490](https://github.com/zhengchengqiaobusiness-arch/Dano/pull/490) remain open.
The full historical AC/T ledger is [here](issue477-memory-release.md).

## Fixed inputs and retained passes

- Candidate Dano `0.2.63`, published `pi-openviking@0.1.18`, official
  OpenViking `v0.4.20`. Built protected image
  `b771b66b5480acaf63dab0b4ddcf623c94fcc612169a8d1c420dd390a8fda629`
  completed the affected Browser rerun, new-chat recall, text/bash and actual
  image upload/read. The unedited image response calls the red circle an ellipse;
  the receipt records this precision limit.
- Prior protected image
  `1297fc14a8f8ae6ab5f5527fa5eec3a6b72378bccc2bf900be2e9f24fb7e925a`
  (`0.2.62`/`0.1.17`) remains the source of the retained Browser receipts.
- MiMo `mimo-v2.5`, existing authorized private configuration, thinking off.
  Model-key replacement is not a prerequisite. Model and tokenizer bindings
  come from the private deployment configuration, not test-specific runtime code.
- Reuse the isolated stack and trusted `https://localhost:18711/` entry.
  Runtime, credentials, assets, TLS and recovery data stay outside the checkout.
- Retain current ordinary Pi CLI consent/save-ready/source/new-session/pause,
  OA single-user login/text/image/bash, Browser save/correct/forget/recall, and
  one collection/pause/export/forget/resume observation. Their receipts state
  their limits. Retain every failed attempt alongside subsequent passes.

## Remaining execution order

| Group | Next concrete checks | Completion evidence |
|---|---|---|
| 1. Collection, lifecycle and Browser | Finish semantic exclusion, pause/claim/in-flight, revoked consent, reconnect/branch/dispose cases beyond the completed same-source overlap rerun. Complete independent real OA Bob and existing business authorization/form/Skill regressions. | Current real entrypoint receipts, separate identities, source/task records and rendered Browser proof; original repetition requirements still apply. |
| 2. Frozen quality and performance | Run the frozen 80 cases three times on the current pair. Run the matched complete on/off workload of at least 100 requests with five concurrent users. | Every attempt, model answers/source review, actual token counts, cold/steady latency, save-ready latency and matched cost. Apply the existing Spec thresholds without filtering failures. |
| 3. Real recovery and rollback | On isolated synthetic owners, finish live shared-document sealing, matched old-volume restoration, new/old queues through ready, clear plus newer writer, OpenViking USER credential rotation, candidate upgrade and matched old-image rollback. | Exact binary/volume/checkpoint identities, public service receipts, final content/source readback, deletion non-revival and owner isolation. Synthetic overlays alone do not close this gate. |
| 4. Release audit and closure | Reconcile AC-01–13/T-01–14 against current evidence, review any resulting code changes, finish upstream PR/merge/closure and scoped cleanup. | No unexplained failed hard constraint or unfinished mandatory gate; merged upstream PR, #477/#465 closure, remote branch removal and cleanup receipt. |

### Current overlap observation

One normal stable-preference message produced an automatic ready operation
`188bc3484d776dccbef807505043d89cc0eb085fa5a03be62e57bd511539dd4f`
and an unsolicited explicit attempt
`911c6f25a4370f1d85e7bccc638623582bcf840c58980a98782320b2667c184c`
which failed with `MEMORY_NO_EXTRACTED_FACT`. The ready document was read and
recalled correctly. Later read-only digest comparison established that the
automatic quote and fact were the exact explicit saved text. Historical raw
selector input/response was not recorded, so its inference path remains unknown.

[Pi PR #17](https://github.com/josephyoung/pi-openviking/pull/17) fixes the
confirmed acceptance gap: even if a model selects an excluded quote, the
selector rejects duplication against a valid receipt from that original source.
Independent facts and later source turns stay eligible; combined quotes remain
retryable. The version bump automatically published `0.1.18` through OIDC.
All 264 extension tests pass. Baseline and fixed versions both pass six real
MiMo selector probes, so those probes alone do not reproduce the historical
failure. See the [fix receipt](evidence/issue477-pi-0118-summary-20260928.json).
The affected final-image Browser rerun reached explicit `ready` in 35.259 seconds
while the completed automatic selection created no operation. Browser source
readback and a fresh-chat recall succeeded. The broader semantic exclusion
matrix remains open; this narrow guard and one Browser observation do not close
AC-04. See the [current Browser receipt](evidence/issue477-0263-overlap-browser-20260928.json).

### Reusable entrypoints

- Ordinary Pi: `ordinary-pi-memory-real.mjs` wraps the published standard
  launcher through `linux-cli-memory.mjs` and `cli-memory-host.mjs`. Supply the
  private service/model files plus `DANO_FIXTURE_PROVIDER`,
  `DANO_FIXTURE_MODEL` and `DANO_FIXTURE_TOKENIZER_REVISION`; mount the verified
  tokenizer assets read-only. Run in the fixed image on the isolated service
  networks. It creates a disposable account, waits for asynchronous USER
  deletion, and retains an empty test account. Failure logs remain private.
- Dano real service: reuse `protected-supervisor-http.mjs`; its synthetic
  authenticated protocol proof does not replace real OA Browser identity.
- Quality: `fixtures/issue477-evaluation.json`, SHA-256
  `8c1ad95d69ab6f58e601d48b353aeea60b63365a65a507de917ed5bc9bca250c`.
  Keep expectations frozen. Old candidate reports cannot establish current-pair
  quality or matched workload completion.
- Recovery: use the shipped operator recovery commands and existing public-API
  recovery fixtures. Check ownership, exact image, matching volumes and current
  synthetic dataset before any mutation; keep credentials and original receipts
  private. Do not modify local phases to manufacture successful recovery.

## Retest rule

A failure must record its stage and actual cause or explicitly say that the
cause is still unknown. Repair operator configuration without changing the
model key or frozen expected results. Repeat only affected completed checks
after a repair, plus the final checks required by the original Spec. If a real
runtime fix requires a new package/image, record the new immutable pair and
identify which earlier evidence no longer applies before proceeding.

## Evidence storage

Full raw attempts and duplicate intermediate reports are preserved outside Git.
The [archive catalog](evidence/issue477-acceptance-archive.md) gives their location,
original paths and SHA-256 hashes. Retain future raw outputs there and commit only
brief final summaries with archive hashes. Frozen inputs, reproduction scripts,
thresholds and the unfinished gates above remain unchanged.
