"""Agent-budget owner commands retain tenant and transaction boundaries."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from controlplane_db.budget_store import (
    burn_legacy_budget_allocation,
    create_budget,
    get_budget,
    list_budgets_for_tenant,
    try_allocate,
    user_exists_for_tenant,
)
from controlplane_db.models import CryptoAgentBudget, Tenant, User

pytestmark = pytest.mark.integration


@pytest.fixture
def budget_store(migrated_postgres):
    engine = create_engine(migrated_postgres)
    ta, tb = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as session:
        session.add_all([Tenant(id=ta, name=str(ta)), Tenant(id=tb, name=str(tb))])
        session.flush()
        user = User(tenant_id=ta, email=str(ta) + "@example.com", first_name="Test",
                    last_name="Budget", gender="unknown", is_active=True)
        session.add(user)
        session.commit()
        uid = user.id
    try:
        yield engine, ta, tb, uid
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM tenants WHERE id IN (:ta, :tb)"),
                               {"ta": ta, "tb": tb})
        engine.dispose()


def budget(engine, tenant, *, user=None, ceiling=100, label=None):
    return create_budget(engine, tenant, owner_pubkey="owner", mint_pubkey="mint",
                         ceiling_lamports=ceiling, label=label, user_id=user)


def test_owner_crud_filters_tenant_and_returns_immutable_views(budget_store):
    engine, ta, tb, user = budget_store
    a = budget(engine, ta, user=user)
    b = budget(engine, tb)
    assert user_exists_for_tenant(engine, ta, user)
    assert not user_exists_for_tenant(engine, tb, user)
    with pytest.raises(ValueError, match="requested tenant"):
        budget(engine, tb, user=user)
    assert get_budget(engine, tb, a.id) is None
    assert try_allocate(engine, tb, a.id, 1) is None
    assert get_budget(engine, ta, b.id) is None
    rows, total = list_budgets_for_tenant(engine, ta)
    assert total == 1 and rows == [a]
    assert list_budgets_for_tenant(engine, ta, offset=1) == ([], 1)
    with pytest.raises(FrozenInstanceError):
        a.allocated_lamports = 99
    assert try_allocate(engine, ta, a.id, 70).allocated_lamports == 70
    assert a.allocated_lamports == 0
    assert try_allocate(engine, ta, a.id, 31) is None
    assert try_allocate(engine, ta, a.id, 0) is None
    assert get_budget(engine, ta, a.id).allocated_lamports == 70


def test_concurrent_allocations_cannot_overcommit_the_ceiling(budget_store):
    engine, ta, _, _ = budget_store
    a = budget(engine, ta)
    start = Barrier(2)

    def allocate():
        start.wait(timeout=5)
        return try_allocate(engine, ta, a.id, 70)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(allocate) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert sum(result is not None for result in results) == 1
    assert get_budget(engine, ta, a.id).allocated_lamports == 70


def test_burn_stays_in_caller_transaction_and_filters_tenant(budget_store):
    engine, ta, tb, user = budget_store
    a = budget(engine, ta, user=user, label="fake-usdc")
    try_allocate(engine, ta, a.id, 70)
    with Session(engine) as session:
        burn_legacy_budget_allocation(session, ta, budget_id=a.id, user_id=None, atoms=20)
        inside = session.execute(text("SELECT allocated_lamports FROM crypto_agent_budgets WHERE id=:id"),
                                 {"id": a.id}).scalar_one()
        assert inside == 50
        assert get_budget(engine, ta, a.id).allocated_lamports == 70
        session.rollback()
    assert get_budget(engine, ta, a.id).allocated_lamports == 70
    with Session(engine) as session:
        burn_legacy_budget_allocation(session, tb, budget_id=a.id, user_id=None, atoms=70)
        session.commit()
    assert get_budget(engine, ta, a.id).allocated_lamports == 70
    with Session(engine) as session:
        # Legacy fallback is retained for billing metadata without a budget id.
        burn_legacy_budget_allocation(session, ta, budget_id=None, user_id=user, atoms=20)
        session.commit()
    assert get_budget(engine, ta, a.id).allocated_lamports == 50
    with Session(engine) as session:
        burn_legacy_budget_allocation(session, ta, budget_id=a.id, user_id=None, atoms=0)
        session.commit()
    assert get_budget(engine, ta, a.id).allocated_lamports == 50


def test_scoped_commands_work_with_non_bypass_rls_role(budget_store):
    engine, ta, tb, _ = budget_store
    b = budget(engine, tb)
    role = "budget_owner_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        connection.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE ON crypto_agent_budgets TO "{role}"')
    scoped = create_engine(engine.url)

    @event.listens_for(scoped, "begin")
    def bind_role(connection):
        connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')

    try:
        a = budget(scoped, ta)
        assert try_allocate(scoped, ta, a.id, 10).allocated_lamports == 10
        assert get_budget(scoped, ta, b.id) is None
        rows, total = list_budgets_for_tenant(scoped, ta)
        assert total == 1 and rows[0].id == a.id
        with Session(scoped) as session:
            burn_legacy_budget_allocation(session, ta, budget_id=a.id, user_id=None, atoms=5)
            session.commit()
        assert get_budget(scoped, ta, a.id).allocated_lamports == 5
    finally:
        scoped.dispose()
        with engine.begin() as connection:
            connection.exec_driver_sql(f'REVOKE ALL ON crypto_agent_budgets FROM "{role}"')
            connection.exec_driver_sql(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
            connection.exec_driver_sql(f'DROP ROLE "{role}"')


def test_burn_refreshes_a_cached_budget_after_concurrent_allocation(budget_store):
    engine, ta, _, _ = budget_store
    a = budget(engine, ta)
    try_allocate(engine, ta, a.id, 70)
    with Session(engine) as session:
        cached = session.get(CryptoAgentBudget, a.id)
        assert cached.allocated_lamports == 70
        assert try_allocate(engine, ta, a.id, 10).allocated_lamports == 80
        burn_legacy_budget_allocation(session, ta, budget_id=a.id, user_id=None, atoms=20)
        assert cached.allocated_lamports == 60
        session.commit()
    assert get_budget(engine, ta, a.id).allocated_lamports == 60
