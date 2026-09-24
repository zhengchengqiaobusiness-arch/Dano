# Issue 477: release and acceptance record

## Fixed candidate

The candidate manifest is `deploy/memory-release.json`; the Dano image build
uses a frozen pnpm lockfile and an exact npm dependency. On 2026-09-23, the
official `ghcr.io/volcengine/openviking:v0.4.20` multi-platform index resolved
to `sha256:b9827753d035f4157b5b318865907fd18f738924ad6398c86feb1182f209825b`.
Its Linux arm64 manifest was
`sha256:d1f3730128f3bde654e7996a88909cb47e4ec08d407806a464da5d91aa64c870`
and amd64 manifest was
`sha256:571fb4e12c66365d3552464617cc9aee8edbdec63e0f7d3ced3beb19db15b742`.
The isolated rootful Podman VM is Linux arm64; its pulled image inspection
confirmed the index digest, platform and upstream entrypoint.

The candidate overlay `deploy/compose/memory.yml` mounts protected config,
model asset and persistent data separately, keeps ports 1933/8080 unpublished,
and keeps the model-provider egress separate from Dano's internal memory
network. Its independent official llama.cpp `b11118` image is pinned to index
`sha256:fb8f521cdfee1b763a6ef0d6633922e780c1393c03b49945526550cf55010343`.
The GGUF checksum is
`ab9b81d9cd329c712eee379cf0068eabe6a5e2a01d0def61535eba9384085e2c`.
`node scripts/check-memory-release.mjs` and its deployment mode passed with a
private synthetic test configuration. Full Compose interpolation passed without
printing its secret environment.

## Executed official-image probe

The first isolated container used the prior, locally successful GGUF
`embedding.dense.provider=local` configuration. Its official image reported
that `llama-cpp-python` was absent, and `/health` did not become ready. This is
a real deployment incompatibility, not an OpenViking API failure. The container
was stopped. The candidate uses an independently pinned upstream llama.cpp
Embedding service. It returned a real 512-dimensional vector for a synthetic
Chinese query. The first OpenViking start with this service also showed that
Podman-injected HTTP proxy variables intercepted internal DNS; the overlay now
sets `NO_PROXY`/`no_proxy` for the internal service names, with a deployment
override for any model-provider hostname that needs direct TLS. The rebuilt
OpenViking service answered its internal `/health` with HTTP 200. A patched
private OpenViking image or runtime `pip install` is not used.

The real, isolated model probe then created a synthetic account and USER key,
wrote one preference message, committed it, and observed the task finish in
30.2 seconds. A USER-scoped search found both the synthetic code word and
Simplified-Chinese preference. The probe used MiMo-v2.5 for extraction and the
512-dimensional local Embedding service; it printed status only and stored its
test USER key in the mode-0600 isolated data volume. This is one functional
sample, not the fixed evaluation set or p95/cost gate.

## Clean Dano/Browser probe and upstream release blocker

A second isolated Compose project, `dano477-clean`, started from empty Dano and
OpenViking volumes, with the official OpenViking and llama.cpp images and the
candidate Dano image. HTTPS `https://localhost:18711/` used the persistent
trusted localhost certificate. The existing Codex in-app Browser completed
production-OA SSO into this isolated Dano account. Both memory switches were
initially off. Explicit memory consent left automatic collection off. A real
MiMo-v2.5 `memory_save` moved from processing to ready; a new browser chat
recalled the synthetic code word `青松47`. The management UI showed source,
timestamp and current status.

This probe exposed two release blockers in `pi-openviking@0.1.8`:

1. One explicit save produced two documents. Correcting the code word removed
   the unrelated language-preference document because both shared the same
   upstream source session. The old extension lacked a durable copy of that
   independent document before source removal. An isolated public OpenViking
   API probe confirmed that `content/write` can recreate the missing user-bound
   document, and a local extension change now stages a bounded, classified
   preservation plan before deleting the source. A unit test covers a lost
   deletion reply, restart and unrelated-document restoration. The temporary
   image then corrected `银杏93→银杏94` while preserving the separate Simplified
   Chinese preference document. Its initial source URI still contained the old
   code word, so the candidate now relocates exclusive retained documents to
   opaque user-bound URIs and labels their source as preserved after correction.
   A further isolated real-service correction of `松柏95→松柏96` completed and
   relocated two documents, including the independent language preference.
   The Dano image pinned to published 0.1.9 displayed the preserved source
   status and the two opaque document URIs in the real Browser. This revealed
   another correction flaw: the extracted document title still said `松柏95`
   although its body said `松柏96`. A new chat using MiMo answered the current
   code correctly but explicitly identified the conflicting old title. The
   0.1.10 extension guard rejects such a partial correction before any remote
   mutation and requires selecting a complete passage. PR #8 was merged and
   published. A full-content correction then exposed a second issue: moving
   the document URI again left an earlier completed job's source reference
   stale, so the task safely remained in `applying`. The local 0.1.11 candidate
   updates historical URI lineage. In the real isolated service the persisted
   task recovered to complete after restart, the browser refused a subsequent
   narrow `松柏96→松柏97` edit, and full-content correction completed. A fresh
   MiMo chat answered only `松柏97`, with no conflicting old memory. The
   independent Simplified-Chinese preference remained in export. PR #9 was
   merged and 0.1.11 appeared in the npm registry. The fixed image built and
   its running package version was confirmed as 0.1.11 with Pi keywords. The
   in-app Browser showed the current `松柏97` document and independently
   preserved language preference in anonymous owner-bound URIs.
2. A second save's OpenViking `session_commit` task completed and produced one
   update, but the old extension stayed at processing. It searched the original
   prompt with a result limit equal to the one changed document; an older
   related memory could rank first indefinitely. A real public API probe found
   the changed document through a search targeted at its validated USER URI.
   The local extension change uses that search for ready verification. The
   long retry period exhausted the configured bounded attempts before the
   change was running; local code now performs one read-only reconciliation
   of such exhausted processing tasks when a user runtime restarts. It never
   replays the original append or commit. In the real Browser, the previously
   blocked task changed to **ready** at 20:00:16 after the temporary 0.1.9
   image restarted, and its content was visible in the management list.

The extension fixes were merged as pi-openviking PRs #7, #8 and #9. Versions
0.1.9, 0.1.10 and 0.1.11 were published through npm Trusted Publisher with
provenance. All 250 package tests passed. The complete Dano suite passed with
Node 22 and the tested Python environment at reduced Vitest concurrency:
142 files, 1651 passed and one skipped. A first run with Node 24 failed to
load the Node 22 `fs-ext` native module; a Node 22 run at default parallelism
had one release-gate timeout, which passed alone and in the bounded full run.
With the tested Python virtual environment on `PATH`, Dano's full suite passed
142 files, 1651 tests and one skip; the host's default Python lacked `httpx`.

## Stopped-stack recovery and post-snapshot deletion

On 2026-09-23, the isolated `dano477-clean` stack was stopped before exporting
all six named volumes and the private deploy-control directory. The private
snapshot manifest at `/private/tmp/dano477-release-backup.iHNEgV/manifest.json`
records SHA-256, byte length and entry count for each archive; the snapshot
directory is mode 0700 and archives are mode 0600. The protected config archive
includes the OA/client configuration, TLS trust material and memory-service
configuration; the protected data archive includes encrypted USER credentials,
owner state, queue and source mappings. This is a local synthetic test backup,
not a production backup.

After the snapshot, a real in-app Browser action forgot the full synthetic
`松柏97` profile document. The completed governance job removed two old document
URIs and its source session while preserving the independent Simplified-Chinese
preference. A separate post-snapshot deletion ledger and owner-state overlay
were saved outside the older snapshot. The ledger records owner, removed URIs,
source IDs, expected current documents and the retained document body. It
contains no API key or removed fact text. The state overlay SHA-256 was checked
after import into a second set of volumes named `dano477-restore-*`.

