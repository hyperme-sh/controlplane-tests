# Control plane test fixtures

## Current version authority

Current development version: `0.2.0`. The services remain before the unreleased
0.24.0 milestone. Earlier 0.24/1.0 preparation and publication notes below are
historical and do not authorize those versions. Existing tags and historical
artifacts remain untouched. Development uses exact source pins and rebuilt
artifacts; no prior release is required. Version changes, tags and publication
require the owner's explicit authorization to the agent.

The `spanopticon-tests` distribution provides PostgreSQL migration templates,
database URL conversion and schema comparison helpers in `spanopticon_tests`.
Use Python 3.13 and uv 0.11.26:

    uv sync --frozen
    bash scripts/check-package.sh

Package checks run from source, an installed wheel outside the checkout and a
rebuilt source distribution. The offline cases use temporary configs and mocked Alembic calls. With
TEST_DATABASE_URL set to a disposable administrative PostgreSQL server, the consumer
case runs a real two-revision Alembic chain, clones templates, verifies data isolation
and detects schema/RLS changes. It does not qualify archived owner suites.

Migration callers must set SPANOPTICON_TEST_ALEMBIC_INI to an existing absolute
owner Alembic config path. The package does not infer a monorepo root from its
installation directory. Use absolute or %(here)s-relative script locations in
that config and provide its owner dependencies. Choose a disposable test database
explicitly; these helpers execute migrations and create/drop template databases.
DATABASE_URL is restored after successful or failed migration calls. The
process-global environment means callers must serialize migration calls within
a process; separate workers should use separate processes and database prefixes.

`integration/` retains the owner-dependent test suites as source material. They
still require reconciliation with service packages, migration chains, source
layout and fixture setup. They are excluded from default package collection;
their retention is not a claim that they run independently. No owner tests are
silently skipped and counted as passing by the package command.

The read-only, pinned main/manual CI workflow validates this package. The separate read-only hosted PR workflow and existing main runner lane both
include disposable PostgreSQL consumer qualification. Full owner-suite
CI, PostgreSQL/RLS/migration and multi-service acceptance remain separate gates.

Version 0.2 uses `spanopticon-tests`, imports `spanopticon_tests`, and requires
`SPANOPTICON_TEST_ALEMBIC_INI`. Update callers and their configuration together.
Retained owner integration suites remain unqualified source material; their
namespace updates do not establish service or database integration acceptance.

CI uses the approved shared-local Linux x64 runner group. Actions qualification
runs the existing main/manual checks; it does not publish or deploy services.

## Release channels

Main validation retains its exact qualified files in Actions artifacts. It does
not create GitHub releases or tags. Historical `test-<commit>` prereleases remain
qualification evidence and do not establish an accepted stable release.

Stable publication requires an explicit `vMAJOR.MINOR.PATCH` tag, the exact
successful main validation artifact, compatible declared contracts and an actual
immutable stable baseline. The publisher independently verifies that evidence;
successful caller jobs alone cannot establish acceptance. Missing evidence and
first-stable bootstrap remain closed. Package qualification, coordinated consumer
acceptance and runtime deployment remain separate.

## Artifact contracts

The root distribution is version 0.24.0. CI exports all packaged runtime/resource
bytes from the actual wheel without importing candidate code, binds the candidate
version and digest to that build, and compares rebuilt-sdist contracts. Conservative
byte equality rejects implementation-only patch changes as well. Installer-metadata
and semantic compatibility remain separate; archived owner/multilanguage artifacts
are not counted as independently qualified by the root package tests.

Development artifact records live under `releases/qualification`: they retain
tested bytes and contracts without reserving a stable version. A test prerelease
is not an accepted stable baseline. Stable promotion still requires independent
live stable-release provenance and an explicit candidate.

## Test qualification and stable acceptance

GitHub `test-<commit>` prereleases are development snapshots. Their embedded
package version does not reserve a stable version. Artifact and consumer evidence
lives under `releases/qualification`; the current wheel binding is still checked
by package CI, alongside any future explicit stable candidate. Earlier test
snapshot receipts are preserved there with an explicit non-stable classification.

`releases/accepted` and `SPANOPTICON_RELEASE_BASELINE_SHA` are reserved for an
actual immutable stable GitHub release. The pinned shared guard verifies the live
release channel, stable tag source and asset digests; a test snapshot cannot
become accepted through a provenance label. No stable baseline is recorded until
that release exists. Stable publication remains closed without a valid baseline;
first stable bootstrap and coordinated release qualification remain separate.
No stable version, approval prerequisite or deployment is introduced here.


### Published stable distribution

The accepted record binds the existing immutable `v0.24.0` release to its actual
GitHub release ID, source revision and published asset digests. Test snapshots
remain under `releases/qualification` and do not establish stable acceptance.
Runtime deployment and coordinated consumer acceptance remain separate.

### Actions qualification and stable publication

Main merges and workflow completion do not publish GitHub test releases. Exact
qualification files remain in Actions artifacts with finite retention. The pinned
publisher independently verifies a real immutable stable baseline and refuses
qualification snapshots, even if a caller job reports success. Existing valid
stable records are retained; no release, version change, deployment or human
approval prerequisite is introduced by this correction.

Historical combined-library workflow validation is retained in
`tests/test_legacy_library_sync.py` with its frozen workflow fixture. These eight
cases preserve pin-validation and credential-handling assertions while allowing
the obsolete Panopticon workflow to be retired. They do not qualify current
service CI, which uses per-job App repository allowlists and immutable locks.

## License

This package is licensed under [Apache-2.0](LICENSE). Dependencies retain their
own licenses; this declaration does not relicense bundled third-party material.
