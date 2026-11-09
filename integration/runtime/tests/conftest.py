from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from controlplane_db.migrations import upgrade

def normalize_sync_sqlalchemy_url(url):
    return url.replace("+asyncpg", "+psycopg").replace("+psycopg2", "+psycopg")

def async_db_url(url):
    return url.replace("+psycopg", "+asyncpg")

def run_alembic_upgrade(url):
    upgrade(url)



@pytest.fixture(scope="session")
def require_docker() -> None:
    if os.environ.get("SKIP_TESTCONTAINERS", "").lower() in ("1", "true", "yes"):
        pytest.fail("SKIP_TESTCONTAINERS is set")
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Docker not available for testcontainers: {exc}")


@pytest.fixture(scope="session")
def postgres_container(require_docker: None):
    with PostgresContainer("postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416", driver="psycopg") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_sync_url(postgres_container: PostgresContainer) -> str:
    return normalize_sync_sqlalchemy_url(postgres_container.get_connection_url())


@pytest.fixture(scope="session")
def migrated_postgres(postgres_sync_url: str) -> str:
    run_alembic_upgrade(postgres_sync_url)
    return postgres_sync_url


@pytest.fixture(scope="session")
def db_async_url(migrated_postgres: str) -> str:
    return async_db_url(migrated_postgres)


@pytest_asyncio.fixture
async def async_engine(db_async_url: str):
    engine = create_async_engine(db_async_url, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs Docker (testcontainers)")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def validate_published_wire_contracts(monkeypatch):
    """Validate real Postgres publisher/outbox envelopes before insertion in e2e tests."""
    from controlplane_events import store
    from controlplane_events.contracts import validate_event_envelope
    original = store.envelope_params
    def checked(envelope):
        validate_event_envelope(envelope)
        return original(envelope)
    monkeypatch.setattr(store, 'envelope_params', checked)


@pytest_asyncio.fixture
async def rls_session(async_engine):
    import uuid
    from sqlalchemy import text
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(uuid.uuid4())},
        )
        yield session


@pytest.fixture(scope="session")
def redis_url(require_docker):
    from testcontainers.community.redis import RedisContainer
    with RedisContainer("redis:7-alpine@sha256:8b81dd37ff027bec4e516d41acfbe9fe2460070dc6d4a4570a2ac5b9d59df065") as container:
        yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
