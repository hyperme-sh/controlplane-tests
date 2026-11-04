"""0042 dedupes role names inside one tenant scope and adds partial unique indexes.

NULL tenant is its own scope. A tenant-scoped "user" row is not the platform
"user" row. Same-scope duplicates keep the oldest created_at, then id.
Foreign keys are repointed. Downgrade drops only the new indexes.
"""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from spanopticon_tests.fixtures.postgres import alembic_ini_path

pytestmark = pytest.mark.integration

_PARENT = "0041_fleet_health"
_REV = "0042_roles_unique_per_tenant"
_PLATFORM_INDEX = "uq_roles_platform_lower_name"
_TENANT_INDEX = "uq_roles_tenant_lower_name"
_MARKETPLACE = "8f8e8d8c-7b6a-4a59-8c3d-2e1f0a9b8c7d"
# Pan-test rows that must survive: different scopes, same name.
_PLATFORM_USER = "8355ff84-db0c-4c8b-9b30-8b485cc14a23"
_TENANT_USER = "fa273bd4-c6c6-4fa5-a232-907eaa09605e"


def _upgrade(url: str, revision: str = "head") -> None:
    import os

    os.environ["DATABASE_URL"] = url
    command.upgrade(Config(str(alembic_ini_path())), revision)


def _downgrade(url: str, revision: str) -> None:
    import os

    os.environ["DATABASE_URL"] = url
    command.downgrade(Config(str(alembic_ini_path())), revision)


def _stamp(url: str, revision: str) -> None:
    import os

    os.environ["DATABASE_URL"] = url
    command.stamp(Config(str(alembic_ini_path())), revision)


@pytest.fixture
def postgres(migration_db: str) -> str:
    """Own database per test, copied from a template at the test's first revision."""
    return migration_db


def _indexes(url: str) -> dict[str, str]:
    eng = create_engine(url)
    with eng.begin() as cx:
        rows = cx.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'roles'
                """
            )
        ).all()
    return {name: definition for name, definition in rows}


def _role_ids(url: str) -> set[str]:
    eng = create_engine(url)
    with eng.begin() as cx:
        rows = cx.execute(text("SELECT id::text FROM roles")).all()
    return {row[0] for row in rows}


def _rls(url: str, table: str) -> tuple[bool, bool]:
    eng = create_engine(url)
    with eng.begin() as cx:
        row = cx.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = :table
                """
            ),
            {"table": table},
        ).one()
    return bool(row[0]), bool(row[1])


@pytest.mark.migration_start(_PARENT)
def test_clean_database_only_adds_partial_unique_indexes(postgres: str) -> None:
    _upgrade(postgres, _PARENT)
    before = _indexes(postgres)
    assert _role_ids(postgres) == set()
    assert _PLATFORM_INDEX not in before
    assert _TENANT_INDEX not in before

    _upgrade(postgres, _REV)

    after = _indexes(postgres)
    assert _role_ids(postgres) == set()
    assert set(after) - set(before) == {_PLATFORM_INDEX, _TENANT_INDEX}
    assert "WHERE" in after[_PLATFORM_INDEX].upper()
    assert "tenant_id IS NULL" in after[_PLATFORM_INDEX]
    assert "lower" in after[_PLATFORM_INDEX].lower()
    assert "WHERE" in after[_TENANT_INDEX].upper()
    assert "tenant_id IS NOT NULL" in after[_TENANT_INDEX]
    assert "lower" in after[_TENANT_INDEX].lower()
    for definition in after.values():
        compact = " ".join(definition.lower().split())
        assert compact != "create unique index on roles (name)"
        assert "unique" not in compact or "where" in compact or "(id)" in compact

    assert _rls(postgres, "roles") == (True, True)
    assert _rls(postgres, "users") == (True, True)
    assert _rls(postgres, "groups") == (True, True)

    _downgrade(postgres, _PARENT)
    downgraded = _indexes(postgres)
    assert _PLATFORM_INDEX not in downgraded
    assert _TENANT_INDEX not in downgraded
    assert _role_ids(postgres) == set()


