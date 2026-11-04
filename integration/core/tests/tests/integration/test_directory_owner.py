"""Directory owner boundaries, detached views and caller transactions."""
from dataclasses import FrozenInstanceError
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from controlplane_db.models import Group, Role, Tenant, UserPreference, UserGroupMembership
from controlplane_tenancy.directory import (
    DirectoryLinkError, UserCreateData, UserUpdateData,
    SqlUserRepository, SqlRoleRepository, SqlGroupRepository,
    SqlOrganizationRepository, SqlOrganizationIdpLinkRepository,
    SqlMembershipRepository, clear_model_preference,
)
from controlplane_tenancy.tenant_guc import set_current_tenant_id_guc
from controlplane_tenancy.tenant_membership import get_tenant_role_group_ids


@pytest_asyncio.fixture
async def directory(async_engine):
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    ta, tb, ra, rb = [uuid.uuid4() for _ in range(4)]
    async with maker() as session:
        session.add_all([Tenant(id=ta, name=str(ta), is_active=True), Tenant(id=tb, name=str(tb), is_active=True)])
        await session.flush()
        session.add_all([Role(id=ra, tenant_id=ta, name="admin", permissions=["invite"]),
                         Role(id=rb, tenant_id=tb, name="admin", permissions=[])])
        await session.commit()
    try:
        yield maker, ta, tb, ra, rb
    finally:
        async with maker() as session:
            await session.execute(text("DELETE FROM tenants WHERE id IN (:ta, :tb)"),
                                  {"ta": ta, "tb": tb})
            await session.commit()


def user_body():
    return UserCreateData(uuid.uuid4().hex + "@example.com", "Directory", "User")


@pytest.mark.asyncio
async def test_crud_views_and_joined_directory_queries_stay_in_tenant(directory):
    maker, ta, tb, ra, rb = directory
    async with maker() as session:
        users, roles = SqlUserRepository(session), SqlRoleRepository(session)
        groups, memberships = SqlGroupRepository(session), SqlMembershipRepository(session)
        orgs, links = SqlOrganizationRepository(session), SqlOrganizationIdpLinkRepository(session)
        a = await users.create(ta, user_body())
        b = await users.create(tb, user_body())
        group = await groups.create(ta, "host", "host group", ra)
        assert await roles.get(tb, ra) is None
        assert await users.get(tb, a.id) is None
        assert await users.get_by_email(tb, a.email) is None
        assert await users.get_by_email(ta, a.email.upper()) == a
        assert await groups.get(tb, group.id) is None
        assert await groups.get_by_name(tb, "host") is None
        assert (await groups.get_by_name(ta, "host")).id == group.id
        assert (await groups.list_paged(ta, offset=0, limit=10, q="host"))[1] == 1
        with pytest.raises(DirectoryLinkError):
            await groups.create(ta, "bad-role", None, rb)
        with pytest.raises(DirectoryLinkError):
            await users.set_role(ta, a.id, rb)
        await users.set_role(ta, a.id, ra)
        with pytest.raises(DirectoryLinkError):
            await memberships.add(ta, b.id, group.id)
        member = await memberships.add(ta, a.id, group.id)
        assert member.user_id == a.id
        assert await memberships.exists(ta, a.id, group.id)
        assert not await memberships.exists(tb, a.id, group.id)
        assert await memberships.list_for_group(tb, group.id) == []
        assert [row.id for row in await memberships.groups_for_user(ta, a.id)] == [group.id]
        assert await get_tenant_role_group_ids(session, ta, user_id=a.id, role="HOST") == [group.id]
        pairs = await memberships.list_for_group(ta, group.id)
        assert pairs[0][0].id == member.id and pairs[0][1].id == a.id
        org = await orgs.create(ta, "Example")
        assert await orgs.get(tb, org.id) is None
        assert (await orgs.list_paged(ta, offset=0, limit=1, q="example"))[1] == 1
        with pytest.raises(DirectoryLinkError):
            await links.create(tenant_id=tb, organization_id=org.id,
                               provider="github", external_key="example", linked_by_sub="owner")
        link = await links.create(tenant_id=ta, organization_id=org.id,
                                  provider="github", external_key="example", linked_by_sub="owner")
        assert await links.find(tb, "github", "example") is None
        assert await links.list_for_org(tb, org.id) == []
        assert await links.list_for_org(ta, org.id) == [link]
        assert not await links.delete(tb, org.id, link.id)
        assert await links.delete(ta, org.id, link.id)
        assert await links.find(ta, "github", "example") is None
        assert not await memberships.remove(tb, a.id, group.id)
        assert await memberships.remove(ta, a.id, group.id)
        assert not await memberships.exists(ta, a.id, group.id)
        assert (await users.list_active_paged(ta, offset=0, limit=5, q=a.email))[1] == 1
        assert len(await users.list_active(ta)) == 1
        with pytest.raises(FrozenInstanceError):
            a.first_name = "changed"
        attributes = {"teams": ["one"]}
        updated = await users.update(ta, a.id, UserUpdateData(first_name="Updated", sociacl_attributes=attributes))
        attributes["teams"].append("two")
        exported = updated.sociacl_attributes
        exported["teams"].append("three")
        assert updated.sociacl_attributes == {"teams": ["one"]}
        assert not hasattr(updated, "_sa_instance_state")
        role = await roles.get(ta, ra)
        role.permissions.append("unsafe")
        assert role.permissions == ["invite"]
        assert await users.soft_delete(ta, a.id)
        assert await users.get(ta, a.id) is None
        assert (await users.get_any(ta, a.id)).deleted_at is not None
        assert await users.update(ta, a.id, UserUpdateData(first_name="revive")) is None
        await session.commit()


