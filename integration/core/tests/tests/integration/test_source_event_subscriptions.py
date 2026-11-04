"""Owner-upgraded subscriptions filter history and future inserts at the database."""

from __future__ import annotations

import threading
import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from controlplane_db import migrations
from controlplane_events import CONTROLPLANE_EVENTS_V1, RoutingControlPlaneConsumer
from controlplane_events.publisher import PostgresEventPublisher
from spanopticon_tests.fixtures.postgres import normalize_sync_sqlalchemy_url

pytestmark = pytest.mark.integration
SOURCE = "payment-service-deposit-outbox"


@pytest.fixture(scope="module")
def source_database(require_docker):
    with PostgresContainer("postgres:16-alpine") as pg:
        url = normalize_sync_sqlalchemy_url(pg.get_connection_url())
        engine = create_engine(url)
        with engine.begin() as db:
            db.exec_driver_sql(
                "CREATE ROLE controlplane; CREATE ROLE controlplane_app NOBYPASSRLS"
            )
        migrations.upgrade(url, chains=["events"], baseline_only=True)
        # Existing subscriptions and events must survive the owner-chain upgrade.
        with engine.begin() as db:
            db.exec_driver_sql(
                "SELECT register_controlplane_event_consumer('pre-upgrade', ARRAY['test.legacy.v1'], 'latest', 5)"
            )
        with engine.begin() as db:
            db.exec_driver_sql(
                "SELECT register_controlplane_event_consumer('payment.deposit.servicebus-outbox.v1', "
                "ARRAY['payment.succeeded.v1'], 'latest', 5)"
            )
        completed = insert(engine, "payment.succeeded.v1", SOURCE, 1)
        insert(engine, "payment.succeeded.v1", SOURCE, 2)
        insert(engine, "payment.succeeded.v1", "invoice-service", 3)
        with engine.begin() as db:
            db.execute(
                text(
                    "UPDATE controlplane_event_deliveries SET status='delivered', delivered_at=now() "
                    "WHERE consumer_group='payment.deposit.servicebus-outbox.v1' AND event_id=:event"
                ),
                {"event": completed},
            )
        migrations.prepare_event_source_index(url)
        migrations.upgrade(url, chains=["events"])
        try:
            yield url, engine
        finally:
            engine.dispose()


def insert(engine, event_type, service, n):
    with Session(engine) as db:
        event_id = PostgresEventPublisher(str(engine.url), service).publish_in_session(
            db, tenant_id=None, event_type=event_type, payload={"n": n}
        )
        db.commit()
        return event_id


def register(db, group, event_type, source, mode="earliest"):
    db.execute(
        text(
            "SELECT register_controlplane_event_consumer(:group, ARRAY[:type], :mode, 5, :source)"
        ),
        {"group": group, "type": event_type, "mode": mode, "source": source},
    )


def delivery_ids(engine, group):
    with engine.connect() as db:
        return set(
            db.execute(
                text(
                    "SELECT event_id FROM controlplane_event_deliveries WHERE consumer_group=:group"
                ),
                {"group": group},
            ).scalars()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, SOURCE])
async def test_consumer_filters_mixed_history_and_future_inserts(
    source_database, source
):
    url, engine = source_database
    event_type, group = f"test.source.{uuid.uuid4().hex}.v1", uuid.uuid4().hex
    # Same event type, with older unrelated invoices and Checkout/test-credit successes.
    for n in range(40):
        insert(
            engine, event_type, ["invoice-service", "checkout", "test-credit"][n % 3], n
        )
    deposit_id = insert(engine, event_type, SOURCE, 40)
    seen = []

    class Payload(BaseModel):
        n: int

    async def handle(envelope, payload):
        seen.append((envelope.service, payload.n))

    consumer = RoutingControlPlaneConsumer(
        url,
        [CONTROLPLANE_EVENTS_V1],
        group,
        {event_type: (Payload, handle)},
        source_service=source,
        batch_size=100,
    )
    try:
        await consumer.poll_once()
        assert len(seen) == (41 if source is None else 1)
        if source is not None:
            assert delivery_ids(engine, group) == {deposit_id}
        insert(engine, event_type, "invoice-service", 41)
        fresh_id = insert(engine, event_type, SOURCE, 42)
        await consumer.poll_once()
        assert len(seen) == (43 if source is None else 2)
        assert seen[-1] == (SOURCE, 42)
        if source is not None:
            assert delivery_ids(engine, group) == {deposit_id, fresh_id}
        # A restart backfills pending deposits without duplicating already delivered rows.
        consumer._registered = False
        await consumer.poll_once()
        assert len(seen) == (43 if source is None else 2)
    finally:
        await consumer.stop()


