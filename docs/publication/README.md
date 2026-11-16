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
No third-party source archives are fetched or bundled. Main publishes only the qualified image to the moving GHCR `:test` channel.
Write credentials are confined to publication after qualification. Stale main
builds skip publication; version and production channels remain disabled.

Reachable history review covers fetched origin branches, tags and HEAD,
including bounded archived wheel members, without executing old code. Findings
remain a review queue, not publication clearance. Deleted/unreachable refs,
remote LFS data and external archives are outside this scan.

TEST will use a moving channel. Integration/e2e remains nightly. No Actions
uploads, visibility changes or history rewrites are introduced.

## Retained-image review

CI inventories tagged and untagged GHCR versions and inspects Linux amd64 layers
without executing old images. It records hosted content, legal paths, inherited
configuration names and source-bundle presence, then scans secrets. Deleted
versions, other platforms and other registries are outside the review scope.
Unavailable inventory is reported explicitly, never as an empty registry.
Post-publication inventory and inspection must succeed. The registry image is
pulled back and compared with the qualified image identity.

The existing history scan has no recorded matches; that is not blanket historical
publication clearance. No historical bytes, registry settings or visibility are
changed. Owner integration issue #3 remains separate.