@pytest.mark.migration_start(_PARENT)
def test_cross_scope_user_roles_are_unchanged(postgres: str) -> None:
    """The pan-test pair is two scopes. 0042 must not merge or delete either."""

    _upgrade(postgres, _PARENT)
    group_id = uuid.uuid4()
    eng = create_engine(postgres)
    with eng.begin() as cx:
        cx.execute(
            text(
                """
                INSERT INTO roles (id, tenant_id, name, permissions, created_at, updated_at)
                VALUES (
                    CAST(:id AS uuid), NULL, 'user', '[]'::jsonb,
                    '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z'
                )
                """
            ),
            {"id": _PLATFORM_USER},
        )
        cx.execute(
            text(
                """
                INSERT INTO roles (id, tenant_id, name, permissions, created_at, updated_at)
                VALUES (
                    CAST(:id AS uuid), CAST(:tid AS uuid), 'user', '[]'::jsonb,
                    '2026-09-25T14:06:08Z', '2026-09-25T14:06:08Z'
                )
                """
            ),
            {"id": _TENANT_USER, "tid": _MARKETPLACE},
        )
        cx.execute(
            text(
                """
                INSERT INTO groups (id, tenant_id, name, role_id)
                VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), 'renter', CAST(:role AS uuid))
                """
            ),
            {"id": group_id, "tid": _MARKETPLACE, "role": _TENANT_USER},
        )

    _upgrade(postgres, _REV)

    with eng.begin() as cx:
        roles = cx.execute(
            text(
                """
                SELECT id::text, tenant_id::text
                FROM roles
                WHERE lower(name) = 'user'
                ORDER BY id::text
                """
            )
        ).all()
        pointed = cx.execute(
            text("SELECT role_id::text FROM groups WHERE id = CAST(:id AS uuid)"),
            {"id": group_id},
        ).scalar_one()
    assert {(row[0], row[1]) for row in roles} == {
        (_PLATFORM_USER, None),
        (_TENANT_USER, _MARKETPLACE),
    }
    assert pointed == _TENANT_USER
    assert _PLATFORM_INDEX in _indexes(postgres)
    assert _TENANT_INDEX in _indexes(postgres)


