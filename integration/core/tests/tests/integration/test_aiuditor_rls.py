"""AIuditor grant tables are tenant-isolated. Not TURN. Not Hypermesh catalog."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

_ROLE = "panopticon_rls_tester"
_TABLES = {
    "aiuditor_connections",
    "aiuditor_approvals",
    "aiuditor_policies",
    "aiuditor_contracts",
    "aiuditor_violations",
    "aiuditor_catalog",
    "aiuditor_envelopes",
    "aiuditor_suite_members",
    "aiuditor_admissions",
    "aiuditor_candidates",
}


def test_aiuditor_contracts_isolated_by_tenant(
    migrated_postgres: str,
    rls_isolation_role_ready: None,
) -> None:
    ta, tb = uuid.uuid4(), uuid.uuid4()
    ca, cb = uuid.uuid4(), uuid.uuid4()
    eng = create_engine(migrated_postgres)
    with eng.begin() as cx:
        cx.execute(
            text(
                "INSERT INTO tenants (id, name, is_active) VALUES "
                "(:ta, :na, true), (:tb, :nb, true)"
            ),
            {
                "ta": ta,
                "na": f"t-a-{ta.hex[:8]}",
                "tb": tb,
                "nb": f"t-b-{tb.hex[:8]}",
            },
        )
        cx.execute(
            text(
                """
                INSERT INTO aiuditor_contracts
                    (id, tenant_id, parties, meters, rail, until, status, created_at)
                VALUES
                    (:ca, :ta, CAST(:pa AS jsonb), CAST(:ma AS jsonb),
                     'stripe', now() + interval '1 hour', 'signed', now()),
                    (:cb, :tb, CAST(:pb AS jsonb), CAST(:mb AS jsonb),
                     'stripe', now() + interval '1 hour', 'signed', now())
                """
            ),
            {
                "ca": ca,
                "cb": cb,
                "ta": ta,
                "tb": tb,
                "pa": '[{"role":"renter","subject":"a"},{"role":"host","subject":"h"}]',
                "pb": '[{"role":"renter","subject":"b"},{"role":"host","subject":"h"}]',
                "ma": '["wall_time"]',
                "mb": '["wall_time"]',
            },
        )
        cx.execute(text(f"GRANT SELECT ON aiuditor_contracts TO {_ROLE}"))

    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id FROM aiuditor_contracts WHERE id IN (:a, :b)"),
            {"a": ca, "b": cb},
        ).fetchall()
    assert {r[0] for r in rows} == {ca, cb}

    with eng.connect() as conn:
        conn.execute(text(f"SET ROLE {_ROLE}"))
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(ta)},
        )
        rows = conn.execute(text("SELECT id FROM aiuditor_contracts ORDER BY id")).fetchall()
        assert [r[0] for r in rows] == [ca]

    with eng.connect() as conn:
        conn.execute(text(f"SET ROLE {_ROLE}"))
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tb)},
        )
        rows = conn.execute(text("SELECT id FROM aiuditor_contracts ORDER BY id")).fetchall()
        assert [r[0] for r in rows] == [cb]


def test_aiuditor_tables_and_lease_contract_id_exist(migrated_postgres: str) -> None:
    eng = create_engine(migrated_postgres)
    with eng.begin() as cx:
        names = {
            r[0]
            for r in cx.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name LIKE 'aiuditor_%'"
                )
            )
        }
        assert names == set(_TABLES)
        assert "turn_allocate" not in names
        col = cx.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'hypermesh_leases' AND column_name = 'contract_id'"
            )
        ).scalar_one()
        assert col == "contract_id"
        rls = {
            r[0]
            for r in cx.execute(
                text(
                    "SELECT relname FROM pg_class "
                    "WHERE relname LIKE 'aiuditor_%' AND relrowsecurity"
                )
            )
        }
        assert rls == set(_TABLES)
