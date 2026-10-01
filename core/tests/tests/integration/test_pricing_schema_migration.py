"""0044 adds pricing tables and a nullable lease ruleset_version.

A database already at 0043 does not have these tables: revision 0001's
create_all uses the models of the revision that ran, not this file. The
test drops the tables create_all built from today's models, then upgrades
so the migration SQL is what creates them. Downgrade drops the tables and
the lease column. round_id is the primary key. ruleset_version and
engine_version are NOT NULL on rounds and fills.
"""

from __future__ import annotations

import sys
import uuid

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from testcontainers.postgres import PostgresContainer

from panopticon_tests.fixtures.postgres import (
    alembic_ini_path,
    normalize_sync_sqlalchemy_url,
)

pytestmark = pytest.mark.integration

_PARENT = "0043_device_host_type"
_REV = "0044_pricing_schema"
_LEASE = "0045_lease_failed_at"
_EVENTS = "0046_controlplane_events"
_IDEMPOTENCY = "0047_event_handler_idempotency"
_HEAD = "0051_host_org_bindings"
_TENANT = "8f8e8d8c-7b6a-4a59-8c3d-2e1f0a9b8c7d"
_HASH = "ab" * 32
_COMMIT = "cd" * 32
_TABLES = (
    "hypermesh_pricing_alerts",
    "hypermesh_fills",
    "hypermesh_orders",
    "hypermesh_pool_prices",
    "hypermesh_cost_inputs",
    "hypermesh_pricing_rounds",
    "hypermesh_pricing_pools",
)


def _upgrade(url: str, revision: str = "head") -> None:
    import os

    os.environ["DATABASE_URL"] = url
    command.upgrade(Config(str(alembic_ini_path())), revision)


def _downgrade(url: str, revision: str) -> None:
    import os

    os.environ["DATABASE_URL"] = url
    command.downgrade(Config(str(alembic_ini_path())), revision)


@pytest.fixture
def postgres(require_docker: None):
    with PostgresContainer("postgres:16-alpine") as pg:
        yield normalize_sync_sqlalchemy_url(pg.get_connection_url())


