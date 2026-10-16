"""Owners-mode startup must tolerate a database revision the image does not contain."""
from __future__ import annotations

import logging

import pytest
from alembic.script.revision import ResolutionError
from alembic.util.exc import CommandError

from controlplane_db import migrations
from controlplane_db import startup


class _Connection:
    def execute(self, statement):
        return None


class _Begin:
    def __enter__(self):
        return _Connection()

    def __exit__(self, *exc):
        return False


class _Engine:
    def begin(self):
        return _Begin()

    def dispose(self):
        return None


class _Inspector:
    def __init__(self, tables):
        self._tables = list(tables)

    def get_table_names(self, schema='public'):
        return list(self._tables)


@pytest.fixture(autouse=True)
def _startup_logger_enabled():
    # Alembic's fileConfig (legacy migrations earlier in the workspace run)
    # disables loggers it does not name, which would hide the startup warning.
    log = logging.getLogger(startup.__name__)
    log.disabled = False
    log.propagate = True


def _owned_tables():
    registry = migrations._registry(migrations._ROOT)
    return sorted({table for entry in registry['chains'].values() for table in entry['tables']})


def _install_database(monkeypatch, versions, tables=()):
    monkeypatch.setattr(migrations, '_engine', lambda url: _Engine())
    monkeypatch.setattr(migrations, '_versions', lambda connection, root: dict(versions))
    monkeypatch.setattr(migrations, '_legacy_revision', lambda connection: None)
    monkeypatch.setattr(startup, 'inspect', lambda connection: _Inspector(tables))
    # The concurrent index preflight has its own Postgres integration tests.
    monkeypatch.setattr(migrations, 'prepare_pending_event_source_index', lambda url, root=None: False)


def _record_upgrades(monkeypatch):
    upgraded = []

    def upgrade(config, target):
        assert target == 'head'
        upgraded.append(config.attributes['version_table'])

    def downgrade(*args, **kwargs):
        raise AssertionError('startup must not downgrade')

    monkeypatch.setattr(startup.command, 'upgrade', upgrade)
    monkeypatch.setattr(startup.command, 'downgrade', downgrade)
    return upgraded


def test_known_revision_behind_head_still_upgrades(monkeypatch):
    heads = migrations.chain_heads()
    versions = dict(heads)
    versions['hypermesh'] = 'hypermesh_0053_baseline'
    versions['platform'] = 'platform_0053_baseline'
    upgraded = _record_upgrades(monkeypatch)
    _install_database(monkeypatch, versions)

    startup._owners('postgresql://example')

    assert upgraded == [f'alembic_version_{chain}' for chain in migrations._ORDER]


def test_unknown_revision_ahead_of_image_warns_and_skips(monkeypatch, caplog):
    heads = migrations.chain_heads()
    future = 'hypermesh_9999_not_in_this_image'
    versions = dict(heads)
    versions['hypermesh'] = future
    versions['platform'] = 'platform_0053_baseline'
    upgraded = _record_upgrades(monkeypatch)
    _install_database(monkeypatch, versions, tables=_owned_tables())

    with caplog.at_level(logging.WARNING):
        startup._owners('postgresql://example')

    assert 'alembic_version_hypermesh' not in upgraded
    assert upgraded == [
        f'alembic_version_{chain}' for chain in migrations._ORDER if chain != 'hypermesh'
    ]
    warnings = [
        record.getMessage() for record in caplog.records if record.name == 'controlplane_db.startup'
    ]
    assert warnings == [
        'skipping hypermesh upgrade: database revision '
        f'{future} is not in this image (image head {heads["hypermesh"]})'
    ]


@pytest.mark.parametrize(
    'later',
    ['hypermesh_0064_later_sibling', 'hypermesh_0001_lower_number_but_later', 'hypermesh_9999_not_in_this_image'],
)
def test_well_formed_unknown_revision_is_trusted_whatever_its_number(monkeypatch, caplog, later):
    # Numbers are not monotonic along a chain, so they never decide direction.
    heads = migrations.chain_heads()
    sibling = later
    versions = dict(heads)
    versions['hypermesh'] = sibling
    upgraded = _record_upgrades(monkeypatch)
    _install_database(monkeypatch, versions, tables=_owned_tables())

    with caplog.at_level(logging.WARNING):
        startup._owners('postgresql://example')

    assert 'alembic_version_hypermesh' not in upgraded
    assert [r.getMessage() for r in caplog.records if r.name == 'controlplane_db.startup'] == [
        f'skipping hypermesh upgrade: database revision {sibling} is not in this image '
        f'(image head {heads["hypermesh"]})'
    ]


