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


def test_host_organization_bindings_isolate_tenants_and_enforce_org_scope(
    migrated_postgres: str,
    rls_isolation_role_ready: None,
) -> None:
    from sqlalchemy.exc import IntegrityError, ProgrammingError

    ta, tb, oa, ob, unbound = (uuid.uuid4() for _ in range(5))
    subject = f"host-{uuid.uuid4()}"
    eng = create_engine(migrated_postgres)
    try:
        with eng.begin() as cx:
            for tenant, org in ((ta, oa), (tb, ob)):
                cx.execute(text("INSERT INTO tenants (id, name, is_active) VALUES (:id, :name, true)"),
                           {"id": tenant, "name": f"host-rls-{tenant}"})
                cx.execute(text("INSERT INTO organizations (id, tenant_id, legal_name) VALUES (:id, :tenant, :name)"),
                           {"id": org, "tenant": tenant, "name": f"Host {org}"})
                cx.execute(text("INSERT INTO host_organization_bindings (tenant_id, keycloak_sub, organization_id) VALUES (:tenant, :sub, :org)"),
                           {"tenant": tenant, "sub": subject, "org": org})

        with eng.begin() as cx:
            cx.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON host_organization_bindings TO panopticon_rls_tester"))
            cx.execute(text("INSERT INTO organizations (id, tenant_id, legal_name) VALUES (:id, :tenant, :name)"),
                       {"id": unbound, "tenant": tb, "name": f"Unbound {unbound}"})

        # FK enforcement also protects writes by privileged database users.
        with eng.begin() as cx:
            with pytest.raises(IntegrityError, match="fk_host_org_binding_organization_tenant"):
                with cx.begin_nested():
                    cx.execute(text("INSERT INTO host_organization_bindings (tenant_id, keycloak_sub, organization_id) VALUES (:tenant, 'wrong-tenant', :org)"),
                               {"tenant": ta, "org": unbound})

        for tenant, expected, other in ((ta, oa, tb), (tb, ob, ta)):
            with eng.begin() as cx:
                cx.execute(text("SET LOCAL ROLE panopticon_rls_tester"))
                cx.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant)})
                rows = cx.execute(text("SELECT organization_id FROM host_organization_bindings WHERE keycloak_sub = :sub"),
                                  {"sub": subject}).scalars().all()
                assert rows == [expected]
                assert cx.execute(text("DELETE FROM host_organization_bindings WHERE tenant_id = :other"),
                                  {"other": other}).rowcount == 0
                assert cx.execute(text("UPDATE host_organization_bindings SET keycloak_sub = 'stolen' WHERE tenant_id = :other"),
                                  {"other": other}).rowcount == 0
                with pytest.raises(ProgrammingError):
                    with cx.begin_nested():
                        cx.execute(text("INSERT INTO host_organization_bindings (tenant_id, keycloak_sub, organization_id) VALUES (:tenant, 'forged', :org)"),
                                   {"tenant": other, "org": expected})
        with eng.begin() as cx:
            cx.execute(text("SET LOCAL ROLE panopticon_rls_tester"))
            cx.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
            assert cx.execute(text("SELECT organization_id FROM host_organization_bindings")).all() == []
    finally:
        eng.dispose()


def test_host_binding_migration_on_existing_schema(migrated_postgres: str) -> None:
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[4] / (
        "core/lib/controlplane-db/src/controlplane_db/alembic/versions/"
        "20261001_0051_host_org_bindings.py"
    )
    spec = importlib.util.spec_from_file_location("host_org_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(migrated_postgres)
    try:
        with engine.connect() as cx:
            transaction = cx.begin()
            try:
                with Operations.context(MigrationContext.configure(cx)):
                    # This historical migration owns the org uniqueness constraint;
                    # remove the newer dependent table within this rolled-back test.
                    cx.execute(text("DROP TABLE hypermesh_onboarding_runs"))
                    cx.execute(text("DROP TABLE hypermesh_host_registrations"))
                    migration.downgrade()
                    assert cx.execute(text("SELECT to_regclass('host_organization_bindings')")).scalar() is None
                    migration.upgrade()
                    migration.upgrade()  # Same path as current-metadata bootstrap.
                assert cx.execute(text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'host_organization_bindings'::regclass"
                )).scalar() is True
                assert cx.execute(text(
                    "SELECT count(*) FROM pg_policies WHERE tablename = 'host_organization_bindings'"
                )).scalar() == 4
                assert cx.execute(text(
                    "SELECT count(*) FROM pg_constraint WHERE conrelid = 'host_organization_bindings'::regclass "
                    "AND conname = 'fk_host_org_binding_organization_tenant'"
                )).scalar() == 1
            finally:
                transaction.rollback()  # Preserve any other tests' fixture rows.
    finally:
        engine.dispose()