def _tables(url: str) -> set[str]:
    eng = create_engine(url)
    with eng.begin() as cx:
        rows = cx.execute(
            text(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(:names)
                """
            ),
            {"names": list(_TABLES)},
        ).all()
    return {row[0] for row in rows}


def _nullable(url: str, table: str, column: str) -> str:
    eng = create_engine(url)
    with eng.begin() as cx:
        row = cx.execute(
            text(
                """
                SELECT is_nullable FROM information_schema.columns
                WHERE table_name = :table AND column_name = :column
                """
            ),
            {"table": table, "column": column},
        ).one()
    return row[0]


def _version(url: str) -> str:
    eng = create_engine(url)
    with eng.begin() as cx:
        return cx.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _strip_pricing_objects(url: str) -> None:
    """Pretend this database was migrated before the pricing models existed."""

    eng = create_engine(url)
    with eng.begin() as cx:
        for table in _TABLES:
            cx.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        cx.execute(text("ALTER TABLE hypermesh_leases DROP COLUMN IF EXISTS ruleset_version"))


def _insert_round(cx, round_id: str, *, idempotency_key: str, ruleset: str | None = "2026-09-25.1") -> None:
    cx.execute(
        text(
            """
            INSERT INTO hypermesh_pricing_rounds (
                round_id, tenant_id, round_start, round_type, ruleset_version,
                engine_version, degraded, request_hash, idempotency_key,
                lottery_seed_commit, lottery_seed, created_at
            ) VALUES (
                :round_id, CAST(:tenant AS uuid), now(), 'realtime', :ruleset,
                '0.1.1', false, :request_hash, :idempotency_key,
                :commit, NULL, now()
            )
            """
        ),
        {
            "round_id": round_id,
            "tenant": _TENANT,
            "ruleset": ruleset,
            "request_hash": _HASH,
            "idempotency_key": idempotency_key,
            "commit": _COMMIT,
        },
    )


def test_pricing_revision_revises_device_host_type() -> None:
    src = alembic_ini_path().parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    script = ScriptDirectory(str(src / "controlplane_db" / "alembic"))
    assert script.get_heads() == [_HEAD]
    revision = script.get_revision(_REV)
    assert revision is not None
    assert revision.down_revision == _PARENT
    lease = script.get_revision(_LEASE)
    assert lease is not None
    assert lease.down_revision == _REV
    events = script.get_revision(_EVENTS)
    assert events is not None
    assert events.down_revision == _LEASE
    idem = script.get_revision(_IDEMPOTENCY)
    assert idem is not None
    assert idem.down_revision == _EVENTS
    head = script.get_revision(_HEAD)
    assert head is not None
    assert head.down_revision == "0050_host_lifecycle"
    lifecycle = script.get_revision("0050_host_lifecycle")
    assert lifecycle is not None
    assert lifecycle.down_revision == "0049_idle_tunnel_lease"
    disk = script.get_revision("0048_lab_pull_disk")
    assert disk is not None
    assert disk.down_revision == _IDEMPOTENCY


def test_pricing_schema_upgrade_and_downgrade(postgres: str) -> None:
    _upgrade(postgres, _PARENT)
    assert _version(postgres) == _PARENT
    _strip_pricing_objects(postgres)
    assert _tables(postgres) == set()

    _upgrade(postgres, _REV)
    assert _version(postgres) == _REV
    assert _tables(postgres) == set(_TABLES)
    assert _nullable(postgres, "hypermesh_pricing_rounds", "ruleset_version") == "NO"
    assert _nullable(postgres, "hypermesh_pricing_rounds", "engine_version") == "NO"
    assert _nullable(postgres, "hypermesh_pricing_rounds", "lottery_seed") == "YES"
    assert _nullable(postgres, "hypermesh_fills", "ruleset_version") == "NO"
    assert _nullable(postgres, "hypermesh_fills", "engine_version") == "NO"
    assert _nullable(postgres, "hypermesh_fills", "device_id") == "YES"
    assert _nullable(postgres, "hypermesh_fills", "lease_id") == "YES"
    assert _nullable(postgres, "hypermesh_fills", "total_cents") == "NO"
    assert _nullable(postgres, "hypermesh_orders", "prepay_ref") == "YES"
    assert _nullable(postgres, "hypermesh_leases", "ruleset_version") == "YES"

    eng = create_engine(postgres)
    with eng.begin() as cx:
        cx.execute(
            text(
                """
                INSERT INTO hypermesh_pricing_pools (
                    pool_id, tenant_id, class_id, region, kind, created_at
                ) VALUES (
                    'agx-large@default', CAST(:tenant AS uuid), 'agx-large',
                    'default', 'P1', now()
                )
                """
            ),
            {"tenant": _TENANT},
        )
        _insert_round(cx, "rt-1", idempotency_key="idem-1")
        cx.execute(
            text(
                """
                INSERT INTO hypermesh_orders (
                    order_id, tenant_id, org_id, pool_id, rungs, tip_cents,
                    hours, status, prepay_ref, created_at
                ) VALUES (
                    'ord-1', CAST(:tenant AS uuid), 'org-1', 'agx-large@default',
                    CAST(:rungs AS jsonb), 0, 2, 'open', NULL, now()
                )
                """
            ),
            {"tenant": _TENANT, "rungs": '[{"pool_id":"agx-large@default","max_cents":80}]'},
        )
        cx.execute(
            text(
                """
                INSERT INTO hypermesh_fills (
                    id, tenant_id, round_id, order_id, pool_id, org_id,
                    rung_index, hours, device_id, lease_id, pay_cents, total_cents,
                    tip_cents, lock_until, ruleset_version, engine_version
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant AS uuid), 'rt-1', 'ord-1',
                    'agx-large@default', 'org-1', 0, 2, NULL, NULL,
                    46, 92, 0, now(), '2026-09-25.1', '0.1.1'
                )
                """
            ),
            {"id": str(uuid.uuid4()), "tenant": _TENANT},
        )

    with pytest.raises(IntegrityError):
        with eng.begin() as cx:
            _insert_round(cx, "rt-1", idempotency_key="idem-2")

    with pytest.raises(DBAPIError):
        with eng.begin() as cx:
            _insert_round(cx, "rt-missing-ruleset", idempotency_key="idem-3", ruleset=None)

    _downgrade(postgres, _PARENT)
    assert _version(postgres) == _PARENT
    assert _tables(postgres) == set()
    eng = create_engine(postgres)
    with eng.begin() as cx:
        missing = cx.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'hypermesh_leases' AND column_name = 'ruleset_version'
                """
            )
        ).first()
    assert missing is None

    _upgrade(postgres, "head")
    assert _version(postgres) == _HEAD
    assert _tables(postgres) == set(_TABLES)
    assert _nullable(postgres, "hypermesh_leases", "ruleset_version") == "YES"
    assert _nullable(postgres, "hypermesh_leases", "failed_at") == "YES"
    assert _nullable(postgres, "hypermesh_device_health", "disk_free_bytes") == "YES"
    assert _nullable(postgres, "hypermesh_device_health", "model_cache_evictable_bytes") == "YES"
    assert _nullable(postgres, "hypermesh_catalog", "size_bytes") == "YES"
    assert _nullable(postgres, "hypermesh_jobs", "dispatch_skip_reason") == "YES"


