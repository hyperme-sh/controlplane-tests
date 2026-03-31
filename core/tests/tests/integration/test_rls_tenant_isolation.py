"""Postgres RLS: restricted role + app.current_tenant_id must hide other tenants' rows."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_rls_billing_records_isolated_by_tenant(
    migrated_postgres: str,
    rls_isolation_role_ready: None,
) -> None:
    ta, tb = uuid.uuid4(), uuid.uuid4()
    bra, brb = uuid.uuid4(), uuid.uuid4()
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
                INSERT INTO billing_records
                    (id, tenant_id, scope, actor, payment_type, currency, status)
                VALUES
                    (:bra, :ta, 'subscription', 'user', 'card', 'usd', 'pending'),
                    (:brb, :tb, 'subscription', 'user', 'card', 'usd', 'pending')
                """
            ),
            {"bra": bra, "brb": brb, "ta": ta, "tb": tb},
        )

    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id FROM billing_records WHERE id IN (:a, :b) ORDER BY id"),
            {"a": bra, "b": brb},
        ).fetchall()
    assert {r[0] for r in rows} == {bra, brb}

    with eng.connect() as conn:
        conn.execute(text("SET ROLE panopticon_rls_tester"))
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(ta)},
        )
        rows = conn.execute(text("SELECT id FROM billing_records ORDER BY id")).fetchall()
        assert [r[0] for r in rows] == [bra]

    with eng.connect() as conn:
        conn.execute(text("SET ROLE panopticon_rls_tester"))
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tb)},
        )
        rows = conn.execute(text("SELECT id FROM billing_records ORDER BY id")).fetchall()
        assert [r[0] for r in rows] == [brb]