def test_upgrade_preserves_unfiltered_subscription(source_database):
    _, engine = source_database
    insert(engine, "test.legacy.v1", "legacy-source", 1)
    assert len(delivery_ids(engine, "pre-upgrade")) == 1
    with engine.connect() as db:
        assert (
            db.exec_driver_sql(
                "SELECT source_service FROM controlplane_event_subscriptions WHERE consumer_group='pre-upgrade'"
            ).scalar_one()
            is None
        )


@pytest.mark.parametrize("replacement", [None, "other-source"])
def test_group_source_cannot_change(source_database, replacement):
    _, engine = source_database
    group, event_type = uuid.uuid4().hex, f"test.immutable.{uuid.uuid4().hex}.v1"
    with engine.begin() as db:
        register(db, group, event_type, SOURCE)
    with pytest.raises(Exception, match="source filter cannot change"):
        with engine.begin() as db:
            register(db, group, event_type, replacement)
    with engine.connect() as db:
        assert (
            db.execute(
                text(
                    "SELECT source_service FROM controlplane_event_subscriptions WHERE consumer_group=:group"
                ),
                {"group": group},
            ).scalar_one()
            == SOURCE
        )


@pytest.mark.parametrize("matching", [True, False])
def test_source_registration_overlapping_insert_loses_no_matching_event(
    source_database, matching
):
    _, engine = source_database
    group, event_type = uuid.uuid4().hex, f"test.race.{uuid.uuid4().hex}.v1"
    registering, finished = threading.Event(), threading.Event()
    errors = []

    def concurrent_registration():
        try:
            with engine.begin() as db:
                registering.set()
                register(db, group, event_type, SOURCE)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    with Session(engine) as db:
        event_id = PostgresEventPublisher(
            "unused", SOURCE if matching else "other"
        ).publish_in_session(
            db,
            tenant_id=None,
            event_type=event_type,
            payload={"n": 1},
        )
        thread = threading.Thread(target=concurrent_registration)
        thread.start()
        try:
            assert registering.wait(2)
            assert not finished.wait(0.1)  # SHARE waits for this in-flight insert.
        finally:
            db.commit()
            thread.join(5)
    assert not thread.is_alive() and not errors
    assert delivery_ids(engine, group) == ({event_id} if matching else set())


def test_source_registration_runs_with_application_grants_and_index(source_database):
    _, engine = source_database
    group, event_type = uuid.uuid4().hex, f"test.role.{uuid.uuid4().hex}.v1"
    keep = insert(engine, event_type, SOURCE, 1)
    insert(engine, event_type, "invoice-service", 2)
    with engine.begin() as db:
        db.exec_driver_sql("SET LOCAL ROLE controlplane_app")
        register(db, group, event_type, SOURCE)
        assert db.execute(
            text(
                "SELECT event_id FROM controlplane_event_deliveries WHERE consumer_group=:group"
            ),
            {"group": group},
        ).scalars().all() == [keep]
        assert not db.exec_driver_sql(
            "SELECT has_table_privilege(current_user, 'controlplane_events', 'UPDATE')"
        ).scalar_one()
        # Both filter terms can be index conditions, rather than filtering every payment.
        db.exec_driver_sql("SET LOCAL enable_seqscan=off")
        plan = (
            db.execute(
                text(
                    "EXPLAIN SELECT id FROM controlplane_events WHERE event_type=ANY(ARRAY[:type]) AND envelope->>'service'=:source"
                ),
                {"type": event_type, "source": SOURCE},
            )
            .scalars()
            .all()
        )
        assert any("ix_controlplane_events_type_source_id" in line for line in plan)
        assert any(
            "Index Cond:" in line and "service" in line and "event_type" in line
            for line in plan
        )