The older OpenViking archive was imported unchanged into the second volume:
both deleted URI files were present before replay. Only internal OpenViking and
Embedding services were started. With Dano and nginx still stopped, a USER-key
client removed the old source and URIs, rewrote the retained preference, and
checked the three expected public documents. OpenViking's physical Markdown
file includes an internal `MEMORY_FIELDS` trailer; public read/write returns
only the document body, which the replay used. A first attempt compared the
physical trailer to the public readback and failed safely before Dano start;
the corrected idempotent replay passed. A USER-scoped recall of the forgotten
code returned no deleted fact. The replay result reported two deleted URIs,
one removed source, one preserved document and no recalled deleted fact.

Only after this readback were restored Dano and nginx started at the fixed
`https://localhost:18711/` entry. Browser management showed the preserved
preference and the two default documents, with no forgotten profile document.
A fresh chat said it did not know the code and used Simplified Chinese; a
second fresh chat used `memory_export`, found no code, and completed without
the old fact. The first chat also displayed `SUPERVISOR_OPERATION_FAILED`
despite a final answer; a minimal ordinary chat and the second memory query
completed normally. This transient error remains under investigation and is
not counted as a clean regression pass. A pre-deletion historical chat still
renders its historical answer; §11.1 explicitly distinguishes that transcript
from new-memory resurrection.

This exercise proves the local snapshot and replay sequence for one synthetic
deletion. A supported, repeatable backup/replay command, upgrade and matched
rollback rehearsal, independent owner revocation, and the full evaluation
remain release gates.

The candidate protected image now includes
`apps/dano/runtime/replay-memory-deletions.mjs`. Against the stopped restored
app it rejected a changed post-browser owner state with
`POST_SNAPSHOT_STATE_MISMATCH` before mutation. Restoring the exact
post-snapshot owner-state overlay and rerunning the same command passed with
two deleted URIs, one removed source, one retained document and three expected
documents. This is a tested per-owner replay step; the general ledger capture,
revocation and matched rollback procedure still need their release rehearsal.

## Candidate upgrade and matched rollback rehearsal

A third isolated stack began with Dano `0.2.34` and
`@josephyoung/pi-openviking@0.1.8`, plus the same fixed official OpenViking
`v0.4.20` and Embedding image. Its volumes and OpenViking account were new.
The in-app Browser completed OA SSO, found both memory switches off, enabled
only explicit memory, saved the synthetic `枫桥31` fact to ready, and recalled
it from a separate chat. With OpenViking then stopped, a second `溪桥82` save
entered `session_unknown`. All six volumes and private deploy-control files
were exported after stopping Dano, nginx and Embedding. The private baseline
manifest at `/private/tmp/dano477-upgrade-baseline.189h5urv/manifest.json`
records the seven archive hashes. The baseline state had one ready operation
and one pending `session_unknown` operation.

The same stack's app was recreated with the actual `0.2.35` candidate image
(`8b1524835ee3`), keeping the old volumes and fixed OpenViking version. The
old ready fact remained visible in management and a fresh MiMo chat answered
`枫桥31`. The old offline task became “结果待核实” and was **not** blindly
resent; its remote session had never been confirmed. This is a safe ambiguity
outcome, not a successful old-queue delivery. The candidate then saved a new
synthetic dark-blue chart preference to ready. Its operation, document URI,
digest and content were recorded in a private upgrade-window reconciliation
file outside the old snapshot, alongside stopped candidate data and OpenViking
archives.

For matched rollback, a fourth isolated stack imported the **old** six-volume
snapshot into fresh volumes and ran the old `0.2.34` image (`ff9dcbf3d807`).
Before Dano started, the USER-bound replay command checked the restored
owner-state hash and public document set: the old profile remained, while the
candidate-window document and source were absent. The physical profile file
had one trailing newline that OpenViking's public read omits, so its replay
ledger used the exact public body. Browser management showed the old ready
fact and the unresolved old queue. The new ready operation was not silently
carried into the old state; its synthetic fact was explicitly resubmitted from
the private reconciliation file through the old browser/model/tool path,
reached ready under a **new** operation ID, and a fresh chat answered both
`枫桥31` and the dark-blue preference. This demonstrates data-matched rollback
and explicit reconciliation of one synthetic upgrade-window operation. It does
not establish automatic operation migration or a production-safe resubmission
policy for arbitrary users.

Compatibility observed in this rehearsal: the `0.2.34`/`0.1.8` owner-state
version 1 and OpenViking `v0.4.20` data were readable by `0.2.35`/`0.1.11`;
the reverse path used the old snapshot and reissued the one newer operation.
No OpenViking storage-format migration was exercised. The old ambiguous queue
exposed a genuine extension recovery gap before #477 can pass.

