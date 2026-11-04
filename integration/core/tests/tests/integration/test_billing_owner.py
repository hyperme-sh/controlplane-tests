"""Platform billing owner scope, caller locks/rollback and role-gated discovery."""
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from controlplane_db.billing_store import (
    BillingPayment, add_payment, get_payment, list_payments,
    find_metadata_payment, host_transfer_payments, refresh_payment,
    subscription_tenant, transfer_scan_page, unfinished_side_effect_claims,
)


@pytest.fixture
def billing_owner(migrated_postgres):
    engine = create_engine(migrated_postgres)
    ta, tb, org = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        for tenant in [ta, tb]:
            connection.execute(text("INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)"),
                               {"id": tenant, "name": str(tenant)})
    ids = []
    with Session(engine) as session:
        for tenant in [ta, tb]:
            payment = BillingPayment(tenant_id=tenant, scope="lease", actor="user",
                payment_type="usdc", currency="usdc", amount=1000, status="succeeded",
                stripe_subscription_id=str(tenant), extra_metadata={
                    "invoice_id": "shared-invoice", "payment_rail": "stripe_stablecoin",
                    "connect_transfer_enabled": True, "settle_side_effects": {"retry": True},
                    "stripe_connect_transfer": {"host_org_id": str(org), "status": "retry"}})
            add_payment(session, payment)
            session.flush()
            ids.append(payment.id)
        session.commit()
    try:
        yield engine, ta, tb, org, ids
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": ta, "b": tb})
        engine.dispose()


def test_owner_handles_scope_copy_isolation_and_rollback(billing_owner):
    engine, ta, tb, org, ids = billing_owner
    with Session(engine) as owner, Session(engine) as observer:
        payment = get_payment(owner, ta, ids[0], lock=True)
        assert isinstance(payment, BillingPayment)
        assert not hasattr(payment, "_sa_instance_state")
        assert get_payment(owner, ta, ids[1]) is None
        assert get_payment(observer, ta, ids[0], lock=True, skip_locked=True) is None
        assert find_metadata_payment(owner, ta, "invoice_id", "shared-invoice").id == ids[0]
        assert host_transfer_payments(owner, ta, org, billing_id=ids[1]) == []
        assert [p.id for p in host_transfer_payments(owner, ta, org)] == [ids[0]]
        rows, count = list_payments(owner, tb)
        assert count == 1 and rows[0].id == ids[1]
        metadata = payment.extra_metadata
        metadata["stripe_connect_transfer"]["status"] = "changed"
        assert payment.extra_metadata["stripe_connect_transfer"]["status"] == "retry"
        payment.amount = 2000
        payment.extra_metadata = metadata
        owner.flush()
        assert get_payment(observer, ta, ids[0], refresh=True).amount == 1000
        owner.rollback()
        assert get_payment(observer, ta, ids[0], refresh=True).amount == 1000
        payment = get_payment(owner, ta, ids[0], lock=True, refresh=True)
        payment.amount = 1500
        owner.commit()
        refresh_payment(owner, payment)
        assert payment.amount == 1500
        assert get_payment(observer, ta, ids[0], refresh=True).amount == 1500


def test_registry_scans_do_not_widen_rls_authority(billing_owner, rls_isolation_role_ready):
    engine, ta, tb, _, ids = billing_owner
    with Session(engine) as session:
        assert {row[1] for row in transfer_scan_page(session)} >= set(ids)
        assert {row[0] for row in unfinished_side_effect_claims(session)} >= set(ids)
        assert subscription_tenant(session, str(tb)) == tb
        session.execute(text("SET LOCAL ROLE spanopticon_rls_tester"))
        session.execute(text("SELECT set_config('app.current_tenant_id', :id, true)"), {"id": str(ta)})
        assert {row[1] for row in transfer_scan_page(session)} == {ids[0]}
        assert {row[0] for row in unfinished_side_effect_claims(session)} == {ids[0]}
        assert subscription_tenant(session, str(tb)) is None
        assert get_payment(session, tb, ids[1]) is None
        session.rollback()
