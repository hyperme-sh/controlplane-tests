from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from panopticon_tests.fixtures.postgres import (
    MigrationTemplates,
    async_db_url,
    normalize_sync_sqlalchemy_url,
    run_alembic_upgrade,
)


@pytest.fixture(scope="session")
def require_docker() -> None:
    if os.environ.get("SKIP_TESTCONTAINERS", "").lower() in ("1", "true", "yes"):
        pytest.skip("SKIP_TESTCONTAINERS is set")
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker not available for testcontainers: {exc}")


@pytest.fixture(scope="session")
def postgres_container(require_docker: None):
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_sync_url(postgres_container: PostgresContainer) -> str:
    return normalize_sync_sqlalchemy_url(postgres_container.get_connection_url())


@pytest.fixture(scope="session")
def migrated_postgres(postgres_sync_url: str) -> str:
    run_alembic_upgrade(postgres_sync_url)
    return postgres_sync_url


@pytest.fixture(scope="session")
def rls_isolation_role_ready(migrated_postgres: str) -> None:
    """Create a role without BYPASSRLS so RLS is enforced (superuser bypasses RLS).

    Tests that assert tenant isolation ``SET ROLE`` to this role after granting
    ``SELECT`` on the table under test.
    """
    role = "panopticon_rls_tester"
    eng = create_engine(migrated_postgres)
    with eng.begin() as cx:
        cx.execute(
            text(
                f"""
                DO $$ BEGIN
                    CREATE ROLE {role} NOINHERIT NOBYPASSRLS;
                EXCEPTION WHEN duplicate_object THEN
                    NULL;
                END $$;
                """
            )
        )
        cx.execute(text(f"GRANT SELECT ON billing_records TO {role}"))
        cx.execute(text(f"GRANT {role} TO CURRENT_USER"))


@pytest.fixture(scope="session")
def db_async_url(migrated_postgres: str) -> str:
    return async_db_url(migrated_postgres)


@pytest_asyncio.fixture
async def async_engine(db_async_url: str):
    # Function-scoped: asyncpg connections bind to the loop that opens them; a
    # session-scoped engine breaks later tests when pytest-asyncio uses a new loop per test.
    engine = create_async_engine(db_async_url, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def rls_session(async_engine) -> AsyncSession:
    tenant_id = uuid.uuid4()
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        yield session


@pytest.fixture(scope="session")
def migration_templates(require_docker: None):
    """One role-free PostgreSQL server per pytest process for migration tests.

    Kept separate from ``postgres_container`` so nothing else can add the
    optional ``controlplane``/``controlplane_app`` roles that change what some
    migrations grant. Under xdist each worker starts its own server, and the
    database names carry the worker id as well.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    with PostgresContainer("postgres:16-alpine") as pg:
        templates = MigrationTemplates(pg.get_connection_url(), prefix=f"mt_{worker}")
        engine = create_engine(normalize_sync_sqlalchemy_url(pg.get_connection_url()))
        with engine.connect() as cx:
            app_roles = cx.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname IN ('controlplane', 'controlplane_app')")
            ).scalars().all()
        engine.dispose()
        assert not app_roles, f"migration server must start without app roles, found {app_roles}"
        yield templates
        templates.dispose()


@pytest.fixture
def migration_database(migration_templates: MigrationTemplates):
    """Factory for per-test databases on the shared migration server.

    ``migration_database()`` is an empty database (``template0``),
    ``migration_database("0043_device_host_type")`` is a copy of a template
    upgraded from empty to that revision, and ``template=`` copies a named
    template such as one from ``migration_templates.derived_template``.
    Every database is dropped after the test.
    """
    created: list[str] = []

    def make(revision: str | None = None, *, template: str | None = None) -> str:
        if template is None:
            template = migration_templates.template_at(revision) if revision else "template0"
        name, url = migration_templates.database(template)
        created.append(name)
        return url

    yield make
    for name in created:
        migration_templates.drop(name)


@pytest.fixture
def migration_db(request: pytest.FixtureRequest, migration_database) -> str:
    """Per-test database already at ``@pytest.mark.migration_start(revision)``, or empty without it.

    Tests still run their own first ``upgrade(revision)``; on the copy it is a
    no-op, so the test observes the same database an upgrade from empty gives.
    """
    marker = request.node.get_closest_marker("migration_start")
    return migration_database(marker.args[0] if marker else None)


@pytest.fixture(scope="session")
def redis_container(require_docker: None):
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs Docker (testcontainers)")
    config.addinivalue_line(
        "markers",
        "migration_start(revision): migration_db starts as a copy of a template at this Alembic revision",
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"
