# #465 acceptance report archive

Archived on 2026-09-28 before report removal. Source HEAD:
`87db242c12aa19ce024956ff155451ff5766212f`; upstream base: `ddf4cd372e896a25b0cedce4d81ab9c5a9c20ae9`.
This storage change does not alter frozen cases, thresholds, results or release gates.

## Storage and integrity

Private archive directory: `/Users/joseph/tmp/dano465-acceptance-archive/20260928T060037Z-87db242c`.
The archive contains all 74 original PR-added JSON reports, including every failed
attempt recorded in those files. Copies retain their original repository paths
under `reports/`. `manifest.json` lists every file's SHA-256, byte count, line count
and whether it was removed from the final PR diff. Directory mode is 0700; files
are 0600. No credentials or private deployment configuration were copied.

- `reports.tar.gz` SHA-256: `03d9d5d59ff62b5ba1e5cfc4c2340524f3dce9ebb57e229c5aadeea40fd112de`.
- `manifest.json` SHA-256: `874956ec7d0e19f43260f9686e379637df4993fd013695965ad4a94b66424381`.
- Verified: 74 byte-identical file copies and 74 matching tar members before removal.
- Removed from Git: 38 raw/intermediate reports, 12,344 lines and 462,649 bytes.
- Retained in Git: frozen inputs, reproduction scripts, Browser screenshots and
  necessary concise summaries. The Pi 0.1.18 [summary](issue477-pi-0118-summary-20260928.json)
  replaces its report containing per-call model responses.

The archive is a separate local delivery artifact. A repository clone alone does
not include it. Transfer the bundle and `SHA256SUMS` together for raw-record review;
verify the bundle hash and manifest before reading individual records. Historical
source HEAD also remains in Git; this cleanup uses an ordinary commit.

```sh
cd '/Users/joseph/tmp/dano465-acceptance-archive/20260928T060037Z-87db242c'
shasum -a 256 -c SHA256SUMS
```

## Removed reports

Each row identifies the exact archived bytes. Historical conclusions and failures
remain in the [acceptance ledger](../issue477-memory-release.md).

