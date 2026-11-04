"""Postgres event log: outbox atomicity, retries, claims, cursor reset."""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.publisher import PostgresEventPublisher
from controlplane_events.routing_consumer import RoutingControlPlaneConsumer
from controlplane_events.store import delivery_counts_sync, normalize_async_url

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


def test_prune_removes_old_events_with_no_deliveries(migrated_postgres: str) -> None:
    eng = create_engine(migrated_postgres)
    event_type = f"test.unsubscribed.{uuid.uuid4().hex[:8]}.v1"
    with eng.begin() as conn:
        old_id = conn.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope, created_at)
                VALUES (
                    :event_type, NULL, CAST(:envelope AS jsonb),
                    now() - interval '40 days'
                )
                RETURNING id
                """
            ),
            {
                "event_type": event_type,
                "envelope": f'{{"event_type":"{event_type}"}}',
            },
        ).scalar_one()
        held_id = conn.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope, created_at)
                VALUES (
                    :event_type, NULL, CAST(:envelope AS jsonb),
                    now() - interval '40 days'
                )
                RETURNING id
                """
            ),
            {
                "event_type": event_type,
                "envelope": f'{{"event_type":"{event_type}","n":2}}',
            },
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO controlplane_event_deliveries (
                    event_id, consumer_group, status, attempts, max_attempts
                )
                VALUES (:id, :group, 'pending', 0, 5)
                """
            ),
            {"id": held_id, "group": f"hold-{uuid.uuid4().hex[:8]}"},
        )
        fresh_id = conn.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope)
                VALUES (:event_type, NULL, CAST(:envelope AS jsonb))
                RETURNING id
                """
            ),
            {
                "event_type": event_type,
                "envelope": f'{{"event_type":"{event_type}","n":3}}',
            },
        ).scalar_one()
        conn.execute(text("SELECT prune_controlplane_events(interval '30 days')"))
        left = {
            int(row[0])
            for row in conn.execute(
                text(
                    """
                    SELECT id FROM controlplane_events
                    WHERE id IN (:old_id, :held_id, :fresh_id)
                    """
                ),
                {"old_id": old_id, "held_id": held_id, "fresh_id": fresh_id},
            ).fetchall()
        }
    assert int(old_id) not in left
    assert int(held_id) in left
    assert int(fresh_id) in left


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


def _share_waiter_queued(eng) -> bool:
    """True when some session is waiting for SHARE on controlplane_events.

    The open insert holds ROW EXCLUSIVE, which does not conflict with SHARE
    UPDATE EXCLUSIVE. A waiting SHARE does, because Postgres queues a new
    request behind any conflicting waiter. A lock timeout on this probe means
    registration is blocked on the insert.
    """

    probe = eng.connect()
    try:
        tx = probe.begin()
        try:
            probe.execute(text("SET LOCAL lock_timeout = '250ms'"))
            probe.execute(
                text("LOCK TABLE controlplane_events IN SHARE UPDATE EXCLUSIVE MODE")
            )
        except Exception as exc:
            tx.rollback()
            return "lock timeout" in str(exc).lower()
        tx.rollback()
        return False
    finally:
        probe.close()


