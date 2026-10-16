"""Owner startup prepares the events source index without a manual step.

``events_0054_source_subscriptions`` refuses a blocking index build on a
published event log. Deployment startup runs the concurrent preflight first, so
an unattended migrate container applies it on fresh, already prepared and
previously stuck databases alike.
"""
from __future__ import annotations

import pytest
from controlplane_db import migrations
from controlplane_db.startup import main
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration
INDEX = 'public.ix_controlplane_events_type_source_id'


def _index_state(url: str):
    engine = create_engine(url.replace('+asyncpg', '+psycopg'))
    try:
        with engine.connect() as db:
            return db.execute(
                text(
                    'SELECT indisvalid AND indisready FROM pg_index '
                    'WHERE indexrelid = to_regclass(:name)'
                ),
                {'name': INDEX},
            ).scalar()
    finally:
        engine.dispose()


def _published_baseline(migration_database) -> str:
    """Owner baselines with a non-empty event log, as on an existing deployment."""
    url = migration_database()
    migrations.upgrade(url, baseline_only=True)
    engine = create_engine(url)
    try:
        with engine.begin() as db:
            db.exec_driver_sql(
                "INSERT INTO controlplane_events(event_type, envelope) "
                "VALUES ('payment.succeeded.v1', '{\"service\": \"invoice-service\"}')"
            )
    finally:
        engine.dispose()
    return url


@pytest.fixture
def owners_mode(monkeypatch):
    def configure(url: str, *, bootstrap: bool = False) -> None:
        monkeypatch.setenv('DATABASE_URL', url)
        monkeypatch.setenv('CONTROLPLANE_MIGRATION_MODE', 'owners')
        if bootstrap:
            monkeypatch.setenv('CONTROLPLANE_DB_BOOTSTRAP', '1')
        else:
            monkeypatch.delenv('CONTROLPLANE_DB_BOOTSTRAP', raising=False)

    return configure


def test_fresh_database_builds_index_in_schema_transaction(migration_database, owners_mode):
    url = migration_database()
    # No event log yet: nothing to prepare concurrently.
    assert migrations._prepare_pending_event_source_index(url) is False
    owners_mode(url, bootstrap=True)
    assert main() == 0
    assert migrations.current_heads(url) == migrations.chain_heads()
    assert _index_state(url) is True


def test_previously_stuck_database_upgrades_unattended(migration_database, owners_mode):
    url = _published_baseline(migration_database)
    # This is the state a deploy got stuck in: the revision refuses to run.
    with pytest.raises(RuntimeError, match='prepare-event-source-index'):
        migrations.upgrade(url, chains=['events'])
    assert migrations.current_heads(url)['events'] == 'events_0053_baseline'
    assert _index_state(url) is None

    owners_mode(url)
    assert main() == 0
    assert migrations.current_heads(url) == migrations.chain_heads()
    assert _index_state(url) is True
    engine = create_engine(url)
    try:
        with engine.connect() as db:
            assert db.exec_driver_sql('SELECT count(*) FROM controlplane_events').scalar_one() == 1
    finally:
        engine.dispose()
    # Rerunning startup after success is a no-op for the preflight.
    assert migrations._prepare_pending_event_source_index(url) is False
    assert main() == 0


def test_already_prepared_database_keeps_its_index(migration_database, owners_mode):
    url = _published_baseline(migration_database)
    migrations.prepare_event_source_index(url)
    engine = create_engine(url)
    try:
        with engine.connect() as db:
            before = db.execute(text('SELECT to_regclass(:name)::oid'), {'name': INDEX}).scalar_one()
    finally:
        engine.dispose()

    owners_mode(url)
    assert main() == 0
    assert migrations.current_heads(url) == migrations.chain_heads()
    engine = create_engine(url)
    try:
        with engine.connect() as db:
            after = db.execute(text('SELECT to_regclass(:name)::oid'), {'name': INDEX}).scalar_one()
    finally:
        engine.dispose()
    assert after == before


def test_interrupted_concurrent_build_is_repaired(migration_database, owners_mode):
    url = _published_baseline(migration_database)
    migrations.prepare_event_source_index(url)
    engine = create_engine(url)
    try:
        # A killed CREATE INDEX CONCURRENTLY leaves an invalid index behind.
        with engine.begin() as db:
            db.execute(
                text('UPDATE pg_index SET indisvalid = false WHERE indexrelid = to_regclass(:name)'),
                {'name': INDEX},
            )
    finally:
        engine.dispose()
    assert _index_state(url) is False

    owners_mode(url)
    assert main() == 0
    assert migrations.current_heads(url)['events'] == migrations.chain_heads()['events']
    assert _index_state(url) is True
