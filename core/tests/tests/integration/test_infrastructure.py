from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_postgres_migrated(async_session):
    res = await async_session.execute(text("SELECT 1"))
    assert res.scalar_one() == 1


@pytest.mark.asyncio
async def test_rls_session_sets_guc(rls_session):
    res = await rls_session.execute(
        text("SELECT current_setting('app.current_tenant_id', true)")
    )
    val = res.scalar_one()
    assert val
    uuid.UUID(val)


@pytest.mark.asyncio
async def test_redis_ping(redis_url: str):
    client = aioredis.from_url(redis_url)
    try:
        assert await client.ping()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_kafka_topic_create(kafka_bootstrap: str, kafka_topic_created: str):
    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    try:
        await producer.send_and_wait(
            kafka_topic_created,
            value=b'{"hello":"world"}',
        )
    finally:
        await producer.stop()


@pytest.mark.asyncio
async def test_respx_mock_httpx(respx_mock):
    import httpx

    respx_mock.get("https://example.test/health").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with httpx.AsyncClient() as client:
        r = await client.get("https://example.test/health")
    assert r.json() == {"ok": True}