@pytest.mark.parametrize(
    'revision',
    [
        'hypermesh_9999',  # not a chain revision id
        'hypermesh_64_short_number',
        'Hypermesh_9999_Typo',  # wrong case
        'platform_9999_other_chain',  # another chain's id
        'hypermesh_99999_extra_digit',
        '9999_hypermesh_reordered',
        'hypermesh_9999_ok\n',  # trailing newline
    ],
)
def test_unknown_revision_not_proven_newer_fails_closed(monkeypatch, caplog, revision):
    versions = dict(migrations.chain_heads())
    versions['hypermesh'] = revision
    upgraded = _record_upgrades(monkeypatch)
    _install_database(monkeypatch, versions, tables=_owned_tables())

    with caplog.at_level(logging.WARNING), pytest.raises(
        ValueError, match='not in this image and is not a revision id of this chain'
    ):
        startup._owners('postgresql://example')

    assert 'alembic_version_hypermesh' not in upgraded
    assert not [r for r in caplog.records if r.name == 'controlplane_db.startup']


def test_unknown_revision_with_missing_owned_tables_fails_closed(monkeypatch, caplog):
    heads = migrations.chain_heads()
    versions = dict(heads)
    versions['hypermesh'] = 'hypermesh_9999_not_in_this_image'
    registry = migrations._registry(migrations._ROOT)
    absent = registry['chains']['hypermesh']['tables'][0]
    tables = [table for table in _owned_tables() if table != absent]
    upgraded = _record_upgrades(monkeypatch)
    _install_database(monkeypatch, versions, tables=tables)

    with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match=absent):
        startup._owners('postgresql://example')

    assert 'alembic_version_hypermesh' not in upgraded
    assert not [r for r in caplog.records if r.name == 'controlplane_db.startup']


def test_unrelated_alembic_error_still_raises(monkeypatch, caplog):
    upgraded = []

    def upgrade(config, target):
        upgraded.append(config.attributes['version_table'])
        raise CommandError("Can't locate revision identified by 'not_the_database_revision'")

    monkeypatch.setattr(startup.command, 'upgrade', upgrade)
    _install_database(monkeypatch, migrations.chain_heads())

    with caplog.at_level(logging.WARNING), pytest.raises(
        CommandError, match='not_the_database_revision'
    ):
        startup._owners('postgresql://example')

    assert upgraded == ['alembic_version_platform']
    assert not [record for record in caplog.records if record.name == 'controlplane_db.startup']


def test_ambiguous_revision_lookup_still_raises(monkeypatch, caplog):
    def explode(self, revision):
        cause = ResolutionError(f"Multiple revisions start with '{revision}'", revision)
        raise CommandError(f"Can't locate revision identified by '{revision}'") from cause

    monkeypatch.setattr('alembic.script.base.ScriptDirectory.get_revision', explode)
    _install_database(monkeypatch, migrations.chain_heads())
    upgraded = _record_upgrades(monkeypatch)

    with caplog.at_level(logging.WARNING), pytest.raises(CommandError) as caught:
        startup._owners('postgresql://example')

    assert str(caught.value).startswith("Can't locate revision identified by ")
    assert str(caught.value.__cause__).startswith('Multiple revisions start with')
    assert upgraded == []
    assert not [record for record in caplog.records if record.name == 'controlplane_db.startup']


def test_unversioned_nonempty_schema_still_fails(monkeypatch):
    versions = {chain: None for chain in migrations._ORDER}
    upgraded = _record_upgrades(monkeypatch)
    _install_database(monkeypatch, versions, tables=('tenants',))

    with pytest.raises(ValueError, match='unversioned existing'):
        startup._owners('postgresql://example')

    assert upgraded == []


def test_legacy_mode_still_upgrades_without_skipping(monkeypatch):
    calls = []
    monkeypatch.setenv('CONTROLPLANE_MIGRATION_MODE', 'legacy')
    monkeypatch.setattr(startup.command, 'upgrade', lambda config, target: calls.append(target))

    assert startup.main() == 0
    assert calls == ['head']