@pytest.mark.asyncio
async def test_owner_mutations_leave_commit_and_rollback_to_caller(directory):
    maker, ta, _, _, _ = directory
    async with maker() as session:
        users = SqlUserRepository(session)
        created = await users.create(ta, user_body())
        async with maker() as observer:
            assert await SqlUserRepository(observer).get(ta, created.id) is None
        await session.rollback()
    async with maker() as observer:
        assert await SqlUserRepository(observer).get(ta, created.id) is None


@pytest.mark.asyncio
async def test_preferences_clear_only_requested_tenant_and_field(directory):
    maker, ta, tb, _, _ = directory
    async with maker() as session:
        users = SqlUserRepository(session)
        a = await users.create(ta, user_body())
        b = await users.create(tb, user_body())
        session.add_all([
            UserPreference(tenant_id=ta, user_id=a.id, preferences={"model_name": "old", "theme": "dark"}),
            UserPreference(tenant_id=tb, user_id=b.id, preferences={"model_name": "other"}),
        ])
        await session.commit()
        await clear_model_preference(session, tb, a.id)
        await clear_model_preference(session, ta, a.id)
        await session.rollback()
        row = (await session.execute(text("SELECT preferences FROM user_preferences WHERE user_id=:uid"),
                                     {"uid": a.id})).scalar_one()
        assert row["model_name"] == "old"
        await clear_model_preference(session, ta, a.id)
        await session.commit()
        assert (await session.execute(text("SELECT preferences FROM user_preferences WHERE user_id=:uid"),
                                      {"uid": a.id})).scalar_one() == {"theme": "dark"}
        assert (await session.execute(text("SELECT preferences FROM user_preferences WHERE user_id=:uid"),
                                      {"uid": b.id})).scalar_one() == {"model_name": "other"}


