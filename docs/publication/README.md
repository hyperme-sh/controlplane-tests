# Tests publication preparation

Tests remains Apache-2.0, version 0.2.0. Original and rebuilt wheels verify
owner LICENSE and NOTICE bytes. Fixture behavior remains unchanged.

CI prepares `ghcr.io/hyperme-sh/controlplane-tests/runtime` as a non-root
consumer image with the qualified fixture library and locked dependencies.
There is no service endpoint; consumers supply their own command. The retained
owner integration tree is not installed in the image and issue #3 remains separate.

Installed dependency legal texts are indexed and verified. Missing texts fail
preparation. All image layers are scanned for secrets, hosted-only code and
source-bundle paths; installed owner bytes and legal texts are checked offline.
No third-party source archives are fetched or bundled. GHCR activation remains
separate and preparation uses no registry credentials.

Reachable history review covers fetched origin branches, tags and HEAD,
including bounded archived wheel members, without executing old code. Findings
remain a review queue, not publication clearance. Deleted/unreachable refs,
remote LFS data and external archives are outside this scan.

TEST will use a moving channel. Integration/e2e remains nightly. No Actions
uploads, visibility changes or history rewrites are introduced.
