# Release compatibility

Admission for this repository uses release line **0.2**. That value is
`release-policy.json` `"line"` and the `release-line` input of
`.github/workflows/release-admission.yml` and `.github/workflows/release-admission.yml`.
Packages that share a release line share major and minor versions. Patch
numbers advance independently and keep the accepted API contract for that
version. The package version in `pyproject.toml` is `0.2.0`. Installing these
workflows does not publish that version.

The immutable GitHub release
[`v0.24.0`](https://github.com/hyperme-sh/controlplane-tests/releases/tag/v0.24.0)
remains published. Its binding is `releases/accepted`. Channel rules for
qualification snapshots and that release are in [releasing.md](releasing.md).

`release-policy.json` names this repository (`hyperme-sh/controlplane-tests`)
and the package version file (`pyproject.toml`). The pinned Spanopticon
workflow checks that configuration on pull requests. A version change or a
`releases/candidate/release.json` submission also runs release admission. Every
tag push and manual release request runs admission. A configuration-only result
is a configuration result; release compatibility is the admission result for
the requested tag.

Before attempting a release:

1. Produce an accepted bundle at `releases/accepted/release.json`, with its
   contract files and immutable artifact references. Set the repository variable
   `SPANOPTICON_RELEASE_BASELINE_SHA` to that bundle's full 40-character commit
   SHA. The baseline needs artifact provenance from a real release.
2. Generate the candidate bundle at `releases/candidate/release.json` from the
   release artifacts. It must contain exactly this component, use line `0.2`,
   and match the declared package version and the requested tag.
3. Run admission against the baseline. Patch contracts stay unchanged.
   Replacing a published artifact at the same version fails admission. Verify
   artifact provenance and downstream consumer behavior separately.
4. Successful admission and artifact validation are prerequisites of the
   publisher for that exact release. The publisher selects the accepted
   baseline independently of candidate data and retains the validation evidence.

A missing baseline fails closed. These workflows do not deploy the control
plane, enable repository Actions, grant private dependency access, or prove
that a declared artifact exists. See the
[Spanopticon release policy](https://github.com/hyperme-sh/spanopticon/blob/main/docs/releases.md)
for the manifest format and compatibility rules.

The package publisher calls the same pinned admission workflow as a
prerequisite job for stable tag events, passing the baseline and the requested
tag. A failed or skipped admission prevents the contents-write publication job
from running. The reusable workflow checks the triggering commit and requested
version. Tag admission is the result that gates publication. Main qualification
snapshots stay on the channel described in [releasing.md](releasing.md).
