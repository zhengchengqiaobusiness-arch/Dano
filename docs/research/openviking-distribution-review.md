# #473 distribution review

Reviewed 2026-09-18 against OpenViking 0.4.20 / source commit
`b54001e2e5c974ffd7a09ba543813fa104a99561`, SDK 0.1.0 and pi 0.82.1.

## Selected delivery boundary

The independent extension will contain our adapter code and the published
HTTP SDK dependency. It will not bundle the OpenViking Python server or copy
its internal implementation. pi remains a peer dependency. The server runs
as a separate, pinned, unmodified service. Reviewed upstream examples inform
the public API calls; no example implementation is copied into this branch.
Model weights used during research are not part of the extension package.

## License obligations and release actions

| Component | Verified declaration | Delivery action |
|---|---|---|
| OpenViking server 0.4.20 | Wheel contains AGPL-3.0 LICENSE; matches fixed upstream root license | Retain license/copyright notices. When delivering an image, provide the exact corresponding source and build/install scripts with accessible release artifacts, not merely a moving upstream branch. Maintain that source access for the delivered version. Any server modification requires reassessing the network-source requirement before release. |
| TypeScript SDK 0.1.0 | Published package and fixed-commit `sdk/typescript/package.json` declare Apache-2.0 | Include Apache-2.0 text and applicable attribution/NOTICE material with the delivered extension. Preserve notices and identify any modifications. The published SDK tarball omits a standalone LICENSE file, so do not assume dependency packaging supplies it. |
| pi coding-agent 0.82.1 | Installed package declares MIT | Declare tested peer compatibility, do not ship a second pi core. Retain MIT copyright/license notices if pi is redistributed in an installation image. |
| Our adapters and examples | Original project-authored code | Preserve the project's chosen license and keep dependency notices distinct. No AGPL server source is vendored into the extension. |

The server actions follow the source-distribution and modified-network-service
conditions in [the fixed upstream AGPL license](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/LICENSE).
The SDK actions follow redistribution section 4 of
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).
The SDK-specific declaration is in
[its fixed package manifest](https://github.com/volcengine/OpenViking/blob/b54001e2e5c974ffd7a09ba543813fa104a99561/sdk/typescript/package.json).

## Feasibility conclusion and release check

This selected distribution has a concrete source/notice delivery path and
does not require copying upstream server code into the extension. It is not
a blanket exemption based on using HTTP. Enterprise delivery must preserve
the same licensing obligations as public delivery. No proprietary fork or
exception from upstream licensing is assumed.

Before publishing #474 or shipping the #477 image, inspect the actual tarball
and image bill of materials, include dependency licenses/notices, attach the
matching server source/build materials, and check that every pinned dependency
and any bundled model has its required distribution artifacts. This is the
release implementation of this review, not a demand to publish nonexistent
artifacts before the feasibility gate. Changes to the selected boundary reopen
the review.