| Report | Original repository path | Bytes | SHA-256 |
|---|---|---:|---|
| <a id="report-01"></a>01 | `docs/research/evidence/issue477-0254-protected-build.json` | 644 | `8191a0c7a4b44b11b293392cff3439842b99881cd153e2acb766d474423bb67c` |
| <a id="report-02"></a>02 | `docs/research/evidence/issue477-0255-compose-startup.json` | 1309 | `6f477684658818351c2f835820a314abead63b9430e908a4814dd84f9e43b262` |
| <a id="report-03"></a>03 | `docs/research/evidence/issue477-0255-default-apt-bootstrap.json` | 1204 | `d6d5d98424f41c3dcbfbaadd8702fe116189deb09c34b401e4dfe44bb3940209` |
| <a id="report-04"></a>04 | `docs/research/evidence/issue477-0255-protected-build.json` | 1105 | `30bebe95626d318d34013fda43a12665b14e88bea2e08caf4f4320decf388753` |
| <a id="report-05"></a>05 | `docs/research/evidence/issue477-0256-protected-key-replacement-build.json` | 1117 | `c079c0d84842b87d55b0b2022c5c814efff93cd6e0e35ef69d606f284c2b14bb` |
| <a id="report-06"></a>06 | `docs/research/evidence/issue477-0258-protected-build.json` | 1076 | `28de98fdea74ae6f6cf1a05fb6f78e85cc9f9e777fe64503ee7a72d1b575baac` |
| <a id="report-07"></a>07 | `docs/research/evidence/issue477-0259-protected-build.json` | 1210 | `4ab14a1a602444f133c5e4087940ea2ee2cc5dbea8786ccd9fad7f89320c6637` |
| <a id="report-08"></a>08 | `docs/research/evidence/issue477-0260-protected-build.json` | 1194 | `3214ec5ff1fcb6fe2872fe3e9d9fcab5d81dfff494d71d606c4e991ea4401dec` |
| <a id="report-09"></a>09 | `docs/research/evidence/issue477-0261-protected-build.json` | 2843 | `40fcc4a7dfb96e1e938f127a563f832ea7a1e53a5c3b544b833af31e144b8e0e` |
| <a id="report-10"></a>10 | `docs/research/evidence/issue477-baseline/irrelevant.json` | 4812 | `628291eb37b9bb6bcdcb3ebcaa1686474d6d3592ff6c4ccc31b37060ef347a39` |
| <a id="report-11"></a>11 | `docs/research/evidence/issue477-baseline/isolation.json` | 10538 | `0be9790d27016999ac5fd4cb0308d2c2d5cdaf96d45df986d89071d27da2d988` |
| <a id="report-12"></a>12 | `docs/research/evidence/issue477-baseline/recall.json` | 10678 | `cf292ce837d2e4f88362c0b9aa4224b6dee181898da1ed86b45a64474f7b6403` |
| <a id="report-13"></a>13 | `docs/research/evidence/issue477-candidate2/five-user-selection.json` | 22070 | `40a4804f1ade029e027f548163ea03f486f9b04e72b0814c11b04b37e19ae74c` |
| <a id="report-14"></a>14 | `docs/research/evidence/issue477-candidate2/formal-retrieval.json` | 24926 | `c5073a1e6fa8faa8e6a52b6e92ca7fca5e5c5734e8c7ca9bdbbf03f522b33a07` |
| <a id="report-15"></a>15 | `docs/research/evidence/issue477-candidate3/five-user-selection.json` | 21958 | `d7cace0d7fc8869234fbb3fa11d1d1bfc48af068523e140dfe57f5e79687203f` |
| <a id="report-16"></a>16 | `docs/research/evidence/issue477-candidate3/formal-retrieval.json` | 24867 | `d7807e5419160d8448688992cd61bbe68051cd4052befcb917309ebab8695376` |
| <a id="report-17"></a>17 | `docs/research/evidence/issue477-candidate4/five-user-selection.json` | 21951 | `5d1d17802f3b1c852fe7a6f44e5db86707c38b14033ce0ee0fe095addf6a8a39` |
| <a id="report-18"></a>18 | `docs/research/evidence/issue477-candidate4/formal-retrieval.json` | 24901 | `c2f6b966af437e4c155b5cf27485f78a289399c51d50cdc847f8f0fe78b6c170` |
| <a id="report-19"></a>19 | `docs/research/evidence/issue477-candidate5/five-user-selection.json` | 21944 | `2fc5a7f500608d25effebb6e8c3a966975a43138214cddbef08e1f14c1dc8c3f` |
| <a id="report-20"></a>20 | `docs/research/evidence/issue477-candidate5/formal-retrieval.json` | 24820 | `a38a920f0d3c46374a35bc482ecb097f4cf1657e6b30eb7c16a405ce6e314a20` |
| <a id="report-21"></a>21 | `docs/research/evidence/issue477-candidate6/full-workload-off.json` | 33567 | `63726f5e8a1b4fff54e3df728872aca0c8ce930243179d6cdbe2b43717b104a2` |
| <a id="report-22"></a>22 | `docs/research/evidence/issue477-candidate6/full-workload-on.json` | 28908 | `bc19dc4fbf2587900a6a39038b0f8e3b25571e2890f3a753fcf926421232bfb4` |
| <a id="report-23"></a>23 | `docs/research/evidence/issue477-candidate6/traced-workload.json` | 1331 | `d74138c7a38bd5fab1b0a3f819521a707138024aa4d3143c1ff8a7594b6cfe17` |
| <a id="report-24"></a>24 | `docs/research/evidence/issue477-candidate7/full-source-image-on.json` | 32219 | `e358d333d2b330330352a466eb7cd2ab22716d62111f25a9bf08f27d01409a0c` |
| <a id="report-25"></a>25 | `docs/research/evidence/issue477-candidate7/full-workload-records.json` | 31498 | `1924c0a22410dc33236be9411c078c82c1b6d6718a32850649ad02cdc1c8a0fe` |
| <a id="report-26"></a>26 | `docs/research/evidence/issue477-candidate7/prompt-layer-image-off.json` | 37614 | `dbeeae6d5e614e0cdc0be25d649e209ec51af5938b7e2d044be346e62e53725d` |
| <a id="report-27"></a>27 | `docs/research/evidence/issue477-candidate7/prompt-layer-image-on.json` | 31909 | `038805e31dd1aed1f07801b594cab33d00bbc47c25bb01505ae32aa596a5d852` |
| <a id="report-28"></a>28 | `docs/research/evidence/issue477-isolation-matrix.json` | 12381 | `06b7d7defadfa7a8c1e75b76a568eb24d928ef24d9929822e1e14331064471ab` |
| <a id="report-29"></a>29 | `docs/research/evidence/issue477-local-tls-oauth-stage-20260928.json` | 1776 | `97a7e13be85122f3a0b6be316d0910c7213c5eb1258d45c187f44654f5d86e93` |
| <a id="report-30"></a>30 | `docs/research/evidence/issue477-oa-client-registration-missing-20260928.json` | 1635 | `586182922b1b7fd6798a9123052168c67bd53d0d9a23c459fa7b12ff5040f261` |
| <a id="report-31"></a>31 | `docs/research/evidence/issue477-oa-registration-ready-20260928.json` | 1480 | `98712efe69aab435b3f74a787605a230edf993cc2cfff50344719074a0b97271` |
| <a id="report-32"></a>32 | `docs/research/evidence/issue477-pi-0118-overlap-fix-20260928.json` | 8570 | `d7b319ca2cea6970df2c4c50f49f5957161250fcc7ab7d8e3cc69ec336a320ee` |
| <a id="report-33"></a>33 | `docs/research/evidence/issue477-public-api-correction-delete.json` | 9804 | `99cda3adf7475155aade763d7f367d0aae4626bdde62c1e55652d829dc2edad3` |
| <a id="report-34"></a>34 | `docs/research/evidence/issue477-save-ready/first-attempt.json` | 1213 | `09c44a712e18bce91a676583116b6709c3bb36d71cd428dfcd74165ef4c0fb2b` |
| <a id="report-35"></a>35 | `docs/research/evidence/issue477-save-ready/numeric-source-retest.json` | 868 | `8fd92ddb075a7170cda294ce5f8f73b9b405988ba07b46c56c10ed09979d4d16` |
| <a id="report-36"></a>36 | `docs/research/evidence/issue477-save-ready/retry-guidance-retest.json` | 900 | `444d8e0a60ab55756853107fb224d95bcad0c88aaa677ab7bb8218e1d24b3107` |
| <a id="report-37"></a>37 | `docs/research/evidence/issue477-save-ready/source-aligned-first-ready.json` | 890 | `419fcd2f37e8fd68990a1151724790129ab32e9fb8e5a98c7a3b6dff87ab7214` |
| <a id="report-38"></a>38 | `docs/research/evidence/issue477-save-ready/verbatim-guard-retest.json` | 919 | `b4833d53abdd7c1d68803d3d5e63e2ab7ad1a17054e459a1f8d3140b3cd9a274` |

