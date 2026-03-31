from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from panopticon_tests.fixtures.postgres import (
    async_db_url,
    normalize_sync_sqlalchemy_url,
    run_alembic_upgrade,
)

try:
    from testcontainers.kafka import KafkaContainer
except ImportError:
    KafkaContainer = None  # type: ignore[misc, assignment]


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
def redis_container(require_docker: None):
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def kafka_bootstrap(require_docker: None):
    if KafkaContainer is None:
        pytest.skip("Kafka testcontainer not available")
    with KafkaContainer(image="confluentinc/cp-kafka:7.6.1") as kafka:
        yield kafka.get_bootstrap_server()


@pytest_asyncio.fixture
async def kafka_topic_created(kafka_bootstrap: str):
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic

    from controlplane_events.topics import CONTROLPLANE_EVENTS_V1

    admin = AIOKafkaAdminClient(bootstrap_servers=kafka_bootstrap)
    await admin.start()
    try:
        await admin.create_topics(
            [NewTopic(name=CONTROLPLANE_EVENTS_V1, num_partitions=1, replication_factor=1)]
        )
    finally:
        await admin.close()
    yield CONTROLPLANE_EVENTS_V1


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs Docker (testcontainers)")


@pytest.fixture
def anyio_backend():
    return "asyncio"
