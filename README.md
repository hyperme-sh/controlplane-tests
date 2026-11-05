# spanopticon-tests

`spanopticon-tests` is the Python package of PostgreSQL test helpers for the
Hypermesh control plane. Import it as `spanopticon_tests`. It builds migration
template databases, converts SQLAlchemy database URLs, and snapshots a schema
so two databases can be compared. Version 0.2.0 on this branch requires Python
3.13. Runtime dependencies are Alembic `>=1.16`, SQLAlchemy `>=2.0`, and
`psycopg[binary]>=3.2`.

## Why

Control-plane services keep tenant, user, billing, and metering state in
PostgreSQL, and their Alembic histories are long. Replaying that history from
an empty database for every test spends most of the suite on migration rather
than on the behavior under test. These helpers migrate each requested revision
once into a template database on a shared disposable server, then hand each
test its own `CREATE DATABASE ... TEMPLATE` copy. A separate snapshot captures
tables, defaults, constraints, indexes, row-level security, functions, and
grants so a test can see what a migration changed.

## How it works

`MigrationTemplates` keeps one administrative connection per process. It reads
the Alembic script directory named by `SPANOPTICON_TEST_ALEMBIC_INI`, requires
a single linear head, and rejects multiple heads, branch-and-merge graphs, and
`depends_on` edges before it creates any template database. A template at
revision R is cloned from the nearest earlier template and upgraded only across
the missing revisions. `database()` copies a template into a fresh database for
one test. `drop()` and `dispose()` tear those databases and the admin engine
down.

`run_alembic_upgrade`, `alembic_upgrade`, and `alembic_downgrade` point Alembic
at one sync URL. For the duration of the command they set the process
environment variable `DATABASE_URL` to the normalized URL, then restore the
previous value on success or failure. Callers serialize migration commands
inside one process. Separate workers use separate processes and distinct
database-name prefixes.

URL helpers leave credentials, host, port, database, and query intact.
`normalize_sync_sqlalchemy_url` rewrites `postgres`, `postgresql`,
`postgresql+psycopg2`, and `postgresql+asyncpg` to `postgresql+psycopg`.
`async_db_url` rewrites that sync URL to `postgresql+asyncpg`. `database_url`
returns the same server URL with a different database name. Other drivers are
left unchanged.

`schema_snapshot` reads `pg_catalog` over an open SQLAlchemy connection. It
omits `alembic_version*` tables, object IDs, and row data. Column identity,
sequence definitions, policies, triggers, and grants remain in the snapshot.

The Alembic config is always explicit. `SPANOPTICON_TEST_ALEMBIC_INI` must be
an existing absolute file. `script_location` in that file is an absolute path
or a `%(here)s` path. The package reads that variable and ignores its
installation directory. Migrations that `GRANT` to the roles `controlplane` or
`controlplane_app` only when those roles exist follow the roles present on the
server you provide. Use a fresh server that has neither role when you want the
grant behavior of a new container.

## Practical use