def test_duplicate_acl_rules_collapse_before_unique_index(postgres: str) -> None:
    """0047 must not fail when identical acl_casbin_rules rows already exist."""

    _upgrade(postgres, _EVENTS)
    assert _version(postgres) == _EVENTS
    tenant = uuid.uuid4()
    eng = create_engine(postgres)
    with eng.begin() as cx:
        cx.execute(
            text(
                "INSERT INTO tenants (id, name, is_active) VALUES (CAST(:id AS uuid), :name, true)"
            ),
            {"id": str(tenant), "name": f"dup-{tenant.hex[:8]}"},
        )
        ids = [
            int(row[0])
            for row in cx.execute(
                text(
                    """
                    INSERT INTO acl_casbin_rules (tenant_id, ptype, v0, v1, v2)
                    VALUES
                        (CAST(:tenant AS uuid), 'p', 'user', 'user_profile', 'read'),
                        (CAST(:tenant AS uuid), 'p', 'user', 'user_profile', 'read'),
                        (CAST(:tenant AS uuid), 'p', 'other', 'user_profile', 'read')
                    RETURNING id
                    """
                ),
                {"tenant": str(tenant)},
            ).fetchall()
        ]
    kept = min(ids[0], ids[1])
    _upgrade(postgres, "head")
    assert _version(postgres) == _HEAD
    with eng.connect() as cx:
        left = [
            int(row[0])
            for row in cx.execute(
                text(
                    """
                    SELECT id FROM acl_casbin_rules
                    WHERE tenant_id = CAST(:tenant AS uuid)
                    ORDER BY id
                    """
                ),
                {"tenant": str(tenant)},
            ).fetchall()
        ]
        indexdef = cx.execute(
            text(
                """
                SELECT indexdef FROM pg_indexes
                WHERE indexname = 'uq_acl_casbin_rules_identity'
                """
            )
        ).scalar_one()
    assert left == sorted([kept, ids[2]])
    assert "NULLS NOT DISTINCT" in indexdef


def test_duplicate_acl_rules_collapse_as_nobypassrls_owner(postgres: str) -> None:
    """0047 must dedupe when the table owner is subject to FORCE RLS."""

    _upgrade(postgres, _EVENTS)
    role = "acl_rule_owner"
    tenant = uuid.uuid4()
    eng = create_engine(postgres)
    with eng.begin() as cx:
        cx.execute(
            text(
                f"""
                DO $$ BEGIN
                    CREATE ROLE {role} NOINHERIT NOBYPASSRLS LOGIN PASSWORD 'ownerpass';
                EXCEPTION WHEN duplicate_object THEN
                    NULL;
                END $$;
                """
            )
        )
        flags = cx.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :role"),
            {"role": role},
        ).one()
        assert flags == (False, False)
        cx.execute(
            text(
                "INSERT INTO tenants (id, name, is_active) VALUES (CAST(:id AS uuid), :name, true)"
            ),
            {"id": str(tenant), "name": f"own-{tenant.hex[:8]}"},
        )
        ids = [
            int(row[0])
            for row in cx.execute(
                text(
                    """
                    INSERT INTO acl_casbin_rules (tenant_id, ptype, v0, v1, v2)
                    VALUES
                        (CAST(:tenant AS uuid), 'p', 'user', 'user_profile', 'read'),
                        (CAST(:tenant AS uuid), 'p', 'user', 'user_profile', 'read'),
                        (CAST(:tenant AS uuid), 'p', 'other', 'user_profile', 'read')
                    RETURNING id
                    """
                ),
                {"tenant": str(tenant)},
            ).fetchall()
        ]
        cx.execute(text(f"ALTER TABLE acl_casbin_rules OWNER TO {role}"))
        cx.execute(text(f"ALTER TABLE ai_budget_event_audit OWNER TO {role}"))
        cx.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON alembic_version TO {role}")
        )
        cx.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {role}"))
        database = make_url(postgres).database
        cx.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {role}'))
    kept = min(ids[0], ids[1])
    owner_url = make_url(postgres).set(username=role, password="ownerpass")
    # This role owns the ACL and budget tables 0047 changes. The database
    # owner applies later revisions, including lab_pull disk columns.
    _upgrade(owner_url.render_as_string(hide_password=False), _IDEMPOTENCY)
    assert _version(postgres) == _IDEMPOTENCY
    _upgrade(postgres, "head")
    assert _version(postgres) == _HEAD
    with eng.connect() as cx:
        owner = cx.execute(
            text(
                """
                SELECT r.rolname
                FROM pg_class AS c
                JOIN pg_roles AS r ON r.oid = c.relowner
                WHERE c.relname = 'acl_casbin_rules'
                """
            )
        ).scalar_one()
        rls = cx.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = 'acl_casbin_rules'
                """
            )
        ).one()
        left = [
            int(row[0])
            for row in cx.execute(
                text(
                    """
                    SELECT id FROM acl_casbin_rules
                    WHERE tenant_id = CAST(:tenant AS uuid)
                    ORDER BY id
                    """
                ),
                {"tenant": str(tenant)},
            ).fetchall()
        ]
        indexdef = cx.execute(
            text(
                """
                SELECT indexdef FROM pg_indexes
                WHERE indexname = 'uq_acl_casbin_rules_identity'
                """
            )
        ).scalar_one()
    assert owner == role
    assert rls == (True, True)
    assert left == sorted([kept, ids[2]])
    assert "NULLS NOT DISTINCT" in indexdef