@pytest.mark.asyncio
async def test_directory_commands_respect_non_bypass_rls_role(directory):
    maker, ta, tb, ra, _ = directory
    role = "directory_owner_" + uuid.uuid4().hex
    tables = "users, roles, groups, organizations, organization_idp_links, user_group_memberships, user_preferences"
    async with maker() as session:
        await session.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS'))
        await session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await session.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO "{role}"'))
        other = await SqlUserRepository(session).create(tb, user_body())
        await session.commit()
    try:
        async with maker() as session:
            await session.execute(text(f'SET LOCAL ROLE "{role}"'))
            await set_current_tenant_id_guc(session, ta)
            users = SqlUserRepository(session)
            created = await users.create(ta, user_body())
            assert await users.get(tb, other.id) is None
            group = await SqlGroupRepository(session).create(ta, "host", None, ra)
            membership = await SqlMembershipRepository(session).add(ta, created.id, group.id)
            assert membership.user_id == created.id
            assert await get_tenant_role_group_ids(session, ta, user_id=created.id, role="host") == [group.id]
            org = await SqlOrganizationRepository(session).create(ta, "RLS Org")
            link = await SqlOrganizationIdpLinkRepository(session).create(
                tenant_id=ta, organization_id=org.id, provider="github", external_key="rls-org", linked_by_sub="owner")
            assert link.organization_id == org.id
            await users.set_role(ta, created.id, ra)
            await users.update(ta, created.id, UserUpdateData(last_name="Changed"))
            await session.rollback()
        async with maker() as session:
            assert await SqlUserRepository(session).get(ta, created.id) is None
    finally:
        async with maker() as session:
            await session.execute(text(f'REVOKE ALL ON {tables} FROM "{role}"'))
            await session.execute(text(f'REVOKE USAGE ON SCHEMA public FROM "{role}"'))
            await session.execute(text(f'DROP ROLE "{role}"'))
            await session.commit()


@pytest.mark.asyncio
async def test_corrupt_cross_tenant_join_rows_never_grant_directory_membership(directory):
    maker, ta, tb, ra, rb = directory
    async with maker() as session:
        a = await SqlUserRepository(session).create(ta, user_body())
        b = await SqlUserRepository(session).create(tb, user_body())
        ga = await SqlGroupRepository(session).create(ta, "host", None, ra)
        gb = await SqlGroupRepository(session).create(tb, "host", None, rb)
        # Historical schema permits these mismatches; owner reads must filter both sides.
        session.add_all([
            UserGroupMembership(tenant_id=ta, user_id=a.id, group_id=gb.id),
            UserGroupMembership(tenant_id=ta, user_id=b.id, group_id=ga.id),
        ])
        await session.flush()
        assert await SqlMembershipRepository(session).groups_for_user(ta, a.id) == []
        assert await SqlMembershipRepository(session).list_for_group(ta, ga.id) == []
        assert await SqlMembershipRepository(session).list_for_group(ta, gb.id) == []
        assert await SqlMembershipRepository(session).groups_for_user(ta, b.id) == []
        assert not await SqlMembershipRepository(session).exists(ta, a.id, gb.id)
        assert not await SqlMembershipRepository(session).exists(ta, b.id, ga.id)
        assert await get_tenant_role_group_ids(session, ta, user_id=a.id, role="host") == []
        await session.rollback()


@pytest.mark.asyncio
async def test_tenant_registry_pages_status_and_caller_lock(directory):
    from sqlalchemy.exc import DBAPIError
    from controlplane_tenancy.tenant_registry import tenant_active, tenant_ids_page, tenant_ids_sync
    maker, ta, tb, _, _ = directory
    async with maker() as owner, maker() as observer:
        assert await tenant_active(owner, uuid.uuid4()) is None
        assert await tenant_active(owner, ta, lock=True) is True
        await observer.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(DBAPIError):
            await observer.execute(text("UPDATE tenants SET is_active = false WHERE id = :id"), {"id": ta})
        await observer.rollback()
        await owner.rollback()
        await observer.execute(text("UPDATE tenants SET is_active = false WHERE id = :id"), {"id": ta})
        await observer.commit()
        assert await tenant_active(owner, ta) is False
        ids = await owner.run_sync(tenant_ids_sync)
        pages = []
        after = None
        while page := await tenant_ids_page(owner, after=after, limit=1):
            pages.extend(page)
            after = page[-1]
        assert pages == sorted(ids)
        assert ta in pages and tb in pages
        with pytest.raises(ValueError):
            await tenant_ids_page(owner, limit=0)