Install from a checkout with Python 3.13 and [uv](https://docs.astral.sh/uv/)
0.11.26, the versions the workflows pin:

```bash
uv python install 3.13
uv sync --frozen
```

Depend on a pinned revision the same way the services do. `user-service`,
`ai-service`, and `payment-service` currently pin:

```text
spanopticon-tests @ git+https://github.com/hyperme-sh/controlplane-tests.git@78328efbb3c5d788903adc20c7e34e6232053ee2
```

Point the helpers at a disposable administrative database. The connection must
be allowed to `CREATE DATABASE` and `DROP DATABASE`. Set
`SPANOPTICON_TEST_ALEMBIC_INI` to the absolute path of the service Alembic
config you want to apply (this repository has no service `alembic.ini` of its
own):

```bash
export TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres
export SPANOPTICON_TEST_ALEMBIC_INI=/absolute/path/to/alembic.ini
```

```python
import os

from sqlalchemy import create_engine

from spanopticon_tests.fixtures.postgres import MigrationTemplates
from spanopticon_tests.schema import schema_snapshot

templates = MigrationTemplates(os.environ["TEST_DATABASE_URL"], prefix="demo")
try:
    head = templates.template_at("head")
    name, url = templates.database(head, label="case")
    engine = create_engine(url)
    with engine.connect() as connection:
        snapshot = schema_snapshot(connection)
    engine.dispose()
    templates.drop(name)
finally:
    templates.dispose()
```

`template_at("head")` migrates an empty database to the single Alembic head.
`database()` returns `(name, sync_url)` for an isolated copy. Compare
`snapshot` with a later snapshot after the test alters tables or enables row
level security. `tests/test_postgres_consumer.py` is a complete two-revision
example of that pattern.

## How Hypermesh uses it

Lease, host, and renter requests are served by the control-plane services.
Those running services load their own packages. CI installs this pin from the
dev dependency group and, where the suite opens PostgreSQL, applies migrations
to a disposable server before HTTP tests run.

- **user-service.** `.github/workflows/service.yml` (`Standalone service`) runs
  on `main`. Its GitHub App token may read `controlplane-tests`, and
  `scripts/check-package.sh` syncs the dev pin before pytest.
  `core/user-service/tests/conftest.py` calls `normalize_sync_sqlalchemy_url`
  on a Testcontainers `postgres:16-alpine` URL, then
  `controlplane_db.migrations.upgrade` for the `platform` and `events` chains.
  The HTTP client then calls user, tenant, host-association, and
  account-closure routes on that database. The workflow also starts a
  PostgreSQL 17 service and exports `TEST_DATABASE_URL` on the same step.
- **ai-service.** `.github/workflows/package.yml` (`Standalone package`) runs
  on `main` with `controlplane-tests` on the same read allowlist.
  `tests/integration/conftest.py` sets `SPANOPTICON_TEST_ALEMBIC_INI` to the
  `alembic.ini` inside the installed `controlplane-db` distribution and calls
  `run_alembic_upgrade` on a Testcontainers PostgreSQL 16 URL before
  metered-inference HTTP. That fixture points the upstream model URL at
  `http://test-llm.invalid/v1`.
- **payment-service.** `.github/workflows/package.yml` allowlists
  `controlplane-tests` and `scripts/check-package.sh` installs the dev pin with
  `uv sync --frozen` before pytest. That run includes the lease-delivery,
  invoice, and payout modules, which talk to the payment app and provider
  doubles. `scripts/verify_dependency_fixtures.py` (the `--fixtures` mode of
  the same script) checks the `spanopticon-tests` git pin, lockfile entry, and
  vendored wheel together.

This repository checks the helpers themselves in
`.github/workflows/package.yml` (push to `main`, and manual dispatch) and
`.github/workflows/consumer-qualification.yml` (pull requests). Both start
PostgreSQL 17 (`postgres:17` digest-pinned in the workflow), export
`TEST_DATABASE_URL` as
`postgresql+psycopg://postgres:package-test-only@127.0.0.1:<port>/postgres`,
and run `bash scripts/check-package.sh` with uv 0.11.26 and Python 3.13. On a
public repository those jobs use `ubuntu-24.04`. The workflow file selects the
self-hosted `linux`, `x64`, and `debrunner` labels when the repository is
private.

## Develop and test

URL conversion and Alembic-config tests run offline:

```bash
uv sync --frozen
uv run --frozen pytest tests/test_normalize_sync_sqlalchemy_url.py tests/test_migration_config.py tests/test_migration_graph.py
```

The graph tests that open a real server skip unless `TEST_DATABASE_URL` is set.
The full package check requires that variable and a disposable administrative
server. It runs the suite from the checkout, from an installed wheel in a clean
virtualenv, and from a wheel rebuilt from the sdist, then compares the exported
wheel contracts:

```bash
export TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres
bash scripts/check-package.sh
```

Default pytest collection is `tests/` only (`pyproject.toml`). The trees under
[`integration/`](integration/README.md) are retained cross-service suites.
[`integration/runtime/`](integration/runtime/README.md) is what
`bash scripts/check-owner-integration.sh` runs, and it needs Docker plus read
access to the pinned service repositories.
`.github/workflows/owner-integration.yml` is that lane on `main`. It is
separate from the package check above.

`tests/test_legacy_library_sync.py` keeps eight cases against the frozen
workflow fixture in `tests/fixtures/legacy-controlplane-libs-sync.yml`. They
cover pin validation and credential handling for that fixture. Current service
workflows use a per-job repository allowlist and the lockfile.

## Release and versioning

This checkout's package version is **0.2.0** (`pyproject.toml`), on release
line `0.2`. Main builds store the validated wheel and sdist as Actions
artifacts. Stable publication is a `vMAJOR.MINOR.PATCH` tag that passes
admission and publishes that artifact. The immutable GitHub release
[v0.24.0](https://github.com/hyperme-sh/controlplane-tests/releases/tag/v0.24.0)
remains the accepted baseline recorded under `releases/accepted`. Channel
details are in [docs/releasing.md](docs/releasing.md). Compatibility rules are
in [docs/release-policy.md](docs/release-policy.md).

## License

This package is licensed under [Apache-2.0](LICENSE). Dependencies retain their
own licenses.
