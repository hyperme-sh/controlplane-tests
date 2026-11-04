# Control plane test fixtures

The `spanopticon-tests` distribution provides PostgreSQL migration templates,
database URL conversion and schema comparison helpers in `spanopticon_tests`.
Use Python 3.13 and uv 0.11.26:

    uv sync --frozen
    bash scripts/check-package.sh

Package checks run from source, an installed wheel outside the checkout and a
rebuilt source distribution. They use temporary config files and mocked Alembic
calls; they do not run PostgreSQL, Redis, Docker or deployed-service tests.

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

The read-only, pinned main/manual CI workflow validates this package. Actions
remains disabled pending shared-runner qualification. Full owner-suite
CI, PostgreSQL/RLS/migration and multi-service acceptance remain separate gates.

Version 0.2 uses `spanopticon-tests`, imports `spanopticon_tests`, and requires
`SPANOPTICON_TEST_ALEMBIC_INI`. Update callers and their configuration together.
Retained owner integration suites remain unqualified source material; their
namespace updates do not establish service or database integration acceptance.

CI uses the approved shared-local Linux x64 runner group. Actions qualification
runs the existing main/manual checks; it does not publish or deploy services.

## Release channels

Successful main-push validation retains the validated Python wheel and source distribution and publishes
an immutable `test-<commit>` GitHub prerelease. Push a `vMAJOR.MINOR.PATCH`
tag at that tested commit to promote the exact files to a stable release,
without rebuilding. Repository visibility applies to all package assets.
Package metadata versions are preserved; these are GitHub distribution snapshots,
not automatic PyPI publication or service deployment.

Promotion requires the original Actions artifact (90-day requested retention;
organization policy may shorten it). Expired evidence fails closed: validate a
new main revision. The publisher verifies the run, source, workflow and artifact
digest independently of release metadata. Repository immutable releases must be
enabled. See the [shared release contract](https://github.com/hyperme-sh/.github/blob/7749591fccd30d46ba9b0adabe82114001c9d0de/actions/release-channel/README.md).