def test_register_during_open_insert_still_delivers(migrated_postgres: str) -> None:
    """A new group registered while an insert is open still gets that event."""

    group = f"reg-{uuid.uuid4().hex[:8]}"
    event_type = f"test.reg.{uuid.uuid4().hex[:8]}.v1"
    eng = create_engine(migrated_postgres)
    hold = eng.connect()
    hold_tx = hold.begin()
    errors: list[BaseException] = []
    thread: threading.Thread | None = None
    try:
        event_id = hold.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope)
                VALUES (:event_type, NULL, CAST(:envelope AS jsonb))
                RETURNING id
                """
            ),
            {
                "event_type": event_type,
                "envelope": (
                    f'{{"event_type":"{event_type}","service":"tests","payload":{{}}}}'
                ),
            },
        ).scalar_one()

        def _register() -> None:
            local = create_engine(migrated_postgres)
            try:
                with local.begin() as conn:
                    conn.execute(
                        text(
                            """
                            SELECT register_controlplane_event_consumer(
                                :group, string_to_array(:types, ','), 'earliest', 5
                            )
                            """
                        ),
                        {"group": group, "types": event_type},
                    )
            except BaseException as exc:
                errors.append(exc)
            finally:
                local.dispose()

        thread = threading.Thread(target=_register)
        thread.start()
        deadline = time.monotonic() + 10
        queued = False
        while time.monotonic() < deadline:
            if errors:
                break
            if _share_waiter_queued(eng):
                queued = True
                break
            time.sleep(0.02)
        assert queued, f"register did not block on the open insert: errors={errors!r}"
        hold_tx.commit()
        thread.join(timeout=20)
    finally:
        if hold_tx.is_active:
            hold_tx.rollback()
        hold.close()
        if thread is not None:
            thread.join(timeout=20)

    assert errors == []
    assert thread is not None and not thread.is_alive()
    with eng.connect() as conn:
        status = conn.execute(
            text(
                """
                SELECT status FROM controlplane_event_deliveries
                WHERE consumer_group = :group AND event_id = :event_id
                """
            ),
            {"group": group, "event_id": event_id},
        ).scalar_one()
    assert status == "pending"


_REGISTER_GRANTS = """
GRANT SELECT, INSERT ON controlplane_events TO {role};
GRANT USAGE, SELECT ON SEQUENCE controlplane_events_id_seq TO {role};
GRANT SELECT, INSERT, UPDATE, DELETE ON controlplane_event_subscriptions TO {role};
GRANT SELECT, INSERT, UPDATE, DELETE ON controlplane_event_deliveries TO {role};
GRANT EXECUTE ON FUNCTION
    notify_controlplane_event(),
    deliver_controlplane_event(),
    register_controlplane_event_consumer(text, text[], text, integer),
    claim_controlplane_event_deliveries(text, text, integer, integer, bigint[]),
    replay_controlplane_event_deliveries(text, bigint),
    prune_controlplane_events(interval)