## Future runs

Write full per-attempt JSON/JSONL, model responses and intermediate run outputs
outside the checkout under `~/tmp/dano465-acceptance-archive/`. Keep successes and
failures together. Git contains frozen inputs, executable reproduction and brief
final summaries with the raw archive location and SHA-256; it does not contain
full request/response records. The ignored `raw/` convention and archived report
paths protect against accidentally adding those outputs again.

Reproduction scripts emit results to stdout and accept external evidence paths.
For example, the historical coverage audit still reports incomplete coverage:

```sh
node scripts/audit-memory-evaluation-coverage.mjs \
  ~/tmp/dano465-acceptance-archive/20260928T060037Z-87db242c/reports/docs/research/evidence/issue477-isolation-matrix.json \
  ~/tmp/dano465-acceptance-archive/20260928T060037Z-87db242c/reports/docs/research/evidence/issue477-public-api-correction-delete.json
```

An audit exit code of 1 remains the recorded incomplete gate; archival does not
change it to a passing result.

## Real recovery and three rotations, 0.2.65

Private directory: `/Users/joseph/tmp/dano465-acceptance-archive/20260928T085749Z-real-recovery-0265`.
The bundle has 134 verified evidence files plus its manifest, including
both recovery defects, all failed attempts, real task usage, full private
runtime/remote snapshots, three rotation runs, focused/full check attempts and
scoped cleanup. It contains private runtime keys and stays outside Git with
0700 directories and 0600 files.

- `evidence.tar.gz` SHA-256: `e9e853240eab20e1e6eeb2575e84bb5d47fdfdf3fe7a3282b2c430bf163d5262`.
- `manifest.json` SHA-256: `d2dbd60d2120c51897b70b83b5f32e0fce072236d454260f305d0e28d8bc4635`.
- Every bundled file was read back and hash-verified. The previous and current
  frozen fixture inputs are retained byte-for-byte. Only candidate version
  metadata changes from `0.2.64` to `0.2.65`; thresholds and cases do not change.
- [Brief result and limits](issue477-0265-real-recovery-20260928.json).

Transfer this private bundle through an appropriate private channel; it must not
be uploaded into the public PR. The local archive alone is not portable CI proof.

## Frozen matrix supplement, 0.2.65

Private directory: `/Users/joseph/tmp/dano465-acceptance-archive/20260928T105551Z-matrix-supplement-0265`.

Contains 52 hash-verified files, including original rejected requests,
semantic review inputs, all correction/deletion/authorization attempts, native
seed task usage and reproduction drivers. Files are 0600; directories are 0700.

- `manifest.json` SHA-256: `35cda75a75640582d22291e4b76e8b3972f0d494d61a7682c1a8286f1e2da23d`.
- [Brief result and remaining gates](issue477-0265-matrix-supplement-20260928.json).
- The runtime volume `dano465formal0263_runtime` remains intact for unfinished
  cases. This archive contains evidence, not a substitute for a volume backup.
