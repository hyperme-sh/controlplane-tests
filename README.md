# Control plane test fixtures

The `panopticon-tests` distribution provides PostgreSQL migration templates,
database URL conversion and schema comparison helpers in `panopticon_tests`.
Use Python 3.13 and uv 0.11.26:

    uv sync --frozen
    bash scripts/check-package.sh

Package checks run from source, an installed wheel outside the checkout and a
rebuilt source distribution. They use temporary config files and mocked Alembic
calls; they do not run PostgreSQL, Redis, Docker or deployed-service tests.

Migration callers must set PANOPTICON_TEST_ALEMBIC_INI to an existing absolute
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
remains disabled pending hosted-worker isolation qualification. Full owner-suite
CI, PostgreSQL/RLS/migration and multi-service acceptance remain separate gates.
