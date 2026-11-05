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
async def test_events_table_round_trip(async_session):
    from controlplane_events.envelope import ControlPlaneEventEnvelope
    from controlplane_events.store import insert_event

    envelope = ControlPlaneEventEnvelope(
        event_type="test.ping.v1",
        service="spanopticon-tests",
        payload={"hello": "world"},
    )
    event_id = await insert_event(async_session, envelope)
    await async_session.commit()
    row = (
        await async_session.execute(
            text("SELECT event_type, envelope->>'service' FROM controlplane_events WHERE id = :id"),
            {"id": event_id},
        )
    ).one()
    assert row[0] == "test.ping.v1"
    assert row[1] == "spanopticon-tests"


@pytest.mark.asyncio
async def test_respx_mock_httpx(respx_mock):
    import httpx

    respx_mock.get("https://example.test/health").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with httpx.AsyncClient() as client:
        r = await client.get("https://example.test/health")
    assert r.json() == {"ok": True}