TO {role};
"""


def test_nobypassrls_role_can_register_with_event_grants_only(migrated_postgres: str) -> None:
    """SHARE is taken inside the definer function. The app role has no UPDATE."""

    role = "panopticon_events_register"
    group = f"reg-role-{uuid.uuid4().hex[:8]}"
    event_type = f"test.regrole.{uuid.uuid4().hex[:8]}.v1"
    eng = create_engine(migrated_postgres)
    try:
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
            conn.execute(text(_REGISTER_GRANTS.format(role=role)))
            conn.execute(text(f"GRANT {role} TO CURRENT_USER"))
            event_id = conn.execute(
                text(
                    """
                    INSERT INTO controlplane_events (event_type, tenant_id, envelope)
                    VALUES (:event_type, NULL, CAST(:envelope AS jsonb))
                    RETURNING id
                    """
                ),
                {
                    "event_type": event_type,
                    "envelope": (
                        f'{{"event_type":"{event_type}","service":"tests","payload":{{}}}}'
                    ),
                },
            ).scalar_one()
            defn = conn.execute(
                text(
                    """
                    SELECT pg_get_functiondef(
                        'register_controlplane_event_consumer(text, text[], text, integer)'::regprocedure
                    )
                    """
                )
            ).scalar_one()
            assert "SECURITY DEFINER" in defn
            assert "search_path" in defn
            assert conn.execute(
                text("SELECT has_table_privilege(:role, 'controlplane_events', 'UPDATE')"),
                {"role": role},
            ).scalar_one() is False
            assert conn.execute(
                text("SELECT has_table_privilege(:role, 'controlplane_events', 'DELETE')"),
                {"role": role},
            ).scalar_one() is False
            assert conn.execute(
                text("SELECT has_table_privilege(:role, 'controlplane_events', 'TRUNCATE')"),
                {"role": role},
            ).scalar_one() is False

        with eng.connect() as conn:
            tx = conn.begin()
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(text("LOCK TABLE controlplane_events IN SHARE MODE"))
            tx.rollback()

        with eng.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            conn.execute(
                text(
                    """
                    SELECT register_controlplane_event_consumer(
                        :group, string_to_array(:types, ','), 'earliest', 5
                    )
                    """
                ),
                {"group": group, "types": event_type},
            )
            status = conn.execute(
                text(
                    """
                    SELECT status FROM controlplane_event_deliveries
                    WHERE consumer_group = :group AND event_id = :event_id
                    """
                ),
                {"group": group, "event_id": event_id},
            ).scalar_one()
        assert status == "pending"
    finally:
        eng.dispose()


def test_register_lock_timeout_then_retry_delivers(migrated_postgres: str) -> None:
    """A publisher that stays open past the lock timeout does not drop the event."""

    group = f"reg-timeout-{uuid.uuid4().hex[:8]}"
    event_type = f"test.regtimeout.{uuid.uuid4().hex[:8]}.v1"
    eng = create_engine(migrated_postgres)
    hold = eng.connect()
    hold_tx = hold.begin()
    try:
        event_id = hold.execute(
            text(
                """
                INSERT INTO controlplane_events (event_type, tenant_id, envelope)
                VALUES (:event_type, NULL, CAST(:envelope AS jsonb))
                RETURNING id
                """
            ),
            {
                "event_type": event_type,
                "envelope": (
                    f'{{"event_type":"{event_type}","service":"tests","payload":{{}}}}'
                ),
            },
        ).scalar_one()
        with eng.connect() as conn:
            with pytest.raises(Exception, match="lock timeout"):
                with conn.begin():
                    conn.execute(
                        text(
                            """
                            SELECT register_controlplane_event_consumer(
                                :group, string_to_array(:types, ','), 'earliest', 5
                            )
                            """
                        ),
                        {"group": group, "types": event_type},
                    )
        with eng.connect() as conn:
            subs = conn.execute(
                text(
                    """
                    SELECT count(*) FROM controlplane_event_subscriptions
                    WHERE consumer_group = :group
                    """
                ),
                {"group": group},
            ).scalar_one()
        assert int(subs) == 0
        hold_tx.commit()
    finally:
        if hold_tx.is_active:
            hold_tx.rollback()
        hold.close()

    with eng.begin() as conn:
        conn.execute(
            text(
                """
                SELECT register_controlplane_event_consumer(
                    :group, string_to_array(:types, ','), 'earliest', 5
                )
                """
            ),
            {"group": group, "types": event_type},
        )
        status = conn.execute(
            text(
                """
                SELECT status FROM controlplane_event_deliveries
                WHERE consumer_group = :group AND event_id = :event_id
                """
            ),
            {"group": group, "event_id": event_id},
        ).scalar_one()
    assert status == "pending"
    eng.dispose()


def test_temp_shadow_tables_do_not_capture_definer_writes(migrated_postgres: str) -> None:
    """A caller's temp tables must not hide the real subscription or delivery tables."""

    role = "panopticon_events_shadow"
    group = f"shadow-{uuid.uuid4().hex[:8]}"
    event_type = f"test.shadow.{uuid.uuid4().hex[:8]}.v1"
    eng = create_engine(migrated_postgres)
    try:
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
            conn.execute(text(_REGISTER_GRANTS.format(role=role)))
            conn.execute(text(f"GRANT {role} TO CURRENT_USER"))
            defs = conn.execute(
                text(
                    """
                    SELECT pg_get_functiondef(p.oid)
                    FROM pg_proc AS p
                    JOIN pg_namespace AS n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'public'
                      AND p.proname IN (
                        'notify_controlplane_event',
                        'deliver_controlplane_event',
                        'register_controlplane_event_consumer'
                      )
                    """
                )
            ).scalars().all()
            assert len(defs) == 3
            for defn in defs:
                folded = defn.replace("'", "").replace('"', "")
                assert "pg_catalog, public, pg_temp" in folded

        with eng.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            conn.execute(text("CREATE TEMP TABLE shadow_log (which text, who text)"))
            conn.execute(
                text(
                    """
                    CREATE TEMP TABLE controlplane_event_subscriptions (
                        consumer_group text,
                        event_type text,
                        max_attempts integer,
                        UNIQUE (consumer_group, event_type)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TEMP TABLE controlplane_event_deliveries (
                        event_id bigint,
                        consumer_group text,
                        status text,
                        attempts integer,
                        max_attempts integer,
                        available_at timestamptz,
                        UNIQUE (consumer_group, event_id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE FUNCTION pg_temp.record_shadow() RETURNS trigger
                    LANGUAGE plpgsql AS $fn$
                    BEGIN
                        INSERT INTO shadow_log (which, who)
                        VALUES (TG_TABLE_NAME, current_user);
                        RETURN NEW;
                    END;
                    $fn$
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TRIGGER shadow_subscriptions
                    AFTER INSERT ON controlplane_event_subscriptions
                    FOR EACH ROW EXECUTE FUNCTION pg_temp.record_shadow()
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TRIGGER shadow_deliveries
                    AFTER INSERT ON controlplane_event_deliveries
                    FOR EACH ROW EXECUTE FUNCTION pg_temp.record_shadow()
                    """
                )
            )
            conn.execute(
                text(
                    """
                    SELECT register_controlplane_event_consumer(
                        :group, string_to_array(:types, ','), 'earliest', 5
                    )
                    """
                ),
                {"group": group, "types": event_type},
            )
            event_id = conn.execute(
                text(
                    """
                    INSERT INTO public.controlplane_events (event_type, tenant_id, envelope)
                    VALUES (:event_type, NULL, CAST(:envelope AS jsonb))
                    RETURNING id
                    """
                ),
                {
                    "event_type": event_type,
                    "envelope": (
                        f'{{"event_type":"{event_type}","service":"tests","payload":{{}}}}'
                    ),
                },
            ).scalar_one()
            fired = conn.execute(text("SELECT which, who FROM shadow_log")).fetchall()
            subs = conn.execute(
                text(
                    """
                    SELECT count(*) FROM public.controlplane_event_subscriptions
                    WHERE consumer_group = :group AND event_type = :event_type
                    """
                ),
                {"group": group, "event_type": event_type},
            ).scalar_one()
            status = conn.execute(
                text(
                    """
                    SELECT status FROM public.controlplane_event_deliveries
                    WHERE consumer_group = :group AND event_id = :event_id
                    """
                ),
                {"group": group, "event_id": event_id},
            ).scalar_one()
        assert fired == []
        assert int(subs) == 1
        assert status == "pending"
    finally:
        eng.dispose()


