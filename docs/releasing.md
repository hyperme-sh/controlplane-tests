# Releasing spanopticon-tests

The package version in this checkout is `0.2.0` (`pyproject.toml`). Admission
workflows pass release line `0.2`. Compatibility rules for a candidate are in
[release-policy.md](release-policy.md). The manifest format is the
[Spanopticon release policy](https://github.com/hyperme-sh/spanopticon/blob/main/docs/releases.md).

## Main validation

`.github/workflows/package.yml` runs on pushes to `main` and on manual
dispatch. It uploads the exact wheel and sdist that `scripts/check-package.sh`
validated as the `tested-dist` artifact (90-day retention, uncompressed) and
the JUnit files as `test-results`. The workflow permission is `contents: read`.
It does not create a GitHub release or a tag.

`scripts/check-package.sh` exports packaged runtime bytes from the wheel,
binds that build's version and digest, and compares the contract of a wheel
rebuilt from the sdist (`cmp` on the two `contract.json` files). Byte equality
is the contract check. Installer metadata and downstream consumer behavior are
separate.

## Qualification snapshots

`test-<commit>` prereleases are development snapshots. The version embedded in
a snapshot wheel does not reserve a stable version number. Records of tested
bytes and contracts live under `releases/qualification`.
`releases/qualification/current/release.json` is the current qualification
manifest: line `0.2`, component version `0.2.0`. Earlier snapshot receipts stay
under `releases/qualification/snapshots/` with an explicit non-stable
classification.

## Stable publication

A stable publish is a push of a `vMAJOR.MINOR.PATCH` tag.
`.github/workflows/release.yml` calls the pinned Spanopticon admission workflow
(`hyperme-sh/spanopticon/.github/workflows/component-release.yml`) with release
line `0.2`, the repository variable `SPANOPTICON_RELEASE_BASELINE_SHA`, and the
tag name. After admission succeeds, the publish job (the job with
`contents: write`) publishes the exact `tested-dist` artifact from
`.github/workflows/package.yml` through the pinned
`hyperme-sh/.github` release-channel action.

`releases/accepted/release.json` is the stable baseline record. It binds the
immutable GitHub release
[v0.24.0](https://github.com/hyperme-sh/controlplane-tests/releases/tag/v0.24.0)
(release id 434678600, source revision
`943df6f85ac9650dc2944ab5ec9e6ab793f3003a`) to the wheel and sdist digests in
`releases/accepted/provenance.json`. Qualification snapshots remain under
`releases/qualification`. Admission reads the live release, the stable tag's
source revision, and those asset digests. A green caller job is a separate
fact from that baseline. A missing baseline fails admission.