@pytest.mark.migration_start(_PARENT)
def test_same_scope_duplicates_repoint_fks_and_rerun_is_idempotent(postgres: str) -> None:
    _upgrade(postgres, _PARENT)
    other_tenant = uuid.uuid4()
    keeper = uuid.uuid4()
    duplicate = uuid.uuid4()
    other_scope = uuid.uuid4()
    platform_keeper = uuid.UUID("00000000-0000-4000-8000-000000000010")
    platform_dup = uuid.UUID("ffffffff-ffff-4000-8000-000000000010")
    guest_keeper = uuid.UUID("00000000-0000-4000-8000-000000000020")
    guest_dup = uuid.UUID("ffffffff-ffff-4000-8000-000000000020")
    user_on_dup = uuid.uuid4()
    user_on_keep = uuid.uuid4()
    platform_user = uuid.uuid4()
    group_id = uuid.uuid4()
    subject_both = uuid.uuid4()
    subject_dup_only = uuid.uuid4()
    link_keep = uuid.uuid4()
    link_dup = uuid.uuid4()
    link_only = uuid.uuid4()

    eng = create_engine(postgres)
    with eng.begin() as cx:
        cx.execute(
            text(
                "INSERT INTO tenants (id, name, is_active) VALUES (CAST(:id AS uuid), :name, true)"
            ),
            {"id": other_tenant, "name": "other-scope"},
        )
        cx.execute(
            text(
                """
                INSERT INTO roles (id, tenant_id, name, permissions, created_at, updated_at)
                VALUES
                  (CAST(:keeper AS uuid), CAST(:tid AS uuid), 'user', '["keep"]'::jsonb,
                   '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z'),
                  (CAST(:dup AS uuid), CAST(:tid AS uuid), 'User', '["drop"]'::jsonb,
                   '2026-09-25T14:06:08Z', '2026-09-25T14:06:08Z'),
                  (CAST(:other AS uuid), CAST(:other_tid AS uuid), 'user', '[]'::jsonb,
                   '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z'),
                  (CAST(:pk AS uuid), NULL, 'admin', '["platform"]'::jsonb,
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
                  (CAST(:pd AS uuid), NULL, 'Admin', '["platform-dup"]'::jsonb,
                   '2026-02-01T00:00:00Z', '2026-02-01T00:00:00Z'),
                  (CAST(:gk AS uuid), NULL, 'guest', '[]'::jsonb,
                   '2026-03-01T00:00:00Z', '2026-03-01T00:00:00Z'),
                  (CAST(:gd AS uuid), NULL, 'Guest', '[]'::jsonb,
                   '2026-03-01T00:00:00Z', '2026-03-01T00:00:00Z')
                """
            ),
            {
                "keeper": keeper,
                "dup": duplicate,
                "tid": _MARKETPLACE,
                "other": other_scope,
                "other_tid": other_tenant,
                "pk": platform_keeper,
                "pd": platform_dup,
                "gk": guest_keeper,
                "gd": guest_dup,
            },
        )
        cx.execute(
            text(
                """
                INSERT INTO users (
                    id, tenant_id, first_name, last_name, email, gender, is_active, role_id
                ) VALUES
                  (CAST(:on_dup AS uuid), CAST(:tid AS uuid), 'A', 'Dup', 'dup@example.com',
                   'unspecified', true, CAST(:dup AS uuid)),
                  (CAST(:on_keep AS uuid), CAST(:tid AS uuid), 'B', 'Keep', 'keep@example.com',
                   'unspecified', true, CAST(:keeper AS uuid)),
                  (CAST(:platform AS uuid), NULL, 'P', 'Admin', 'platform@example.com',
                   'unspecified', true, CAST(:pd AS uuid))
                """
            ),
            {
                "on_dup": user_on_dup,
                "on_keep": user_on_keep,
                "platform": platform_user,
                "tid": _MARKETPLACE,
                "dup": duplicate,
                "keeper": keeper,
                "pd": platform_dup,
            },
        )
        cx.execute(
            text(
                """
                INSERT INTO groups (id, tenant_id, name, role_id)
                VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), 'renter', CAST(:role AS uuid))
                """
            ),
            {"id": group_id, "tid": _MARKETPLACE, "role": duplicate},
        )
        cx.execute(
            text(
                """
                CREATE TABLE role_assignees (
                    id uuid PRIMARY KEY,
                    subject_id uuid NOT NULL,
                    role_id uuid NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
                    UNIQUE (subject_id, role_id)
                )
                """
            )
        )
        cx.execute(
            text(
                """
                CREATE UNIQUE INDEX uq_role_assignees_live
                ON role_assignees (subject_id, role_id)
                WHERE subject_id IS NOT NULL
                """
            )
        )
        cx.execute(
            text(
                """
                INSERT INTO role_assignees (id, subject_id, role_id) VALUES
                  (CAST(:keep AS uuid), CAST(:both AS uuid), CAST(:keeper AS uuid)),
                  (CAST(:dup AS uuid), CAST(:both AS uuid), CAST(:duplicate AS uuid)),
                  (CAST(:only AS uuid), CAST(:only_subject AS uuid), CAST(:duplicate AS uuid))
                """
            ),
            {
                "keep": link_keep,
                "dup": link_dup,
                "only": link_only,
                "both": subject_both,
                "only_subject": subject_dup_only,
                "keeper": keeper,
                "duplicate": duplicate,
            },
        )

    _upgrade(postgres, _REV)

    with eng.begin() as cx:
        remaining = {
            row[0]
            for row in cx.execute(text("SELECT id::text FROM roles")).all()
        }
        user_roles = dict(
            cx.execute(
                text("SELECT id::text, role_id::text FROM users")
            ).all()
        )
        group_role = cx.execute(
            text("SELECT role_id::text FROM groups WHERE id = CAST(:id AS uuid)"),
            {"id": group_id},
        ).scalar_one()
        links = cx.execute(
            text(
                "SELECT id::text, subject_id::text, role_id::text FROM role_assignees ORDER BY id::text"
            )
        ).all()
        perms = cx.execute(
            text("SELECT permissions::text FROM roles WHERE id = CAST(:id AS uuid)"),
            {"id": keeper},
        ).scalar_one()

    assert str(duplicate) not in remaining
    assert str(platform_dup) not in remaining
    assert str(guest_dup) not in remaining
    assert str(keeper) in remaining
    assert str(other_scope) in remaining
    assert str(platform_keeper) in remaining
    assert str(guest_keeper) in remaining
    assert user_roles[str(user_on_dup)] == str(keeper)
    assert user_roles[str(user_on_keep)] == str(keeper)
    assert user_roles[str(platform_user)] == str(platform_keeper)
    assert group_role == str(keeper)
    assert perms == '["keep"]'
    by_subject = {}
    for link_id, subject_id, role_id in links:
        by_subject.setdefault(subject_id, []).append((link_id, role_id))
    assert by_subject[str(subject_both)] == [(str(link_keep), str(keeper))]
    assert by_subject[str(subject_dup_only)] == [(str(link_only), str(keeper))]

    ids_after = _role_ids(postgres)
    _stamp(postgres, _PARENT)
    _upgrade(postgres, _REV)
    assert _role_ids(postgres) == ids_after
    assert _PLATFORM_INDEX in _indexes(postgres)
    assert _TENANT_INDEX in _indexes(postgres)

    _downgrade(postgres, _PARENT)
    downgraded = _indexes(postgres)
    assert _PLATFORM_INDEX not in downgraded
    assert _TENANT_INDEX not in downgraded
    assert _role_ids(postgres) == ids_after
    assert _rls(postgres, "roles") == (True, True)

    _upgrade(postgres, _REV)
    assert _indexes(postgres).keys() >= {_PLATFORM_INDEX, _TENANT_INDEX}
    assert _role_ids(postgres) == ids_after