def test_empty_source_is_rejected():
    with pytest.raises(ValueError, match="source_service must be nonempty"):
        RoutingControlPlaneConsumer("unused", [], "unused", {}, source_service=" ")


def test_upgrade_narrows_existing_relay_without_changing_delivery_history(
    source_database,
):
    _, engine = source_database
    group = "payment.deposit.servicebus-outbox.v1"
    with engine.begin() as db:
        register(db, group, "payment.succeeded.v1", SOURCE)
        records = db.execute(
            text(
                "SELECT d.status, e.envelope->>'service' AS source, e.envelope->'payload'->>'n' AS n "
                "FROM controlplane_event_deliveries d JOIN controlplane_events e ON e.id=d.event_id "
                "WHERE d.consumer_group=:group ORDER BY e.id"
            ),
            {"group": group},
        ).all()
        assert [(row.status, row.source, row.n) for row in records] == [
            ("delivered", SOURCE, "1"),
            ("pending", SOURCE, "2"),
            ("pending", "invoice-service", "3"),
        ]
    # Preserve the old no-op backlog, but never enqueue more non-deposits.
    insert(engine, "payment.succeeded.v1", "invoice-service", 4)
    assert len(delivery_ids(engine, group)) == 3


def test_old_registration_signature_retains_configured_source_filter(source_database):
    _, engine = source_database
    group, event_type = uuid.uuid4().hex, f"test.old-signature.{uuid.uuid4().hex}.v1"
    keep = insert(engine, event_type, SOURCE, 1)
    insert(engine, event_type, "invoice-service", 2)
    with engine.begin() as db:
        register(db, group, event_type, SOURCE)
        db.execute(
            text(
                "SELECT register_controlplane_event_consumer(:group, ARRAY[:type], 'earliest', 5)"
            ),
            {"group": group, "type": event_type},
        )
    insert(engine, event_type, "invoice-service", 3)
    assert delivery_ids(engine, group) == {keep}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, SOURCE])
async def test_cursor_reset_filters_updates_and_backfill(source_database, source):
    url, engine = source_database
    group, event_type = uuid.uuid4().hex, f"test.reset.{uuid.uuid4().hex}.v1"
    old_other = insert(engine, event_type, "invoice-service", 0)
    first = insert(engine, event_type, SOURCE, 1)
    seen = []

    class Payload(BaseModel):
        n: int

    async def handle(envelope, payload):
        seen.append((envelope.service, payload.n))

    consumer = RoutingControlPlaneConsumer(
        url,
        [],
        group,
        {event_type: (Payload, handle)},
        source_service=source,
        batch_size=100,
    )
    try:
        await consumer.poll_once()
        second = insert(engine, event_type, SOURCE, 2)
        missing_other = insert(engine, event_type, "invoice-service", 3)
        await consumer.poll_once()
        with engine.begin() as db:
            # Exercise both UPDATE of a pre-filter retained delivery and INSERT
            # of historical rows that do not currently have any delivery.
            db.execute(
                text(
                    "INSERT INTO controlplane_event_deliveries(event_id,consumer_group,status) "
                    "VALUES (:event,:group,'delivered') ON CONFLICT DO NOTHING"
                ),
                {"event": old_other, "group": group},
            )
            db.execute(
                text(
                    "DELETE FROM controlplane_event_deliveries WHERE consumer_group=:group AND event_id IN (:second,:other)"
                ),
                {"group": group, "second": second, "other": missing_other},
            )
        await consumer.reset_cursor()
        with engine.connect() as db:
            old_status = db.execute(
                text(
                    "SELECT status FROM controlplane_event_deliveries WHERE consumer_group=:group AND event_id=:event"
                ),
                {"group": group, "event": old_other},
            ).scalar_one()
        assert old_status == ("pending" if source is None else "delivered")
        assert delivery_ids(engine, group) == (
            {old_other, first, second, missing_other}
            if source is None
            else {old_other, first, second}
        )
        before = len(seen)
        await consumer.poll_once()
        replayed = seen[before:]
        assert replayed == (
            [("invoice-service", 0), (SOURCE, 1), (SOURCE, 2), ("invoice-service", 3)]
            if source is None
            else [(SOURCE, 1), (SOURCE, 2)]
        )
        # A nonzero cursor must not reset the completed earlier deposit.
        await consumer.reset_cursor(first)
        before = len(seen)
        await consumer.poll_once()
        assert seen[before:] == (
            [(SOURCE, 2), ("invoice-service", 3)] if source is None else [(SOURCE, 2)]
        )
    finally:
        await consumer.stop()


