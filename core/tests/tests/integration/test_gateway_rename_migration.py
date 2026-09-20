"""0023 has to converge a router-era database on the same schema as a fresh one.

Test deploys hypermesh against a database created before the gateway rename, so
"the models say gateway" is not enough -- something in the chain has to move the
old names. When 0023 was missing, every hypermesh boot on the test VM died with
``column hypermesh_tunnels.assigned_gateway_id does not exist``, and no unit test
noticed because unit tests build their schema from the current models.

The comparison is against a fresh upgrade rather than a hand-written expected
list so that adding a hypermesh column later cannot make this test pass while
the two shapes drift.
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

from panopticon_tests.fixtures.postgres import (
    alembic_ini_path,
    normalize_sync_sqlalchemy_url,
)

pytestmark = pytest.mark.integration

_RENAME_REVISION = "0023_gateway_rename"

# What a pre-rename database looks like. Kept literal rather than imported from
# the migration so that loosening the migration cannot quietly loosen the test.
_LEGACY_TABLES = ("hypermesh_routers", "hypermesh_router_metrics")
_LEGACY_COLUMNS = (
    ("hypermesh_router_metrics", "router_id"),
    ("hypermesh_tickets", "assigned_router_id"),
    ("hypermesh_tunnels", "assigned_router_id"),
)


def _upgrade(url: str, revision: str = "head") -> None:
    os.environ["DATABASE_URL"] = url
    command.upgrade(Config(str(alembic_ini_path())), revision)


def _downgrade(url: str, revision: str) -> None:
    os.environ["DATABASE_URL"] = url
    command.downgrade(Config(str(alembic_ini_path())), revision)


def _shape(url: str) -> dict[str, object]:
    """Every hypermesh name a later migration or query could depend on."""

    eng = create_engine(url)
    with eng.begin() as cx:
        tables = sorted(
            r[0]
            for r in cx.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name LIKE 'hypermesh%'"
                )
            )
        )
        columns = sorted(
            (r[0], r[1])
            for r in cx.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_name LIKE 'hypermesh%'"
                )
            )
        )
        indexes = sorted(
            r[0]
            for r in cx.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename LIKE 'hypermesh%'")
            )
        )
        constraints = sorted(
            (r[0], r[1])
            for r in cx.execute(
                text(
                    "SELECT conrelid::regclass::text, conname FROM pg_constraint "
                    "WHERE conrelid::regclass::text LIKE 'hypermesh%'"
                )
            )
        )
        policies = sorted(
            (r[0], r[1])
            for r in cx.execute(
                text(
                    "SELECT tablename, policyname FROM pg_policies "
                    "WHERE tablename LIKE 'hypermesh%'"
                )
            )
        )
        defaults = sorted(
            (r[0], r[1])
            for r in cx.execute(
                text(
                    "SELECT table_name, coalesce(column_default, '') "
                    "FROM information_schema.columns "
                    "WHERE column_name = 'role' AND table_name LIKE 'hypermesh%'"
                )
            )
        )
    return {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "constraints": constraints,
        "policies": policies,
        "role_defaults": defaults,
    }


@pytest.fixture
def legacy_database(require_docker: None):
    """A database in the router-era shape, built by rolling 0023 back."""

    with PostgresContainer("postgres:16-alpine") as pg:
        url = normalize_sync_sqlalchemy_url(pg.get_connection_url())
        _upgrade(url)
        # The downgrade is how the old shape is reconstructed, so a downgrade
        # that silently does nothing would leave this test asserting on an
        # already-new database. Verified below before it is used.
        _downgrade(url, "0022_host_cert_override")
        yield url


@pytest.fixture
def fresh_database(require_docker: None):
    with PostgresContainer("postgres:16-alpine") as pg:
        url = normalize_sync_sqlalchemy_url(pg.get_connection_url())
        _upgrade(url)
        yield url


def test_legacy_fixture_really_is_router_era(legacy_database: str) -> None:
    """Guards the fixture: the rest of the file is vacuous without this."""

    shape = _shape(legacy_database)
    tables = shape["tables"]
    columns = shape["columns"]
    assert isinstance(tables, list) and isinstance(columns, list)

    for table in _LEGACY_TABLES:
        assert table in tables, f"{table} missing, so downgrade did not rebuild the old shape"
    assert "hypermesh_gateways" not in tables
    assert "hypermesh_gateway_metrics" not in tables
    for pair in _LEGACY_COLUMNS:
        assert pair in columns, f"{pair} missing, so downgrade did not rename columns back"


def test_upgrade_converges_on_the_fresh_shape(
    legacy_database: str, fresh_database: str
) -> None:
    _upgrade(legacy_database)

    upgraded, fresh = _shape(legacy_database), _shape(fresh_database)
    for key in ("tables", "columns", "indexes", "constraints", "policies", "role_defaults"):
        assert upgraded[key] == fresh[key], (
            f"{key} differs between a renamed database and a fresh one. "
            "A later migration that touches these by name will work on one and "
            f"fail on the other.\nrenamed: {upgraded[key]}\nfresh:   {fresh[key]}"
        )


def test_upgrade_is_idempotent(legacy_database: str) -> None:
    """A half-finished rename has to be resumable, not a permanent wedge."""

    _upgrade(legacy_database)
    after_first = _shape(legacy_database)

    _downgrade(legacy_database, "0022_host_cert_override")
    _upgrade(legacy_database)
    assert _shape(legacy_database) == after_first


def test_rename_is_a_noop_on_a_fresh_database(fresh_database: str) -> None:
    """Fresh databases get the new names from 0012, so 0023 must not fight it.

    Snapshot at 0023, not head: later revisions (lease audit, …) are not
    part of the rename and must not make this comparison fail.
    """

    _downgrade(fresh_database, _RENAME_REVISION)
    before = _shape(fresh_database)
    _downgrade(fresh_database, "0022_host_cert_override")
    _upgrade(fresh_database, _RENAME_REVISION)
    assert _shape(fresh_database) == before


def test_router_role_rows_are_migrated(legacy_database: str) -> None:
    """PlaneRole has no 'router' member, so a surviving row fails to load."""

    eng = create_engine(legacy_database)
    with eng.begin() as cx:
        tenant = cx.execute(text("SELECT id FROM tenants LIMIT 1")).scalar_one()
        cx.execute(
            text(
                "INSERT INTO hypermesh_routers "
                "(id, tenant_id, name, role, created_at) VALUES "
                "(gen_random_uuid(), :t, 'fyber-test-router', 'router', now())"
            ),
            {"t": tenant},
        )

    _upgrade(legacy_database)

    with eng.begin() as cx:
        assert cx.execute(text("SELECT count(*) FROM hypermesh_gateways")).scalar_one() == 1
        role, name = cx.execute(
            text("SELECT role, name FROM hypermesh_gateways")
        ).one()
    assert role == "gateway"
    # Seeding keys on id, so it would never correct the name on its own.
    assert name == "fyber-test-gateway"
