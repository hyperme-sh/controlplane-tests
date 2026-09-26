"""Postgres event log: outbox atomicity, retries, claims, cursor reset."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.publisher import PostgresEventPublisher
from controlplane_events.routing_consumer import RoutingControlPlaneConsumer
from controlplane_events.store import normalize_async_url

pytestmark = pytest.mark.integration


def _count_events(sync_url: str) -> int:
    eng = create_engine(sync_url)
    with eng.connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM controlplane_events")).scalar_one())


@pytest.mark.asyncio
async def test_outbox_rollback_writes_no_event(migrated_postgres: str) -> None:
    publisher = PostgresEventPublisher(migrated_postgres, "payment-service")
    before = _count_events(migrated_postgres)
    eng = create_engine(migrated_postgres)
    with Session(eng) as session:
        publisher.publish_in_session(
            session,
            tenant_id=uuid.uuid4(),
            event_type="payment.succeeded.v1",
            payload={"billing_record_id": str(uuid.uuid4())},
        )
        session.rollback()
    assert _count_events(migrated_postgres) == before

    with Session(eng) as session:
        event_id = publisher.publish_in_session(
            session,
            tenant_id=None,
            event_type="payment.succeeded.v1",
            payload={"billing_record_id": str(uuid.uuid4())},
        )
        session.commit()
    assert event_id > 0
    assert _count_events(migrated_postgres) == before + 1


@pytest.mark.asyncio
async def test_redelivery_then_dead_and_single_claim(migrated_postgres: str) -> None:
    publisher = PostgresEventPublisher(migrated_postgres, "tests")
    from pydantic import BaseModel

    class Payload(BaseModel):
        n: int

    retry_type = f"test.retry.{uuid.uuid4().hex[:8]}.v1"
    dead_type = f"test.dead.{uuid.uuid4().hex[:8]}.v1"
    calls: list[str] = []

    async def on_retry(_env, _payload) -> None:
        calls.append("retry")
        if calls.count("retry") == 1:
            raise RuntimeError("try again")

    await publisher.start()
    try:
        await publisher.publish(tenant_id=uuid.uuid4(), event_type=retry_type, payload={"n": 1})
        await publisher.publish(tenant_id=uuid.uuid4(), event_type=dead_type, payload={"n": 2})
    finally:
        await publisher.stop()

    retry = RoutingControlPlaneConsumer(
        migrated_postgres,
        ["controlplane.events.v1"],
        f"retry-{uuid.uuid4().hex[:8]}",
        {retry_type: (Payload, on_retry)},
        max_attempts=3,
        retry_backoff_s=0,
        batch_size=500,
        worker_id="retry-worker",
    )
    await retry.poll_once()
    assert calls == ["retry"]
    await retry.poll_once()
    assert calls == ["retry", "retry"]
    await retry.stop()

    async def on_dead(_env, _payload) -> None:
        calls.append("dead")
        raise RuntimeError("always")

    dead = RoutingControlPlaneConsumer(
        migrated_postgres,
        ["controlplane.events.v1"],
        f"dead-{uuid.uuid4().hex[:8]}",
        {dead_type: (Payload, on_dead)},
        max_attempts=2,
        retry_backoff_s=0,
        batch_size=500,
        worker_id="dead-worker",
    )
    await dead.poll_once()
    await dead.poll_once()
    await dead.poll_once()
    assert calls.count("dead") == 2
    async with dead._engine.begin() as conn:  # type: ignore[union-attr]
        status = (
            await conn.execute(
                text(
                    """
                    SELECT d.status
                    FROM controlplane_event_deliveries d
                    JOIN controlplane_events e ON e.id = d.event_id
                    WHERE d.consumer_group = :group AND e.event_type = :event_type
                    """
                ),
                {"group": dead._group_id, "event_type": dead_type},
            )
        ).scalar_one()
    assert status == "dead"
    await dead.stop()


@pytest.mark.asyncio
async def test_two_workers_do_not_double_claim(migrated_postgres: str) -> None:
    publisher = PostgresEventPublisher(migrated_postgres, "tests")
    group = f"claim-{uuid.uuid4().hex[:8]}"
    eng = create_engine(migrated_postgres)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                SELECT register_controlplane_event_consumer(
                    :group, string_to_array(:types, ','), 'latest', 5
                )
                """
            ),
            {"group": group, "types": "test.claim.v1"},
        )
    await publisher.start()
    try:
        for i in range(4):
            await publisher.publish(
                tenant_id=None,
                event_type="test.claim.v1",
                payload={"i": i},
            )
    finally:
        await publisher.stop()

    barrier = threading.Barrier(2)
    found: dict[str, list[int]] = {}

    def _claim(worker: str) -> None:
        local = create_engine(migrated_postgres)
        with local.connect() as conn:
            trans = conn.begin()
            barrier.wait(timeout=10)
            rows = conn.execute(
                text(
                    """
                    SELECT event_id
                    FROM claim_controlplane_event_deliveries(:group, :worker, 4, 30)
                    """
                ),
                {"group": group, "worker": worker},
            ).fetchall()
            found[worker] = [int(row[0]) for row in rows]
            barrier.wait(timeout=10)
            trans.commit()
        local.dispose()

    threads = [
        threading.Thread(target=_claim, args=(f"w{i}",))
        for i in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert set(found) == {"w1", "w2"}
    left = set(found["w1"])
    right = set(found["w2"])
    assert left.isdisjoint(right)
    assert len(left) + len(right) == 4


@pytest.mark.asyncio
async def test_cursor_reset_replays(migrated_postgres: str) -> None:
    from pydantic import BaseModel

    class Payload(BaseModel):
        n: int

    publisher = PostgresEventPublisher(migrated_postgres, "tests")
    await publisher.start()
    group = f"replay-{uuid.uuid4().hex[:8]}"
    event_type = f"test.replay.{uuid.uuid4().hex[:8]}.v1"
    try:
        await publisher.publish(tenant_id=None, event_type=event_type, payload={"n": 1})
    finally:
        await publisher.stop()

    seen: list[int] = []

    async def on_event(_env, payload: Payload) -> None:
        seen.append(payload.n)

    consumer = RoutingControlPlaneConsumer(
        migrated_postgres,
        ["controlplane.events.v1"],
        group,
        {event_type: (Payload, on_event)},
        batch_size=500,
        worker_id="replay",
    )
    await consumer.poll_once()
    assert seen == [1]
    await consumer.reset_cursor(0)
    await consumer.poll_once()
    assert seen == [1, 1]
    await consumer.stop()


@pytest.mark.asyncio
async def test_bus_policy_ignores_tenant_guc(migrated_postgres: str) -> None:
    role = "panopticon_events_tester"
    eng = create_engine(migrated_postgres)
    other = uuid.uuid4()
    event_tenant = uuid.uuid4()
    with eng.begin() as conn:
        conn.execute(
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
        conn.execute(
            text(
                f"GRANT SELECT, INSERT ON controlplane_events TO {role}"
            )
        )
        conn.execute(text(f"GRANT USAGE, SELECT ON SEQUENCE controlplane_events_id_seq TO {role}"))
        conn.execute(
            text(
                "GRANT EXECUTE ON FUNCTION notify_controlplane_event(), "
                f"deliver_controlplane_event() TO {role}"
            )
        )
        conn.execute(text(f"GRANT {role} TO CURRENT_USER"))
        conn.execute(text(f"SET ROLE {role}"))
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(other)},
        )
        event_id = conn.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope)
                VALUES ('test.rls.v1', :tenant, CAST(:envelope AS jsonb))
                RETURNING id
                """
            ),
            {
                "tenant": event_tenant,
                "envelope": '{"event_type":"test.rls.v1","service":"tests","payload":{}}',
            },
        ).scalar_one()
        seen = conn.execute(
            text("SELECT tenant_id FROM controlplane_events WHERE id = :id"),
            {"id": event_id},
        ).scalar_one()
        conn.execute(text("RESET ROLE"))
    assert seen == event_tenant


@pytest.mark.asyncio
async def test_async_publish_is_visible(db_async_url: str) -> None:
    publisher = PostgresEventPublisher(db_async_url, "tests")
    await publisher.publish(
        tenant_id=uuid.uuid4(),
        event_type="test.async.v1",
        payload={"ok": True},
    )
    await publisher.stop()
    engine = create_async_engine(normalize_async_url(db_async_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM controlplane_events WHERE event_type = 'test.async.v1'"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert int(count) >= 1


def test_prune_function_removes_old_terminal_events(migrated_postgres: str) -> None:
    eng = create_engine(migrated_postgres)
    with eng.begin() as conn:
        event_id = conn.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope, created_at)
                VALUES (
                    'test.prune.v1', NULL, '{"event_type":"test.prune.v1"}'::jsonb,
                    now() - interval '40 days'
                )
                RETURNING id
                """
            )
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO controlplane_event_deliveries (
                    event_id, consumer_group, status, attempts, max_attempts
                )
                VALUES (:id, 'prune-test', 'delivered', 1, 5)
                ON CONFLICT DO NOTHING
                """
            ),
            {"id": event_id},
        )
        removed = conn.execute(
            text("SELECT prune_controlplane_events(interval '30 days')")
        ).scalar_one()
        left = conn.execute(
            text("SELECT count(*) FROM controlplane_events WHERE id = :id"),
            {"id": event_id},
        ).scalar_one()
    assert int(removed) >= 1
    assert int(left) == 0


def test_uncommitted_insert_does_not_skip_an_earlier_id(migrated_postgres: str) -> None:
    """tx A holds id 1, tx B commits id 2, then A commits. Both get a delivery row."""

    group = f"gap-{uuid.uuid4().hex[:8]}"
    event_type = f"test.gap.{uuid.uuid4().hex[:8]}.v1"
    eng = create_engine(migrated_postgres)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                SELECT register_controlplane_event_consumer(
                    :group, string_to_array(:types, ','), 'latest', 5
                )
                """
            ),
            {"group": group, "types": event_type},
        )

    hold = eng.connect()
    later = eng.connect()
    hold_tx = hold.begin()
    try:
        first_id = hold.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope)
                VALUES (:event_type, NULL, CAST(:envelope AS jsonb))
                RETURNING id
                """
            ),
            {
                "event_type": event_type,
                "envelope": f'{{"event_type":"{event_type}","service":"tests","payload":{{"n":1}}}}',
            },
        ).scalar_one()
        later_tx = later.begin()
        second_id = later.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope)
                VALUES (:event_type, NULL, CAST(:envelope AS jsonb))
                RETURNING id
                """
            ),
            {
                "event_type": event_type,
                "envelope": f'{{"event_type":"{event_type}","service":"tests","payload":{{"n":2}}}}',
            },
        ).scalar_one()
        later_tx.commit()
        hold_tx.commit()
    finally:
        hold.close()
        later.close()

    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT event_id, status
                FROM controlplane_event_deliveries
                WHERE consumer_group = :group
                  AND event_id IN (:first_id, :second_id)
                ORDER BY event_id
                """
            ),
            {"group": group, "first_id": first_id, "second_id": second_id},
        ).fetchall()
    assert [int(row[0]) for row in rows] == [int(first_id), int(second_id)]
    assert {row[1] for row in rows} == {"pending"}


def test_expired_lease_at_max_attempts_is_dead(migrated_postgres: str) -> None:
    group = f"lease-{uuid.uuid4().hex[:8]}"
    event_type = f"test.lease.{uuid.uuid4().hex[:8]}.v1"
    eng = create_engine(migrated_postgres)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                SELECT register_controlplane_event_consumer(
                    :group, string_to_array(:types, ','), 'latest', 2
                )
                """
            ),
            {"group": group, "types": event_type},
        )
        conn.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope)
                VALUES (:event_type, NULL, CAST(:envelope AS jsonb))
                """
            ),
            {
                "event_type": event_type,
                "envelope": f'{{"event_type":"{event_type}","service":"tests","payload":{{}}}}',
            },
        )

        def _expire_and_claim(worker: str) -> list:
            conn.execute(
                text(
                    """
                    UPDATE controlplane_event_deliveries
                    SET lease_expires_at = now() - interval '1 second'
                    WHERE consumer_group = :group AND status = 'claimed'
                    """
                ),
                {"group": group},
            )
            return conn.execute(
                text(
                    """
                    SELECT event_id, attempts
                    FROM claim_controlplane_event_deliveries(:group, :worker, 1, 30)
                    """
                ),
                {"group": group, "worker": worker},
            ).fetchall()

        first = conn.execute(
            text(
                """
                SELECT event_id, attempts
                FROM claim_controlplane_event_deliveries(:group, 'worker-a', 1, 30)
                """
            ),
            {"group": group},
        ).fetchall()
        assert len(first) == 1
        assert int(first[0][1]) == 1
        second = _expire_and_claim("worker-b")
        assert len(second) == 1
        assert int(second[0][1]) == 2
        third = _expire_and_claim("worker-c")
        assert third == []
        status = conn.execute(
            text(
                """
                SELECT status FROM controlplane_event_deliveries
                WHERE consumer_group = :group
                """
            ),
            {"group": group},
        ).scalar_one()
    assert status == "dead"
