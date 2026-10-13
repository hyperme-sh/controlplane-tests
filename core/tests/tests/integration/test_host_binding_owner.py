"""Binding owner scope, tenant foreign keys and original enrollment lock compatibility."""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from host_binding_store import bind_organization, lock_identity, organization_id


@pytest_asyncio.fixture
async def binding_owner(migrated_postgres, db_async_url):
    engine = create_engine(migrated_postgres)
    async_engine = create_async_engine(db_async_url)
    ta = uuid.UUID('00000000-0000-4000-8000-000000000071')
    tb, oa, ob = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        for tenant, org in [(ta, oa), (tb, ob)]:
            connection.execute(text("INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)"),
                               {"id": tenant, "name": str(tenant)})
            connection.execute(text("INSERT INTO organizations(id,tenant_id,legal_name) VALUES(:id,:tenant,'owner test')"),
                               {"id": org, "tenant": tenant})
    try:
        yield engine, async_sessionmaker(async_engine, expire_on_commit=False), ta, tb, oa, ob
    finally:
        await async_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": ta, "b": tb})
        engine.dispose()


@pytest.mark.asyncio
async def test_binding_scope_whole_transaction_and_cross_tenant_refusal(binding_owner):
    _, maker, ta, tb, oa, ob = binding_owner
    async with maker() as caller, maker() as observer:
        await lock_identity(caller, ta, 'verified-subject')
        await bind_organization(caller, ta, 'verified-subject', oa)
        assert await organization_id(caller, ta, 'verified-subject') == oa
        assert await organization_id(observer, ta, 'verified-subject') is None
        await caller.rollback()
        assert await organization_id(observer, ta, 'verified-subject') is None
        await lock_identity(caller, ta, 'verified-subject')
        await bind_organization(caller, ta, 'verified-subject', oa)
        await caller.commit()
        await lock_identity(caller, tb, 'verified-subject')
        await bind_organization(caller, tb, 'verified-subject', ob)
        await caller.commit()
        assert await organization_id(observer, ta, 'verified-subject') == oa
        assert await organization_id(observer, tb, 'verified-subject') == ob
        assert await organization_id(observer, ta, 'other-subject') is None
        with pytest.raises(IntegrityError):
            await bind_organization(caller, ta, 'different-subject', ob)
        await caller.rollback()
        assert await organization_id(observer, ta, 'different-subject') is None


@pytest.mark.asyncio
async def test_original_lock_key_blocks_and_releases_only_with_caller_completion(binding_owner):
    _, maker, ta, tb, _, _ = binding_owner
    # Fixed key from the pre-extraction v1 encoding for this tenant and subject.
    old_key = 8281518114384836373
    async with maker() as legacy, maker() as owner:
        await legacy.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": old_key})
        await owner.execute(text("SET LOCAL lock_timeout = '100ms'"))
        await lock_identity(owner, tb, 'owner-lock-compat')
        with pytest.raises(DBAPIError):
            await lock_identity(owner, ta, 'owner-lock-compat')
        await owner.rollback()
        await legacy.rollback()
        await lock_identity(owner, ta, 'owner-lock-compat')
        assert not await legacy.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": old_key})
        await owner.commit()
        assert await legacy.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": old_key})


@pytest.mark.asyncio
async def test_binding_owner_retains_caller_rls(binding_owner, rls_isolation_role_ready):
    engine, maker, ta, tb, oa, ob = binding_owner
    with engine.begin() as connection:
        connection.execute(text("GRANT SELECT ON host_organization_bindings TO panopticon_rls_tester"))
    async with maker() as session:
        await bind_organization(session, ta, 'verified-subject', oa)
        await bind_organization(session, tb, 'verified-subject', ob)
        await session.commit()
        await session.execute(text("SET LOCAL ROLE panopticon_rls_tester"))
        await session.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(ta)})
        assert await organization_id(session, ta, 'verified-subject') == oa
        assert await organization_id(session, tb, 'verified-subject') is None