def test_first_old_image_registration_keeps_reserved_deposit_source(require_docker):
    with PostgresContainer("postgres:16-alpine") as pg:
        url = normalize_sync_sqlalchemy_url(pg.get_connection_url())
        migrations.upgrade(url, chains=["events"])
        engine = create_engine(url)
        try:
            keep = insert(engine, "payment.succeeded.v1", SOURCE, 1)
            insert(engine, "payment.succeeded.v1", "invoice-service", 2)
            group = "payment.deposit.servicebus-outbox.v1"
            with engine.begin() as db:
                db.execute(
                    text(
                        "SELECT register_controlplane_event_consumer(:group, ARRAY['payment.succeeded.v1'], 'earliest', 5)"
                    ),
                    {"group": group},
                )
            with engine.begin() as db:
                register(db, group, "payment.succeeded.v1", SOURCE)
            assert delivery_ids(engine, group) == {keep}
        finally:
            engine.dispose()


def test_concurrent_index_preflight_allows_publishers_and_is_required_for_existing_log(
    require_docker, monkeypatch
):
    import time
    from sqlalchemy import event

    with PostgresContainer("postgres:16-alpine") as pg:
        url = normalize_sync_sqlalchemy_url(pg.get_connection_url())
        migrations.upgrade(url, chains=["events"], baseline_only=True)
        engine = create_engine(url)
        entered, errors = threading.Event(), []
        worker_pid = []

        def prepare():
            try:
                # Exercise the real owner CLI, including its AUTOCOMMIT connection.
                monkeypatch.setenv("DATABASE_URL", url)
                monkeypatch.setattr(
                    "sys.argv", ["migrations", "prepare-event-source-index"]
                )
                assert migrations.main() == 0
            except Exception as exc:
                errors.append(exc)

        def starting(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("CREATE INDEX CONCURRENTLY"):
                worker_pid.append(
                    connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
                )
                entered.set()

        # Existing databases must not silently fall back to a blocking build.
        with pytest.raises(RuntimeError, match="prepare-event-source-index"):
            migrations.upgrade(url, chains=["events"])
        assert migrations.current_heads(url)["events"] == "events_0053_baseline"
        event.listen(type(engine), "before_cursor_execute", starting)
        hold = engine.connect()
        tx = hold.begin()
        thread = None
        try:
            hold.exec_driver_sql(
                "INSERT INTO controlplane_events(event_type,envelope) VALUES ('test.index.v1','{}')"
            )
            thread = threading.Thread(target=prepare)
            thread.start()
            assert entered.wait(5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with engine.connect() as db:
                    building = db.execute(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid "
                            "AND relation='public.controlplane_events'::regclass "
                            "AND mode='ShareUpdateExclusiveLock' AND granted)"
                        ),
                        {"pid": worker_pid[0]},
                    ).scalar_one()
                if building:
                    break
                time.sleep(0.02)
            assert building and thread.is_alive() and not errors
            with engine.begin() as publisher:
                publisher.exec_driver_sql("SET LOCAL lock_timeout='500ms'")
                publisher.exec_driver_sql(
                    "INSERT INTO controlplane_events(event_type,envelope) VALUES ('test.index.v1','{}')"
                )
            tx.commit()
            thread.join(10)
            assert not thread.is_alive() and not errors
            # The preflight did not accidentally run the schema upgrade.
            assert migrations.current_heads(url)["events"] == "events_0053_baseline"
            migrations.upgrade(url, chains=["events"])
            migrations.prepare_event_source_index(
                url
            )  # Safe to rerun after completion.
        finally:
            if tx.is_active:
                tx.rollback()
            hold.close()
            if thread is not None:
                thread.join(15)
            event.remove(type(engine), "before_cursor_execute", starting)
            engine.dispose()