The gap was reproduced in two tests: a Session creation that failed before
OpenViking accepted it left `session_unknown` forever, and pause after that
failure left the payload pending across a new authorization epoch. The
`pi-openviking@0.1.12` fix retries only creation of the **same empty Session
ID** after USER-scoped absence and checks current authorization before that
mutation. It does not retry an unknown message append or commit. The two tests
were red on 0.1.11 and green on 0.1.12; all 252 extension tests passed. A
one-off container from the candidate image with local 0.1.12 bits advanced
the *actual old snapshot operation* against fixed OpenViking `v0.4.20` from
`session_unknown` through `session_created`, `message_delivered`, `processing`
to `ready`, with the original Session ID and one memory document. Extension
PR [#10](https://github.com/josephyoung/pi-openviking/pull/10) merged;
Trusted Publisher run `35879801711` succeeded and published 0.1.12. Dano's
frozen lockfile installed the exact registry tarball after npm CDN propagation.
The complete protected image `5dddd4a48172` reports Dano `0.2.35`, exact
extension `0.1.12`, both Pi keywords and the replay command. A fresh fifth
isolated stack imported the **old** six-volume snapshot, started the fixed
OpenViking/Embedding services and this formal image, then passed real in-app
Browser acceptance: the old pending `溪桥82` task became “已记住”, its profile
document retained the earlier `枫桥31` fact, and a new MiMo chat correctly
answered both codes. This closes the specific session-creation old-queue
recovery gap, without changing the separate upgrade-window reconciliation
policy requirement.

## Frozen evaluation baseline, 2026-09-24

The 80-case synthetic dataset was committed as
[`issue477-evaluation.json`](fixtures/issue477-evaluation.json) at `5616f135`
before executing it. It fixes 20 recall, 10 correction, 20 isolation,
10 deletion, 10 irrelevant-request and 10 authorization cases, with three
repetitions each; the five-user/100-request workload, model, machine,
configuration, official MiMo list prices and acceptance thresholds are also
frozen there. The tested Podman VM had four CPUs and 4,076,376,064 bytes of
RAM. MiMo-v2.5 extraction of the five four-fact source Sessions completed in
87.2–95.1 seconds, producing one to four documents per synthetic owner.
This is batch extraction timing, not the single-fact explicit-save p95 metric.

The real OpenViking `v0.4.20` USER-key run produced these sanitized per-attempt
files: [recall](evidence/issue477-baseline/recall.json),
[isolation](evidence/issue477-baseline/isolation.json) and
[irrelevant requests](evidence/issue477-baseline/irrelevant.json).
All 60/60 recall searches found a document containing the expected fact and
none returned the next owner's forbidden value; this is source retrieval,
not a Dano/MiMo answer-correctness result. All 60/60 cross-user probes kept
the target user's content and state isolated. Search, read, write and tree
export returned 403 in 48 probes. The 12 same-named Session-message probes
returned 200 because OpenViking wrote to the caller's own Session namespace;
the target Session context was unchanged, and the caller's context contained
its synthetic marker without the target fact.

The initial relevance configuration **failed** its fixed requirement:
0/30 irrelevant-request repetitions would avoid injection. Every real search
returned at least one memory above the configured `minimumScore=0.1`; the
published extension's context hook selects those entries under its token
budget. This is a measured selection outcome, not 30 completed Dano chats.
The irrelevant top scores ranged 0.354–0.535, while relevant top scores ranged
0.396–0.755. A single higher vector-score cutoff would also discard some
required facts. The candidate therefore needs a separately frozen and tested
relevance stage; these failed baseline results remain part of the record.

The formal `0.2.35`/`0.1.12` Browser regression also completed ordinary
MiMo text chat, a model-triggered `bash ls` tool call, a real upload of the
fixed synthetic `shapes.png` (model identified red circle, blue square and
yellow triangle), and an `ask_user_question` radio card submitted as
“咖啡” and returned to the model. These observations do not cover the remaining
Skill, Field Assist, Heimdall, dual-user or 100-request gates.

## Candidate 2: bounded local reranking

The first relevance repair used Dano `0.2.36`, exact pi-openviking `0.1.12`,
the same OpenViking/Embedding versions and a separately pinned upstream
llama.cpp reranking service. Its `bge-reranker-v2-m3-Q4_K_M` asset has SHA-256
`e186a244ed455b4ab66ec64339ce7427a6ae13f5c0b5e544de96e50f0f8b3673`.
The unchanged 80-case dataset and the new private limits were frozen in
[`issue477-evaluation-candidate2.json`](fixtures/issue477-evaluation-candidate2.json)
before formal execution. OpenViking `v0.4.20` `/find` is a QUICK vector path,
so merely enabling its server-side reranker cannot affect this extension's
recall. Dano reranks USER-scoped candidates before injecting them, and a
missing, timed-out or malformed reranking response omits memory for that
request. The published extension and user-bound credential scope are unchanged.

The formal image `46443b7b95aa` started with the private reranker configuration.
All 143 Vitest files passed (1,655 tests, one skipped); type/Svelte checks had
no diagnostics. The [candidate 2 retrieval results](evidence/issue477-candidate2/formal-retrieval.json)
contain 90 attempts: 60/60 expected source documents selected, 30/30
irrelevant requests selected no memory, no cross-owner forbidden fact, and
search plus reranking p95 324.45 ms. In the real in-app Browser, a fresh
MiMo chat answered both stored upgrade codes after restart; an unrelated
arithmetic chat returned 45. These are Browser observations, while the
per-attempt file records service selection, not 90 Dano model answers.

The first [five-user, 100-request selection probe](evidence/issue477-candidate2/five-user-selection.json)
then exposed a concurrency flaw: reranking all five vector candidates with
the 750 ms timeout selected only 7/70 relevant facts. 30/30 irrelevant
requests omitted memory; steady selection p95 was 962.5 ms. This is a
supplemental memory-selection workload, **not** the Spec's 100 complete user
requests or a release pass. The failure is retained. The fixed corpus's
expected fact was already in the first vector result for 20/20 distinct
recall cases. Exploratory probes capped reranking at two candidates and used
a 900 ms timeout; 70/70 relevant and 30/30 irrelevant requests then selected
correctly, with steady selection p95 793.64 ms. A separately frozen candidate
must still implement and repeat that result, including Dano host overhead.

## Candidate 3: frozen bounded retrieval retest

The unchanged 80-case dataset and the two-candidate, 900 ms reranker limits
were frozen in [`issue477-evaluation-candidate3.json`](fixtures/issue477-evaluation-candidate3.json)
at `cd064666` before the formal rerun. Dano `0.2.37` limits both the USER-scoped
OpenViking `/find` request and the local reranker input to two candidates. The
protected image `8cb0ff821faf` installed the exact published
`pi-openviking@0.1.12`. Its running package metadata included both required
Pi keywords. All 143 Vitest files passed (1,657 tests, one skipped), and the
server and Svelte checks had no diagnostics.

The [formal retrieval results](evidence/issue477-candidate3/formal-retrieval.json)
record all 90 unchanged recall and irrelevant-request attempts across three
repetitions. Expected source selection was 60/60, irrelevant requests selected
no memory in 30/30 attempts, and no next-owner forbidden fact was read.
USER-scoped search plus reranking p95 was 223.25 ms; reranking alone p95 was
209.41 ms. The [five-user concurrent selection results](evidence/issue477-candidate3/five-user-selection.json)
record 100 attempts: relevant selection 70/70, irrelevant omission 30/30,
first-round p95 690.97 ms and steady selection p95 817.31 ms. That workload
uses real OpenViking and reranker requests on the four-CPU isolated Podman VM,
but it does **not** include five complete Dano/MiMo user requests, model token
usage, extraction cost, or Dano host overhead. It cannot satisfy the Spec's
full latency and cost release gate by itself.

The real in-app Browser on the running `0.2.37` image completed OA-backed
MiMo chats through the fixed trusted `https://localhost:18711/` entry. In a
fresh chat it answered both stored upgrade facts, `枫桥31` and `溪桥82`; a second
fresh chat answered the unrelated `19×23` request as `437`, with no memory fact
in the visible answer. This is direct model/browser evidence for those two
requests, not a 60/30 model-answer audit.

The same final image passed additional real Browser regressions: MiMo
triggered `bash ls` and reported `uploads`; an `ask_user_question` radio card
accepted “茶” and the model repeated it. A textarea question exposed Field
Assist, and its MiMo-backed polish changed the synthetic value “处理个人事务” to
“需处理个人事务”; the card was cancelled before any OA submission.

The first “请假” quick action exposed a protected-stack configuration gap:
the model could find no OA Skill. This stack's supervisor profile had an empty
`trustedSkillPaths`. Copying the image seed into the Agent Config Directory
did not enable it, because protected sessions load only the profile's trusted
image paths. After adding the exact image-owned `open-websearch` Skill path to
the **private local acceptance profile** and restarting Dano, a fresh Browser
chat invoked and read that Skill's `SKILL.md`. A model-triggered Bash
`test -r` found the trusted Skill readable and
`/etc/dano-protected/agent/settings.json` unreadable. This validates generic
Skill loading and the worker's Heimdall read boundary on this image. The
business “请假” Skill itself is absent from the isolated stack, so its OA
workflow remains unaccepted. The deployment contract now states the protected
Skill allowlist requirement explicitly.

A separate local acceptance initialization defect left `{产品名称}` in the
protected Agent Config Directory's `SYSTEM.md`, although the repository product
name is “小络助手”. It made the model answer “我是，公司内部OA智能助手” in a test
conversation. With Dano stopped, the image's `render-system-prompt.mjs`
replaced that template using the image's product configuration; a new Browser
chat then answered “我是小络助手，公司内部 OA 智能助手。” The acceptance configuration
must render the prompt before release. Sending `/compact` in the browser
composer produced an ordinary model message rather than Pi compression, so
that attempt is **not** counted as the required compression regression.

## Candidate 4: review fixes and repeat acceptance

The code and unchanged 80-case dataset were frozen in
[`issue477-evaluation-candidate4.json`](fixtures/issue477-evaluation-candidate4.json)
at `d243610b`. The protected image `204933dea05f` reports Dano `0.2.38` and
the same published `pi-openviking@0.1.12`. The offline recovery command now
uses Dano's `MemoryCredentialStore` rather than manually opening and
decrypting the USER credential file; the existing store checks the file owner,
mode, size, symlink boundary, key version and authenticated owner. The release
checker parses the pnpm lockfile as YAML. The frozen lockfile install, full
server/Svelte check, targeted credential/reranker/config tests and deployment
release check passed.

A one-off `0.2.38` container mounted the current private volumes read-only and
an older ledger with no network. It rejected the mismatched state hash at
`load` before any credential or remote operation. Against the previously
restored isolated volumes, with only official OpenViking and Embedding running,
the same image replayed the ledger successfully and checked two deleted URIs,
one removed source, one retained document and all three expected documents.
This revalidates the repaired credential path on the real service for the one
synthetic owner; it does not create a general multi-owner ledger policy.

The [candidate-4 formal retrieval results](evidence/issue477-candidate4/formal-retrieval.json)
record 60/60 relevant source selections, 30/30 irrelevant omissions and zero
forbidden cross-owner reads; selection p95 was 279.75 ms. The
[five-user concurrent selection results](evidence/issue477-candidate4/five-user-selection.json)
record 70/70 relevant selections and 30/30 irrelevant omissions, with
first-round p95 849.68 ms and steady p95 958.60 ms. The latter has only
41.40 ms margin below the 1-second selection threshold **before** Dano host
overhead; it is not a pass of the complete-request gate. In the real in-app
Browser, a fresh MiMo chat answered `枫桥31` and `溪桥82`, but also volunteered
an extra recently saved synthetic marker `山河58`; a separate unrelated
arithmetic chat answered only `391` for `23×17`. The extra fact needs review
in the model-answer quality audit, rather than being silently counted as a
clean exact answer.

## Candidate 5: single-candidate selection retest

The unchanged 80-case dataset and thresholds were frozen with
[`issue477-evaluation-candidate5.json`](fixtures/issue477-evaluation-candidate5.json)
at `8c64029b` before this run. Only the maximum USER-scoped vector search and
local reranker candidate count changed from two to one. The final image remains
`0.2.38` with exact `pi-openviking@0.1.12`; official OpenViking, Embedding and
reranker versions and the four-CPU Podman VM are unchanged. The separate
[formal retrieval evidence](evidence/issue477-candidate5/formal-retrieval.json)
records 60/60 expected-source selections, 30/30 irrelevant omissions, zero
cross-owner forbidden hits and 185.35 ms selection p95. The
[five-user concurrent selection evidence](evidence/issue477-candidate5/five-user-selection.json)
records 70/70 relevant selections and 30/30 irrelevant omissions across 100
attempts, with 710.17 ms cold-round and 537.83 ms steady selection p95.
These are **memory-selection-only** calls, not complete Dano/MiMo requests.

The protected local acceptance config now uses one candidate. An app restart
temporarily left nginx with its old upstream IP, producing HTTP 502; restarting
nginx restored the fixed HTTPS entry to HTTP 200. The authenticated Browser
restored its prior chat after reconnect. In a fresh MiMo chat asking for only
the two saved codes, the model answered `枫桥31` and `溪桥82` but again volunteered
the unrelated `山河58` marker despite the request not to add information.
Reducing the candidate count improves measured selection latency but does not
resolve this answer-quality concern. The final image's model-triggered
`bash ls` also completed and reported `uploads` before this restart.

Additional `0.2.38` Browser regressions completed on the same fixed HTTPS
entry. MiMo invoked `ask_user_question`, rendered a two-option radio card,
accepted “茶” and repeated the submitted choice. It invoked and read the
image-approved `open-websearch` Skill and returned its frontmatter name. One
real upload of the fixed external `shapes.png` reached the chat; MiMo correctly
identified the red circle, blue square and yellow triangle in left-to-right
order. The first, longer form prompt remained waiting and was cancelled; the
shorter retry completed, so this does not establish a form failure root cause.

The first `/compact` attempt on a one-turn chat failed because Pi had no
history outside its configured 20,000-token recent window. For a controlled
regression, the local acceptance profile temporarily enabled slash commands
and set Pi's `keepRecentTokens` to one. After two ordinary turns, `/compact`
showed the active compaction state, then completed. The newest persisted
session contained one `compaction` entry between the second and third user
turns; a subsequent MiMo turn correctly repeated the synthetic first-turn
test word. Both temporary configuration overrides were removed, Dano/nginx
restarted, and the fixed HTTPS entry returned HTTP 200. The original default
settings remain in place; this is a real Pi compaction path check under a
short acceptance window, not a long-context performance benchmark.

## Candidate 6: multi-owner replay command, 0.2.39

The protected image `ce3db0c7b493` contains Dano `0.2.39` and exact
`pi-openviking@0.1.12`. The offline replay command now accepts the original
version-1 single-owner ledger and a version-2 list of owner entries. It
preflights all state hashes, owner bindings and USER credentials, then verifies
every remote identity before the first remote mutation. A valid partial replay
can be run again. Node syntax, the full Dano server/Svelte check, the server
build and all 143 Vitest files (1,657 passed, one skipped) passed. Both release
checker modes and the protected image's frozen lockfile build passed.

The [sanitized replay summary](evidence/issue477-replay-v2/summary.json) records
the real-service results. The old deletion ledger passed unchanged against
the restored isolated OpenViking service: two deleted URIs, one removed source,
one retained document and three expected documents. A new two-owner ledger
used two existing isolated USER accounts; it removed one newly staged
synthetic document from each account and left their nine original documents
unchanged. An immediate second run passed, confirming idempotent replay.
Duplicate owner entries were rejected as `INVALID_LEDGER` with networking
disabled. Corrupting only the second owner's post-state hash yielded
`POST_SNAPSHOT_STATE_MISMATCH` at `load`; both staged documents still existed
after that failure, and a later valid replay removed them.

This covers the mechanics of a supplied multi-owner ledger. It does **not**
capture deletion/revocation events automatically outside older snapshots, nor
does it define arbitrary upgrade-window reconciliation. Those remain hard
release gates; the new command alone is not a recovery guarantee.

The final `0.2.39` protected image was then started on the established
`https://localhost:18711/` acceptance entry. The app container reported
healthy, the existing localhost CA verified HTTPS 200, and the authenticated
in-app Browser restored its prior Pi session after reload. In a new chat,
MiMo answered the synthetic prompt `0.2.39 验收：仅回复“服务可用”。` with `服务可用。`.
This confirms final-image browser connectivity and one complete ordinary
request, not the fixed memory-answer or five-user load gates.

The same final image's installed `pi-openviking` manifest reported version
`0.1.12`, Pi keywords `pi-package` and `pi-extension`, and the standard
`dist/standard.js` entry. In a fresh Node 22 `pi 0.85.1` RPC session, loading
that exact standard entry produced the expected notice that unprotected Pi
does not enable long-term memory, and `get_state` succeeded without a provider
request. A first attempt with Node 24 could not load the Node 22 `fs-ext`
binary; the tested runtime is Node 22. On the protected final image, a new
authenticated Browser chat asked for the two digits after the previously saved
`枫桥` code; MiMo answered exactly `枫桥31`. This adds one final-image model
answer, not the fixed 20-case, three-run recall acceptance.

The final-image Browser also completed the three required ordinary runtime
checks at the fixed HTTPS entry: MiMo answered a plain-text request, invoked
`bash ls` and confirmed `uploads`, and read one real upload of the fixed
synthetic `shapes.png`. It identified the red circle, blue square and yellow
triangle in order. The same screenshot showed the uploaded image and answer.
This is a single final-image pass, not all T-12 repetitions or the absent
business OA Skill.

## Remaining release gates

### Candidate 0.2.40 recovery journal (2026-09-24)

The candidate adds a separate, host-owned memory-recovery volume. A stopped
service one-off bootstrap copied one existing owner's state into that volume;
the protected app then restarted on the fixed HTTPS entry with image
`localhost/dano477-protected:0.2.40`, and both the app health check and HTTPS
request passed. The deployment release checker passed with the distinct
recovery volume. New owner-state revisions are mirrored there; destructive
remote methods write an owner-bound, fsynced intent first. Unit coverage
includes missing/mismatched mirrors, partial event tails, and a failed mirror
that poisons later memory access. The full repository check, tests, server build
and release checker passed for this candidate.

In the authenticated Browser, an explicit `memory_save` for the synthetic
`栀霞72` fact reached `已记住` and merged into `profile.md`. A selective
correction was rejected with `MEMORY_TARGET_AMBIGUOUS`. Diagnosis showed that
the target text occurred once in the three live OpenViking documents, but the
owner ledger had multiple operations pointing at the merged document. The
package governance barrier requires one matching source digest in that case
and rejected the operation before creating a job. This is a real selective
governance acceptance gap; it must not be counted as a successful correction.

No Browser deletion intent was recorded during this test. An attempted click
on the final `确认清空` control was rejected by automatic approval review because
the browser session was not independently proved to be a disposable test
account whose entire memory could be erased. The confirmation dialog was
cancelled. No workaround or indirect deletion was used. The journal's remote
deletion path therefore has unit evidence but no real-service Browser proof.
Automatic replay of the new journal, checkpointing against arbitrary older
snapshots, and credential/upgrade-window reconciliation are still missing.
This candidate is **not** a rollback or release acceptance.

The journal reader now validates each event's version, unique ID, timestamp,
exact owner and mutation fields, including owner-bound document URIs. A failed
append poisons that owner's memory runtime before any remote deletion can be
sent. The updated candidate passed 144 Vitest files (1664 tests passed, one
skipped) when the existing `httpx`-capable local Python was selected; the
default Python lacked `httpx`, and one full-suite concurrent run timed out in
an unrelated Skill test, which passed in isolation. Type/Svelte checks, server
build and release check passed. The final rebuilt protected image
`localhost/dano477-protected:0.2.40-journal-final` rejected a cross-owner
tampered event in a disposable container probe. The isolated app and nginx
were recreated from that exact image; the fixed HTTPS entry returned 200
without changing the localhost CA. Its health check reached `healthy`, and a
fresh in-app Browser tab reconnected to Alice's existing chat on that entry.

### Recovery checkpoint and automatic journal replay candidate

The protected image now contains `runtime/reconcile-memory-recovery.mjs`.
Its stopped-stack `checkpoint` binds each owner-state hash to a validated
journal prefix. `preflight` checks the restored state, full owner set,
checkpoint prefix and encrypted USER credential before remote work. `replay`
verifies all identities, applies only post-checkpoint events in order, reads
back the affected sources/documents, and writes the newer owner states only
after every owner's readback succeeds. A partial remote failure is retryable.
The current candidate writes a private receipt bound to the exact checkpoint
and newer state before overlay. A post-overlay retry requires that receipt;
the targeted regression test first reproduced acceptance of an altered old
state hash and now rejects it before any remote contact. A removed receipt
also fails preflight. The updated code is in the rebuilt `0.2.41-receipt`
image described below. The targeted eight-test suite also confirms that a
bad checkpoint for Bob prevents replay of Alice's deletion before either
remote client is contacted; a valid two-owner checkpoint replays both
deletions. This uses simulated remote clients, not a two-owner real-service
rollback.

With the isolated finalqueue app/nginx stopped, image
`localhost/dano477-protected:0.2.40-reconcile` created a private checkpoint
for one existing owner. Its preflight and real OpenViking-backed replay passed
with **zero** post-checkpoint events; the app returned healthy at the same
HTTPS entry and the in-app Browser restored the existing chat. This verifies
the real deployment's file paths, credential and clean no-op recovery only.
The executable test suite also covers two post-checkpoint deletions (one
source session and one document) through the actual HTTP client against a
local simulated service, old-state restoration, idempotent retry, altered
journal prefix, new owner and remote failure before state overlay. The test
uses no real OpenViking deletion, so it is not a real-service deletion replay.

Automatic approval review initially rejected creation of a dedicated
OpenViking test USER and synthetic remote data. After the operator explicitly
confirmed that permission, a new USER named
`dano477_reconcile_synthetic_01` was created in the isolated OpenViking
service. Its encrypted USER credential and owner state were kept in separate
`dano477-reconcile-test-data` and `dano477-reconcile-test-recovery` volumes;
the existing OA Browser owner was not changed. A synthetic document and empty
Session were present before a one-owner checkpoint (`journalBytes=0`). Two
later recovery intents targeted only that USER's Session and document. The
rebuilt image `adab70014794` passed preflight with `owners=1, events=2`,
then replay removed both on the real OpenViking service. USER-bound readback
reported neither document nor Session present. An immediate second replay
also passed, proving this two-event path is retryable. A second checkpoint
captured the existing journal prefix, then the recovery mirror advanced to
revision 2 while the stopped test data volume retained revision 1. Another
two intents were replayed against a restaged document and Session. The
readback found neither target and the restored local state advanced to
revision 2. A further stopped-stack rehearsal copied the current protected
data and OpenViking volumes into distinct old-volume clones and compared their
contents before resuming the original stack. After two new deletion intents,
the current service removed the synthetic document and Session. A separate
official `v0.4.20` OpenViking instance booted from the **old** clone, where
USER-bound readback still found both. Its matched old protected data was at
revision 2, while the independent recovery mirror was at revision 3. The
one-owner/two-event preflight and replay passed; readback then found neither
document nor Session, and the restored state advanced to revision 3. A second
replay passed. The alternate instance was stopped and removed. Sanitized
per-step evidence is in
[`issue477-recovery-journal/summary.json`](evidence/issue477-recovery-journal/summary.json).
The exact synthetic USER was then removed through the official admin API, and
the separate old-volume clones, test data/recovery/checkpoint volumes and
probe script were removed. The original `dano477-finalqueue` stack remained
healthy on the fixed HTTPS entry.
This proves one synthetic matched old-volume deletion replay on the same
candidate version; it does not cover an arbitrary old-version migration or
general upgrade-window operations. The command deliberately fails closed for new owners,
new writers, pending governance, retirement and invalid/stale credentials;
general upgrade-window reconciliation and arbitrary old snapshots remain
release gates, as do the full AC/T Browser and quality matrices.

On the rebuilt `0.2.41-receipt` image, a second real-service rehearsal used
two temporary synthetic USERs in one temporary account. Each had a distinct
encrypted credential, protected owner state and real OpenViking document.
After a two-owner checkpoint, both independent recovery mirrors advanced one
revision and logged a deletion; the old local states were restored. Preflight
reported two owners and two later events. Replay removed both public
documents, overlaid both newer states and wrote private checkpoint-bound
receipts; a second replay passed. USER-bound readback found neither document.
The temporary account was deleted through the official admin API. The
[sanitized run summary](evidence/issue477-two-owner-receipt/summary.json)
records the image and aggregate assertions. This tests
two-owner state restoration and remote deletion on the current service; it
does not recreate an older image/data-volume pair or cover source Session
removal, revocation, new writers or credential rotation.

For this candidate, the rebuilt protected image completed successfully.
`pnpm run check` reported no server or Svelte diagnostics, the release checker
passed, and the bounded full Vitest run passed 145 files (1,670 tests passed,
one skipped). An earlier concurrent full run had one unrelated provider Skill
gate timeout; that test passed alone and in the bounded full rerun.

The exact rebuilt image `adab70014794` replaced the local app container and
reached `healthy`; the persistent localhost HTTPS entry returned 200. In a
fresh authenticated in-app Browser chat, MiMo returned the requested plain
text, invoked `bash ls` and reported `uploads`, then read one upload of the
fixed external `shapes.png` and identified red circle, blue square and yellow
triangle in order. These three final-image checks cover the repository's
Podman runtime regression floor, not the remaining §11.2 business Skill,
dual-user or full model-quality release gates.

### Merged-document correction package 0.1.13 (2026-09-24)

The ambiguous live correction above exposed a package defect: OpenViking
coalesced two source operations into one document and paraphrased the selected
fact, so neither stored source digest equaled the unique document sentence.
[pi-openviking PR #11](https://github.com/josephyoung/pi-openviking/pull/11)
now accepts that case only after the selected sentence occurs exactly once and
the remaining text is classified unrelated. It revokes the complete source
group to prevent old Session replay and checks the exact operation-ID set in
the state transaction, so a writer appearing during inspection fails closed.

The independent package's 254 tests and type check passed. An isolated
official OpenViking v0.4.20 account with two real USER-bound source Sessions
completed the corrected-document readback; the unrelated fact remained, both
Sessions were deleted, the old URI was relocated, and export marked both old
sources revoked. The synthetic account and one-off container were deleted.
GitHub Trusted Publisher
[run 35923522460](https://github.com/josephyoung/pi-openviking/actions/runs/35923522460)
published `0.1.13` with provenance; the npm registry's exact-version metadata
and integrity matched the Dano lockfile. Dano `0.2.41` pins it exactly.
The repository check passed and a complete Vitest rerun with Python `httpx`
passed 145 files, 1670 tests, with one existing skip.

The first `0.2.41` protected image was built from that source revision with the exact
published `0.1.13`. Because the builder's direct GitHub TLS connection failed
while installing the pinned `open-webSearch` Skill, the build-only Git source
was routed through a temporary read-only local mirror of upstream
`v2.1.11` (`3094fa5`); the shipped Dockerfile's Skill installer and pinned
source stayed in use. The mirror service and clone were removed after the
build. The image reported both package versions and both required Pi keywords.
The fixed HTTPS Compose stack passed health and the deployment smoke check
(anonymous Cookie, Client, SSE, message and disconnect).

In the authenticated in-app Browser on `0.2.41`, the merged `profile.md`
contained four synthetic facts and the selected `栀霞72` phrase once. The UI
corrected that phrase to `清禾93`, relocated the retained document to an opaque
URI, preserved the other three facts, and showed all old sources as revoked.
A fresh chat asked for the recovery-test code; MiMo answered only `清禾93`.
The same `0.2.41` Browser session also invoked `bash ls` and returned
`uploads`, then uploaded the fixed external `shapes.png` once; MiMo identified
red circle, blue square and yellow triangle in order. The active per-User
workspace was on the named XFS data volume; a `bwrap` write/remove probe as
the deployed app UID passed against that exact workspace with the deployed
no-`/proc` binding configuration. This closes the previously observed
merged-document Browser blocker, but it is one correction case, not the full
§11 correction, deletion, two-user or performance acceptance.

The installed published `0.1.13` was also loaded through the actual pinned Pi
`DefaultResourceLoader` using both entries, twice each, locally and in the
rebuilt protected image `3d8569c7463f`. The ordinary Pi
entry registered eight tools including `memory_save` and the `user_bash`
handler; the Dano factory registered one memory tool. Neither produced an
error or duplicate registration. This checks loading and registration; an
ordinary protected Pi CLI chat was then tested separately as described below.

A disposable probe layer on the rebuilt image used the **published**
`0.1.13` standard entry and pinned Pi `0.85.1` through the public protected
Pi CLI RPC interface. It used the same MiMo-v2.5 model, its pinned tokenizer
revision and a fresh synthetic USER in the isolated OpenViking service. The
CLI showed default-off memory and separate collection consent, accepted its
own confirmation prompt, let the real model call `memory_save`, reached
`ready`, displayed the saved content and source, recalled the fact after
`new_session`, and paused memory. Automatic collection stayed unauthorized;
the USER key did not appear in captured RPC events or stderr. The synthetic
account was deleted through the official admin API. The
[sanitized result](evidence/issue477-published-cli/summary.json) records these
assertions. This is one ordinary CLI functional path; it does not establish
the complete pair audit or the Dano multi-user/browser and quality matrices.

The updated `0.2.41-receipt` image contains product `0.2.41`, published
pi-openviking `0.1.13` and the checkpoint receipt code. The fixed HTTPS
Compose stack started with this image, all five services reached healthy or
running state, and `smoke:deploy` passed Cookie, Client, SSE, message and
disconnect. In a fresh authenticated in-app Browser chat on this image,
MiMo recalled `清禾93`, executed `bash ls` and returned `uploads`, then read
one upload of the fixed synthetic image and answered “红色圆形、蓝色正方形、黄色三角形”。
This reruns the image-sensitive chat/tool/upload checks; it does not repeat
the complete governance or multi-user matrix.

On the same rebuilt image and fixed HTTPS entry, two authenticated Browser
tabs sharing Alice's account exercised one reversible pause transition. The
initial state was memory enabled and automatic collection unauthorized.
After pausing in the first tab, the second tab displayed the paused state;
a fresh chat asked for the already saved synthetic recovery code and MiMo
answered “不知道。” After enabling memory again in the first tab, another fresh
chat in the second tab answered “清禾93”. The final state was memory enabled
and automatic collection still unauthorized. This is one same-owner,
cross-tab pause/recall/resume observation, not an independent second user or
the full three-repetition pause and in-flight-save matrix.

The same Browser account then separately authorized automatic collection and
sent one synthetic stable preference in a new chat, explicitly without a
`memory_save` request. The management UI showed an automatic-collection
operation move from processing to ready, with an automatic source. A fresh
MiMo chat recalled the preference. After revoking collection consent, the UI
showed automatic collection unauthorized while a fresh chat still recalled
the previously saved fact. The UI then forgot only that synthetic sentence;
its source changed to revoked, a new chat answered that it did not know the
preference, and a separate new chat still recalled an unrelated saved code.
Long-term memory remained enabled; collection authorization remained revoked.
The [sanitized Browser summary](evidence/issue477-auto-collection-browser/summary.json)
records the assertions. This is one real Browser case, not the sensitive-data
exclusion, inference-filter, in-flight revocation or three-repetition matrix.
Two additional fresh chats, one with only a fake API key and one with a benign
preference alongside a different fake API key, produced no new visible save
operation or management document during the observation window. Collection
authorization was revoked afterward. Because the selector's execution and
fact-level decision were not independently observed, these are negative
Browser observations, not proof of the full sensitive-data exclusion gate.

The business OA Skill regression was probed without writing to OA. A
read-only production inventory found the generated “请假申请” Skill and the
configured OA URL/tenant-key entries; the older documented
`dano-a-oa-qingjia` path is not the installed Skill. A disposable layer on
the candidate image loaded that Skill as a protected trusted resource with
the two OA configuration entries. In the authenticated in-app Browser, the
“请假” quick action discovered the Skill, rendered the four-operation choice,
and then rendered all six fields of the new-leave form. The form was cancelled
without any business mutation. Its dynamic user-list selector failed because
the same-origin business path returned `200 text/html` from the Dano entry,
not JSON. An independent request to that path reproduced the response.

The model's ordinary protected `bash` worked. The original broker PATH
omitted the image's Python virtual environment; Python discovery and the
Skill's read-only commands surfaced `SUPERVISOR_OPERATION_FAILED`. Adding
that virtual environment to the **probe profile only** let a
Browser-triggered Python command import `httpx`. A direct app-container
read-only option lookup still returned
`authentication unavailable`: the available OA URL/tenant-key settings did
not yield a usable business authorization header for this generated Skill. Passing
business credentials directly into model-triggered bash would violate the
protected worker boundary and was not done. The probe layer/profile and OA
environment were removed from the running stack; the original receipt image,
supervisor profile, fixed HTTPS entry and smoke check were restored. The
[sanitized OA summary](evidence/issue477-oa-skill/summary.json) records the
observations. Functional business choices, authenticated requests and the
complete OA Browser regression remain release blockers.

The direct app-container option lookup had no Browser Login Session binding,
so that call alone does not establish a login-bound failure. Read-only review
of the installed generated Skill found that its `auth_headers()` runs before
the OA request and requires its own environment or browser-state credential
source. The protected broker deliberately projects neither production tenant
keys nor browser storage into a worker. Correcting `broker.path` alone cannot
make this unchanged Skill reach the Dano provider transport; a login-bound
Browser retest and a reviewed credential-boundary integration are still needed.
The Python path requirement is now documented for protected deployment.

### Five-user complete-request diagnosis (2026-09-24)

The `0.2.42` candidate replaces the tokenizer's single-flight rejection with a
bounded per-model FIFO. Five simultaneous Dano/MiMo requests then recalled the
correct owner fact and answered it in a first pilot. The canonical protected
image was built and ran with published `pi-openviking@0.1.13`. The matched
[100-request memory-on](evidence/issue477-candidate6/full-workload-on.json)
and [memory-off](evidence/issue477-candidate6/full-workload-off.json)
workloads each completed 100 real Dano/MiMo requests for five authenticated
synthetic owners. With memory on, strict answer matching passed 63/70 recall
attempts; all 30 irrelevant answers contained no owner fact. The memory-off
workload answered 6/70 recall questions by chance. The on/off prompt p95s
were 20.145/25.466 s and estimated model costs were $0.01209/$0.01859.
Answer length and cache variation prevent attributing the cost difference to
memory alone.

The same frozen workload on a disposable container with sanitized extension
and transport tracing produced [direct evidence](evidence/issue477-candidate6/traced-workload.json):
70/70 expected sources returned, 70/70 relevant contexts injected, 30/30
irrelevant contexts omitted, 759 ms recall-wait p95, 837 ms maximum, and 213
injected tokens maximum. It completed 100/100 requests, but strict answer
matching fell to 57/70. Two additional context logs were cache reuses within
an existing request, not extra requests. The one-candidate reranker and the
tokenizer were not the remaining source of those wrong answers. A targeted
answer probe showed both weekday paraphrases (for example, `星期三` for `周三`)
and the model misclassifying a personal-memory question as an unsupported OA
business operation. The production OA capability boundary remains intact; a
generic clarification for memory-only answers is being evaluated separately.

In the [next disposable candidate](evidence/issue477-candidate7/instrumented-full-workload.json),
the shipped SYSTEM.md template clarifies that answering an authorized personal
memory fact does not require an OA Skill, while remembered text grants no OA
business authority. The 100 complete Dano/MiMo requests passed 70/70 expected
source hits, 70/70 context injections, 70/70 semantically correct recall
answers, and 30/30 irrelevant requests with no injection or personal fact in
the answer. Literal matching was 64/70; six answers used `星期` where the frozen
fact used `周`. Recall wait p95/max were 452/694 ms, and injection was at most
213 tokens. This used a copied runtime prompt in a disposable container;
at that point, a full-source image repeat and matched memory-off cost
comparison were still necessary.
An image made by copying only this prompt template onto the previously
full-built `0.2.42` queue image then completed [matched on/off
workloads](evidence/issue477-candidate7/matched-workload-summary.json):
100/100 requests in each arm, 69/70 semantic recall answers with memory on,
30/30 irrelevant answers without personal facts, and estimated model cost
$0.01068 on versus $0.01932 off (observed increment −44.7%). One relevant
request still received an OA-capability refusal. Because this candidate used a
single-file image layer after a full-source rebuild hit Podman disk capacity,
the full-source image and Browser/provider gates were still open at that
point. The cost result also cannot isolate memory overhead from answer-length
or cache variation.
After scoped removal of this issue's stopped test containers and obsolete
images, the complete Dockerfile `protected-runtime` build succeeded as image
`c490cc97f45e`. Its built server tree and SYSTEM.md template hashes match
the prompt-layer image byte for byte. On the full-source image, a further
[five-user 100-request run](evidence/issue477-candidate7/full-source-summary.json)
completed 100/100 Dano/MiMo requests, answered 70/70 recall cases correctly
under the weekday-equivalence rubric, and kept owner facts out of 30/30
irrelevant answers. Literal recall matching was 64/70; no OA refusal occurred
in this run. The matched memory-off arm was not repeated on the new image tag;
its runtime code and prompt are byte-identical to the already measured arm.
The tokenizer, host-config and SYSTEM prompt focused tests passed 23/23;
`pnpm run check` and the full-source image build passed. On 2026-09-24,
the full Vitest suite was rerun without a concurrent image build, with
`/private/tmp/dano465-openviking-venv/bin` first on `PATH` so the Python
provider tests could import `httpx` 0.28.1: **145 test files,
1674 passed, 1 skipped** in 49.82 s. The ordinary shell's `python3` lacked
`httpx` and produced six environment-only failures; that run is not counted.
An earlier concurrent full run was also stopped after startup/timeouts and is
not counted. This proves the repository test suite, not the outstanding real
Browser, full evaluation matrix, save-ready, or rollback release gates.

After the isolated evaluation, the shared OpenViking account contained seven
USERs. Five matched the exact synthetic `eval477_` OA subject → Dano OAuth ID
→ memory-owner ID derivation. Those five were removed through the official
single-USER admin endpoint; a fresh listing contained the two original USERs
and no synthetic USER. The temporary copied credential directory was removed.
The original fixed HTTPS entry remained healthy. This is test-resource cleanup,
not a multi-user rollback or deletion-non-resurrection acceptance result.

### Existing-config upgrade regression (2026-09-24)

Switching the fixed isolated stack from `0.2.41` to the first full-source
`0.2.42` image exposed a real startup regression: its existing private
`memory-service.json` had no `tokenizerLimits.maxQueuedRequests`, and the new
parser rejected it before the HTTP host started. The container restarted
repeatedly; the original `0.2.41` image was restored and passed HTTPS health.
The version-1 parser now supplies a bounded queue of eight only when that
new field is absent. Explicit zero and other invalid values still fail.
The release manifest was also updated to match root version `0.2.42`.

The repaired complete Dockerfile image `b7a9f62c395f` was built from the
current source. Its fixed public open-webSearch tag was cloned on the host,
verified as commit `3094fa558fce35a8373e45ed5a6c43362e206906`, and
mounted read-only for the last build step after the VM's GitHub proxy returned
502; the Dockerfile still installed the same tag. The original private config
and named volumes were left in place. The repaired image started with no
restart, became healthy at `https://localhost:18711/`, and the Codex in-app
Browser restored the authenticated session, prior chat, and memory management
view. In a new chat, MiMo answered `7+5` as `12`, executed `bash ls` and
reported `uploads`, then read the fixed synthetic `shapes.png` upload and
identified its red circle, blue square, and yellow triangle in order.
The repaired source passed `pnpm run check`, the release-manifest check,
13 targeted tokenizer/config tests and a non-concurrent full Vitest run:
145 files, 1675 passed, 1 skipped. Screenshots and the sanitized summary are
in [the upgrade evidence](evidence/issue477-upgrade/). This
proves the existing-config upgrade and three browser regressions, not the
remaining dual-user, business OA, full quality matrix, or rollback gates.

### Live #465 PRD/Spec audit (2026-09-24)

The first case of the independently frozen
[save-ready workload](fixtures/issue477-save-ready.json) failed on the
`0.2.42`/`0.1.13` image. In the real Browser, the user supplied synthetic
`云汀201`, but MiMo's `memory_save` argument was `云汀2020`. The operation then
became `ready` after 173 seconds (UI timestamp precision), above the 60-second
limit, and the exported content held the wrong fact. The
[sanitized failure receipt](evidence/issue477-save-ready/first-attempt.json)
records both observations and the exact-document cleanup. A retryable VLM
connection error appeared during extraction; its contribution to latency is
not yet isolated. The other nine fixed cases were not run against this failed
candidate, so no p95 is claimed. The independent package fix was published as
`@josephyoung/pi-openviking@0.1.14` and locked in Dano `0.2.43`. Its
`MEMORY_SOURCE_MISMATCH` guard checks the proposed content against the current
user message before creating an operation. The rebuilt protected image
`1f1eea87390f` passed package/version inspection, release-manifest check,
`pnpm run check`, full Vitest (1675 passed, one skipped), and `pnpm run build`.
In the [same frozen S-01 Browser retest](evidence/issue477-save-ready/verbatim-guard-retest.json),
MiMo again proposed content that did not match the source; the guard blocked it
and the save-record list gained no new operation. This establishes the
fail-closed correction, but S-01 still fails successful-save acceptance and no
healthy save-ready p95 is established. The remaining fixed cases and release
gates remain open.

The frozen 20 cross-USER isolation cases ran three times each against the
real OpenViking public API in the protected local stack. The
[executable matrix](fixtures/issue477-isolation-matrix.py) and
[sanitized per-attempt receipt](evidence/issue477-isolation-matrix.json)
record 60/60 passes: search, direct read, direct write and export returned
403 in all 48 attempts. In the 12 Session-ID replay attempts, OpenViking
returned 200 but created an actor-owned Session at a distinct URI; the
target Session's message count remained one. Target facts did not appear in
responses or change on readback. Each round created five synthetic USERs;
all 15 were removed, and the original USER set was restored after each round.
This proves the upstream USER-key boundary for these frozen cases. It does
not establish Dano routing, project scope, or two independent OA Browser
sessions, which remain separate acceptance gates.

Compared with the live [Issue #465 PRD](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/465)
and [Spec](https://github.com/zhengchengqiaobusiness-arch/Dano/issues/465#issuecomment-5674833976).
"Partial" identifies evidence already collected; it is **not** acceptance.
All thirteen ACs and fourteen T cases remain open until their complete
requirement and required real-service/browser method pass. In particular,
the 60/60 and 30/30 figures above measure selection, not model answers.

| PRD | Current evidence | Missing acceptance |
|---|---|---|
| AC-01/02 | Published independent package `0.1.14`, fixed lockfile, two Pi keywords, rebuilt image, dual-entry loader check and one real-service protected Pi CLI save/new-session recall | Complete pair audit and Dano integration repetition |
| AC-03 | Browser explicit save, ready status/source and new-chat recall | Repeat fixed cases with model-answer review |
| AC-04 | Collection filters and consent have automated coverage; one Browser collection reached ready and was recalled after separate consent; two synthetic-key chats produced no visible save | Sensitive-data/inference exclusions with observed selector decisions, revocation race and fixed repetitions |
| AC-05/06 | Frozen 20 cross-USER cases ×3 passed against real OpenViking with forged headers, including isolated Session-ID replay; protected file boundary | Two independent authenticated Browser users, Dano routing and project scope |
| AC-07 | Browser merged correction with model answer on `0.2.41`, package isolated real-service correction, one targeted collected-fact forget and one post-snapshot deletion replay | Ten correction and ten deletion cases ×3, old queue/cache/inflight/backup non-resurrection |
| AC-08 | Browser defaults-off, separate collection consent/revocation, management and one same-owner cross-tab pause/recall/resume flow | All governance transitions, independent two-Session pause, export and blocked-write recovery ×3 |
| AC-09/10 | Old ambiguous queue recovered on real service; ordinary chat survived selected memory failures | Full lifecycle/fault matrix, truthful explicit failure and no duplicate/cross-owner replay |
| AC-11 | Final-image Browser form, generic Skill, image, bash and Pi compression; an isolated production-generated leave Skill was discovered and rendered its operation choice and six-field form | Business options/authentication failed; complete OA and final-image regression matrix |
| AC-12 | Clean stack, old-snapshot replay, two-owner supplied-ledger replay, candidate upgrade and matched rollback; automatic-journal matched old-volume one-owner replay plus current-service two-owner deletion/retry | Real multi-owner source/revocation and old-version rollback, arbitrary upgrade-window reconciliation |
| AC-13 | Frozen 80-case dataset; 20 isolation cases ×3 passed against real OpenViking; candidate-7 traced workload had 70/70 semantic recall answers and 30/30 irrelevant omissions; full-source image repeated 100 complete requests with 70/70 recall answers; byte-identical prompt-layer image has matched on/off cost evidence; first frozen save-ready case **failed** at 173 seconds and wrong fact; rebuilt image blocks the same mismatch before save | Complete successful save-ready cases, other five categories ×3, Dano/Browser isolation and causal latency interpretation |

| Spec test | Current evidence | Missing acceptance |
|---|---|---|
| T-01 | Published `0.1.14`, exact Dano lockfile, rebuilt protected image, dual-entry loading twice per entry, and one real-service ordinary Pi CLI save/new-session recall | Full pair audit and repeated functional checks |
| T-02/03 | Real USER-key isolation matrix 20×3, including header forgery and Session-ID collision | Dano/Peer/project scope and independent Bob Browser across fixed repetitions |
| T-04/05 | Automated lifecycle/collection tests; one real Browser automatic collection/recall/revocation path | Full multi-viewer/rebind/branch/dispose and collection exclusions |
| T-06/07 | Actual old `session_unknown` recovery and credential-store replay | All crash windows, rotation, user switch and anonymous transfer on fixed service |
| T-08 | Real Browser new-chat recall; bounded reranker selection | Short-session extraction, no-result/timeout and model-answer repetitions |
| T-09/10 | Real-service correction/deletion/replay; Browser targeted forget with unrelated recall preserved, defaults-off and cross-tab pause | Complete correction/forget/pause/restore state matrix ×3 |
| T-11 | Protected file access denied; real USER-key 403 probes | Full unauthenticated/401/403/native-tool/symlink/env/HTTP matrix |
| T-12 | Final-image Browser form, generic Skill, image, bash and Pi compression; generated leave Skill choice/form rendered in a disposable layer | Working business options/authentication and complete final-image repetition |
| T-13 | Clean deploy, candidate upgrade, matched old-data rollback, two-owner supplied-ledger replay; automatic-journal old-volume one-owner replay and current-service two-owner deletion/retry | General old queue, credential/new writer and old-version multi-user reconciliation |
| T-14 | Five-user, 100-complete-request MiMo candidate-7 run passed semantic recall, injection, wait and token limits in an instrumented disposable container; full-source image repeated 100 complete requests with 70/70 recall answers; byte-identical prompt-layer image passed matched on/off cost; frozen S-01 still fails successful-save acceptance after source guard | Complete healthy save-ready p95 and full fixed matrix |

The fixed §11.1 minima are 20 recall, 10 correction, 20 isolation, 10 deletion,
10 irrelevant and 10 authorization cases, each independently repeated three
times. Correction, isolation, deletion and authorization require every attempt
to pass. Recall needs at least 90% required-source hits **and** at least 90%
correct model answers; irrelevant requests need at least 90% without injected
memory. Five distinct concurrent users must run at least 100 **complete Dano
requests**, with steady recall-added p95 ≤1 s, wait hard limit 2 s, measured
injection ≤1,500 tokens, healthy save-ready p95 ≤60 s and incremental model
cost ≤20% against the same memory-off workload. The earlier 100-attempt probe
called OpenViking/reranker only; candidate 7 now supplies complete matched
requests and a full-source image repeat. The healthy save-ready comparison
and full six-category matrix are still open.

The §11.2 Browser flow also requires a separately authenticated Bob context;
a second tab sharing Alice's cookie cannot supply it. The current local stack
has only Alice's authenticated Browser context. No production go/no-go decision
or issue closure follows from this partial evidence.

- Package and validate the recovery procedure as a repeatable command,
  including deletion/revocation records across arbitrary rollback points.
- Define and test the multi-user upgrade-window reconciliation policy beyond
  one controlled synthetic resubmission.
- Retain the failed vector-only and candidate-2 concurrency results alongside
  the passing candidate-3/4/5 selection evidence; finish full model-answer review.
- Complete correction, deletion and authorization cases three times each,
  remaining model-answer review, the healthy save-ready workload and the
  independent dual-user in-app Browser scenario.
- Complete the business OA Skill Browser flow, remaining final-image Field
  Assist/Heimdall/SSE regressions, close each audited AC/T gap and clean test
  resources.

No production deployment or release conclusion is implied by this record.