def test_delivery_counts_are_read_only_and_transaction_scoped(migrated_postgres: str) -> None:
    engine = create_engine(migrated_postgres)
    try:
        with engine.connect() as connection:
            before = delivery_counts_sync(connection)
            group = "counts-" + uuid.uuid4().hex
            for status in ("pending", "pending", "claimed", "dead", "delivered"):
                event_id = connection.execute(
                    text("INSERT INTO controlplane_events (event_type, envelope) "
                         "VALUES ('test.counts.v1', '{}'::jsonb) RETURNING id")
                ).scalar_one()
                connection.execute(
                    text("INSERT INTO controlplane_event_deliveries "
                         "(event_id, consumer_group, status) VALUES (:event, :group, :status)"),
                    {"event": event_id, "group": group, "status": status},
                )
            assert delivery_counts_sync(connection) == (before[0] + 3, before[1] + 1)
            # Reading twice neither consumes nor changes the delivery rows.
            assert delivery_counts_sync(connection) == (before[0] + 3, before[1] + 1)
            connection.rollback()
            assert delivery_counts_sync(connection) == before
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_defer_preserves_final_retry_across_worker_restart(migrated_postgres):
    from pydantic import BaseModel

    class Payload(BaseModel):
        n: int

    event_type = f"test.defer.{uuid.uuid4().hex}.v1"
    group = f"defer-{uuid.uuid4().hex}"
    handled = []
    async def handle(_env, payload):
        handled.append(payload.n)

    first = RoutingControlPlaneConsumer(
        migrated_postgres, ["controlplane.events.v1"], group,
        {event_type: (Payload, handle)}, max_attempts=1, worker_id="interrupted",
    )
    publisher = PostgresEventPublisher(migrated_postgres, "tests")
    await publisher.start()
    try:
        await publisher.publish(tenant_id=uuid.uuid4(), event_type=event_type, payload={"n": 7})
    finally:
        await publisher.stop()
    # Register and claim directly to model a block before the handler runs.
    first._engine = create_async_engine(normalize_async_url(migrated_postgres))
    try:
        assert await first._ensure_registered()
        rows = await first._claim(limit=1)
        assert len(rows) == 1 and rows[0]["attempts"] == 1
        event_id = int(rows[0]["event_id"])
        assert await first._defer(event_id)
        assert not await first._defer(event_id)  # Replays do not refund twice.
        async with first._engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT status, attempts, claimed_by FROM controlplane_event_deliveries "
                "WHERE consumer_group=:group AND event_id=:event"
            ), {"group": group, "event": event_id})).one()
        assert tuple(row) == ("pending", 0, None)
    finally:
        await first.stop()
    restarted = RoutingControlPlaneConsumer(
        migrated_postgres, ["controlplane.events.v1"], group,
        {event_type: (Payload, handle)}, max_attempts=1, worker_id="recovered",
    )
    try:
        assert await restarted.poll_once() == 1
        assert handled == [7]
        assert await restarted.poll_once() == 0
    finally:
        await restarted.stop()
