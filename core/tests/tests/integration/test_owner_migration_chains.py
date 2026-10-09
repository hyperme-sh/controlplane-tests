"""Owner baselines must equal the actual old-chain 0053 PostgreSQL schema."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text
from alembic.util.exc import CommandError
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from controlplane_db import migrations
from panopticon_tests.fixtures.postgres import normalize_sync_sqlalchemy_url, run_alembic_upgrade, alembic_ini_path
from panopticon_tests.schema import schema_snapshot

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[4]
CHAINS = ROOT / 'core/lib/controlplane-db/src/controlplane_db/migration_chains'


def snapshot(url):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return schema_snapshot(connection)
    finally:
        engine.dispose()


def execute(url, sql):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(sql, execution_options={'no_parameters': True})
    finally:
        engine.dispose()


@pytest.fixture(scope='module')
def databases():
    with PostgresContainer('postgres:16-alpine', username='migration', password='local-test-only', dbname='source') as pg:
        url = normalize_sync_sqlalchemy_url(pg.get_connection_url())
        execute(url, 'CREATE ROLE controlplane; CREATE ROLE controlplane_app NOBYPASSRLS')
        run_alembic_upgrade(url)
        # No source connections may remain when copying its schema and data.
        admin = create_engine(url, isolation_level='AUTOCOMMIT')
        with admin.connect() as connection:
            for name in ('fresh', 'stamped', 'wrong_head', 'isolated', 'rollback', 'no_roles', 'reload', 'unversioned', 'reload_old', 'reload_fresh', 'reload_failure', 'marketplace_owners', 'marketplace_legacy', 'marketplace_fresh'):
                template = 'source' if name in ('stamped', 'rollback', 'reload', 'reload_failure', 'marketplace_owners', 'marketplace_legacy') else 'template0'
                connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
        admin.dispose()
        yield {name: url.rsplit('/', 1)[0] + '/' + name for name in
               ('source', 'fresh', 'stamped', 'wrong_head', 'isolated', 'rollback', 'no_roles', 'reload', 'unversioned', 'reload_old', 'reload_fresh', 'reload_failure', 'marketplace_owners', 'marketplace_legacy', 'marketplace_fresh')}


def test_fresh_baselines_match_every_0053_database_object(databases):
    migrations.upgrade(databases['fresh'], baseline_only=True)
    expected, actual = snapshot(databases['source']), snapshot(databases['fresh'])
    differences = {name: {field: [expected['tables'].get(name, {}).get(field), value]
                         for field, value in table.items()
                         if expected['tables'].get(name, {}).get(field) != value}
                   for name, table in actual['tables'].items() if expected['tables'].get(name) != table}
    assert actual == expected, json.dumps(differences, indent=2)
    assert len(actual['tables']) == 80
    assert 'hypermesh_onboarding_runs' in actual['tables']
    assert len(actual['functions']) == 7
    assert migrations.current_heads(databases['fresh']) == {name: entry['baseline'] for name, entry in json.loads((CHAINS/'manifest.json').read_text())['chains'].items()}
    # Fresh baselines contain schema, not the private old-chain seed data.
    engine = create_engine(databases['fresh'])
    with engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM tenants')).scalar_one() == 0
    engine.dispose()


def test_stamp_then_upgrade_preserves_schema_and_existing_data(databases):
    url = databases['stamped']
    identity = str(uuid.uuid4())
    execute(url, f"INSERT INTO tenants (id,name,is_active) VALUES ('{identity}', 'baseline-preservation-test',false)")
    before = snapshot(url)
    migrations.stamp_baselines(url)
    migrations.upgrade(url, baseline_only=True)
    migrations.stamp_baselines(url)  # Idempotent only while every owner is at baseline.
    assert snapshot(url) == before
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT name FROM tenants WHERE id=:id'), {'id': identity}).scalar_one() == 'baseline-preservation-test'
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0053_onboarding_runs'
    engine.dispose()


def test_other_mixed_revisions_cannot_be_stamped_or_implicitly_upgraded(databases, monkeypatch):
    url = databases['wrong_head']
    monkeypatch.setenv('DATABASE_URL', url)
    command.upgrade(Config(str(alembic_ini_path())), '0052_host_registrations')
    before = snapshot(url)
    with pytest.raises(ValueError, match='0053_onboarding_runs'):
        migrations.stamp_baselines(url)
    with pytest.raises(ValueError, match='explicit 0053'):
        migrations.upgrade(url)
    assert snapshot(url) == before
    assert set(migrations.current_heads(url).values()) == {None}
    run_alembic_upgrade(url)
    migrations.stamp_baselines(url)
    registry = json.loads((CHAINS / 'manifest.json').read_text())
    assert migrations.current_heads(url) == {chain: entry['baseline'] for chain, entry in registry['chains'].items()}
    assert snapshot(url) == snapshot(databases['source'])


def test_product_migration_changes_only_its_owner_head(databases, tmp_path):
    root = tmp_path / 'chains'
    shutil.copytree(CHAINS, root)
    migrations.upgrade(databases['isolated'], registry_path=root)
    before = migrations.current_heads(databases['isolated'], root)
    (root / 'hypermesh/versions/0054_test.py').write_text('''from alembic import op
revision = "hypermesh_planted_0054"
down_revision = "CURRENT_HEAD"
branch_labels = None
depends_on = None
def upgrade():
    op.execute("ALTER TABLE hypermesh_devices ADD COLUMN planted_test integer")
def downgrade():
    op.execute("ALTER TABLE hypermesh_devices DROP COLUMN planted_test")
'''.replace('CURRENT_HEAD', before['hypermesh']))
    migrations.upgrade(databases['isolated'], chains=['hypermesh'], registry_path=root)
    after = migrations.current_heads(databases['isolated'], root)
    assert after['hypermesh'] == 'hypermesh_planted_0054'
    assert {k:v for k,v in after.items() if k != 'hypermesh'} == {k:v for k,v in before.items() if k != 'hypermesh'}
    assert snapshot(databases['source']) != snapshot(databases['isolated'])


def test_failure_rolls_back_every_stamp_table(databases, tmp_path):
    root = tmp_path / 'chains'
    shutil.copytree(CHAINS, root)
    # A nonexistent last revision causes failure after earlier commands issued.
    registry = json.loads((root/'manifest.json').read_text())
    registry['chains']['payment_service']['baseline'] = 'missing_baseline'
    (root/'manifest.json').write_text(json.dumps(registry))
    with pytest.raises(CommandError, match="missing_baseline"):
        migrations.stamp_baselines(databases['rollback'], root)
    assert set(migrations.current_heads(databases['rollback']).values()) == {None}


def test_schema_diff_detects_rls_index_constraint_and_grant_drift(databases):
    url = databases['isolated']
    before = snapshot(url)
    execute(url, "ALTER TABLE hypermesh_devices DISABLE ROW LEVEL SECURITY; "
                 "CREATE INDEX planted_tenant_idx ON hypermesh_devices(tenant_id); "
                 "ALTER TABLE hypermesh_devices ADD CONSTRAINT planted_check CHECK (id IS NOT NULL); "
                 "GRANT SELECT ON hypermesh_devices TO controlplane_app")
    after = snapshot(url)
    for key in ('rls', 'indexes', 'constraints', 'grants'):
        assert before['tables']['hypermesh_devices'][key] != after['tables']['hypermesh_devices'][key]


def test_baselines_match_when_optional_app_roles_do_not_exist(migration_database):
    # The shared migration server is checked to have no controlplane/controlplane_app roles.
    url = migration_database('head')
    fresh = migration_database()
    migrations.upgrade(fresh, baseline_only=True)
    assert snapshot(url) == snapshot(fresh)


def test_cross_chain_foreign_keys_only_target_platform(databases):
    registry = json.loads((CHAINS/'manifest.json').read_text())
    owners = {table: chain for chain, entry in registry['chains'].items() for table in entry['tables']}
    engine = create_engine(databases['source'])
    with engine.connect() as connection:
        pairs = connection.execute(text("""SELECT child.relname,parent.relname FROM pg_constraint k
            JOIN pg_class child ON child.oid=k.conrelid JOIN pg_class parent ON parent.oid=k.confrelid
            JOIN pg_namespace n ON n.oid=child.relnamespace
            WHERE k.contype='f' AND n.nspname='public'""")).all()
    engine.dispose()
    for child, parent in pairs:
        assert owners[child] == owners[parent] or owners[parent] == 'platform'


def test_empty_or_partial_databases_cannot_be_stamped(databases):
    with pytest.raises(ValueError, match='0053_onboarding_runs'):
        migrations.stamp_baselines(databases['no_roles'])
    execute(databases['no_roles'], "CREATE TABLE alembic_version(version_num varchar(32)); "
            "INSERT INTO alembic_version VALUES ('0053_onboarding_runs')")
    with pytest.raises(ValueError, match='missing baseline tables'):
        migrations.stamp_baselines(databases['no_roles'])
    assert set(migrations.current_heads(databases['no_roles']).values()) == {None}


def test_reload_adopts_existing_database_and_is_repeatable(databases, tmp_path):
    from controlplane_db.startup import _owners
    url = databases['reload']
    before = snapshot(url)
    _owners(url)
    _owners(url)
    assert migrations.current_heads(url) == migrations.chain_heads()
    after = snapshot(url)
    assert after['tables'].pop('host_payout_addresses')['rls'] == [True, True]
    assert_journal_schema_delta(after)
    assert_subject_scope_delta(before, after)
    assert_cache_migration_delta(before, after)
    root = tmp_path / 'chains'
    shutil.copytree(CHAINS, root)
    (root / 'payment_service/versions/0054_reload.py').write_text("""from alembic import op
revision = 'payment_reload_0054'
down_revision = 'payment_service_0058_transfers'
branch_labels = None
depends_on = None
def upgrade():
    op.execute('CREATE TABLE payment_reload_marker(id integer)')
def downgrade():
    pass
""")
    _owners(url, root)
    _owners(url, root)
    assert migrations.current_heads(url, root)['payment_service'] == 'payment_reload_0054'


def test_reload_refuses_unversioned_existing_schema(databases):
    from controlplane_db.startup import _owners
    url = databases['unversioned']
    execute(url, 'CREATE TABLE tenants (id uuid PRIMARY KEY)')
    before = snapshot(url)
    with pytest.raises(ValueError, match='unversioned existing'):
        _owners(url)
    assert snapshot(url) == before


def test_reload_refuses_0052_without_changing_it(databases, monkeypatch):
    from controlplane_db.startup import _owners
    url = databases['reload_old']
    monkeypatch.setenv('DATABASE_URL', url)
    command.upgrade(Config(str(alembic_ini_path())), '0052_host_registrations')
    before = snapshot(url)
    with pytest.raises(ValueError, match='0053_onboarding_runs'):
        _owners(url)
    assert snapshot(url) == before
    assert set(migrations.current_heads(url).values()) == {None}


def test_reload_fresh_database_uses_owner_schema(databases):
    from controlplane_db.startup import _owners
    _owners(databases['reload_fresh'])
    after = snapshot(databases['reload_fresh'])
    assert after['tables'].pop('host_payout_addresses')['rls'] == [True, True]
    assert_journal_schema_delta(after)
    before = snapshot(databases['source'])
    assert_subject_scope_delta(before, after)
    assert_cache_migration_delta(before, after)
    assert migrations.current_heads(databases['reload_fresh']) == migrations.chain_heads()


def test_reload_upgrade_failure_rolls_back_adoption(databases, tmp_path):
    from controlplane_db.startup import _owners
    root = tmp_path / 'chains'
    shutil.copytree(CHAINS, root)
    (root / 'payment_service/versions/0054_failure.py').write_text("""from alembic import op
revision = 'payment_failure_0054'
down_revision = 'payment_service_0058_transfers'
branch_labels = None
depends_on = None
def upgrade():
    op.execute('CREATE TABLE reload_failure_marker(id integer)')
    raise RuntimeError('planted reload failure')
def downgrade():
    pass
""")
    url = databases['reload_failure']
    before = snapshot(url)
    with pytest.raises(RuntimeError, match='planted reload failure'):
        _owners(url, root)
    assert snapshot(url) == before
    assert set(migrations.current_heads(url).values()) == {None}


@pytest.mark.parametrize("mode, database", [("owners", "marketplace_owners"), ("legacy", "marketplace_legacy"), ("owners", "marketplace_fresh")])
def test_startup_provisions_configured_marketplace_after_migrations(databases, monkeypatch, mode, database):
    from controlplane_tenancy import marketplace, startup
    url = databases[database]
    tenant = uuid.uuid4()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CONTROLPLANE_MIGRATION_MODE", mode)
    monkeypatch.setattr(marketplace, "MARKETPLACE_TENANT_ID", tenant)
    assert startup.main() == 0
    assert startup.main() == 0
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            row = connection.execute(text("SELECT name, is_active FROM tenants WHERE id = :id"), {"id": tenant}).one()
            assert row.is_active is True
            assert row.name == f"Marketplace {tenant}"
            connection.execute(text("UPDATE tenants SET name = 'Existing marketplace' WHERE id = :id"), {"id": tenant})
            marketplace.ensure_marketplace_tenant(connection)
            assert connection.execute(text("SELECT name FROM tenants WHERE id = :id"), {"id": tenant}).scalar_one() == "Existing marketplace"
            connection.execute(text("UPDATE tenants SET is_active = false WHERE id = :id"), {"id": tenant})
        with pytest.raises(RuntimeError, match="inactive"), engine.begin() as connection:
            marketplace.ensure_marketplace_tenant(connection)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT is_active FROM tenants WHERE id = :id"), {"id": tenant}).scalar_one() is False
    finally:
        engine.dispose()


def assert_cache_migration_delta(before, after):
    import copy
    expected = copy.deepcopy(before)
    health = expected['tables']['hypermesh_device_health']
    health['columns'].extend([
        ['model_cache_hashes', 'jsonb', False, None, '', '', None],
        ['model_cache_available_bytes', 'bigint', False, None, '', '', None],
    ])
    health['constraints'].append(['ck_hypermesh_health_cache_available', 'c',
        'CHECK (model_cache_available_bytes IS NULL OR model_cache_available_bytes >= 0)', True])
    health['constraints'].sort(key=lambda row: row[0])
    jobs = expected['tables']['hypermesh_jobs']
    for column in jobs['columns']:
        if column[0] == 'source_ref':
            column[1] = 'character varying(1024)'
    jobs['columns'].extend([
        ['file_hash', 'character varying(256)', False, None, '', '', '"default"'],
        ['size_bytes', 'bigint', False, None, '', '', None],
    ])
    expected['tables']['hypermesh_host_registrations']['columns'].append(
        ['machine_identity', 'jsonb', False, None, '', '', None])
    expected['tables']['aiuditor_connections']['columns'].append(
        ['control_plane_id', 'character varying(128)', True, "'local'::character varying", '', '', '"default"'])
    identity = after['tables'].pop('aiuditor_machine_identities')
    assert identity['rls'] == [True, True]
    assert [(c[0], c[1], c[2]) for c in identity['columns']] == [
        ('authority_id', 'character varying(128)', True),
        ('tenant_id', 'uuid', True),
        ('control_plane_id', 'character varying(128)', True),
        ('device_id', 'uuid', True),
        ('request_id', 'uuid', True),
        ('state', 'jsonb', True),
        ('revoked', 'boolean', True),
    ]
    assert any(c[1] == 'p' and c[2] == 'PRIMARY KEY (authority_id, tenant_id, control_plane_id, device_id)' for c in identity['constraints'])
    assert len(identity['policies']) == 1
    assert 'app.current_tenant_id' in identity['policies'][0][4]
    assert 'app.current_tenant_id' in identity['policies'][0][5]
    scoped = after['tables'].pop('aiuditor_private_model_admissions')
    assert scoped['rls'] == [True, True]
    assert {c[0] for c in scoped['columns']} == {
        'id', 'tenant_id', 'owner_sub', 'target', 'target_sha256', 'lab_job_id',
        'lab_device_id', 'status', 'evidence', 'audit', 'created_at',
    }
    assert len(scoped['policies']) == 1
    assert 'app.current_tenant_id' in scoped['policies'][0][4]
    assert 'app.current_tenant_id' in scoped['policies'][0][5]
    revisions = after['tables'].pop('hypermesh_model_revisions')
    assert revisions['rls'] == [True, True]
    assert {c[0] for c in revisions['columns']} == {
        'id', 'tenant_id', 'owner_sub', 'request_id', 'name', 'license', 'size_bytes',
        'state', 'manifest', 'storage_account', 'storage_container', 'sealed_files',
        'expires_at', 'created_at', 'sealed_at', 'scan_evidence', 'scan_state',
        'inspection', 'inspection_claim_id', 'inspection_claim_until',
    }
    assert len(revisions['policies']) == 1
    assert 'app.current_tenant_id' in revisions['policies'][0][4]
    assert 'app.current_tenant_id' in revisions['policies'][0][5]
    sites = after['tables'].pop('hypermesh_sites')
    assert sites['rls'] == [True, True]
    assert [(c[0], c[1], c[2]) for c in sites['columns']] == [
        ('tenant_id', 'uuid', True),
        ('site_id', 'character varying(128)', True),
        ('owner_org', 'uuid', False),
        ('created_at', 'timestamp with time zone', True),
    ]
    assert any(c[1] == 'p' and c[2] == 'PRIMARY KEY (tenant_id, site_id)' for c in sites['constraints'])
    assert len(sites['policies']) == 4
    tokens = expected['tables']['hypermesh_site_tokens']
    tokens['policies'].append([
        'site_token_lookup_by_hash', 'r', True, ['PUBLIC'],
        "((secret_hash)::text = NULLIF(current_setting('app.site_token_hash'::text, true), ''::text))",
        None,
    ])
    tokens['policies'].sort(key=lambda row: row[0])
    expected['tables']['hypermesh_leases']['columns'].extend([
        ['payment_amount_atoms', 'bigint', False, None, '', '', None],
        ['payment_test_credit_available', 'boolean', True, 'false', '', '', None],
    ])
    assert after == expected


def test_payout_subject_forward_migration_preserves_receipt_and_refuses_lossy_downgrade():
    from sqlalchemy.exc import IntegrityError

    with PostgresContainer("postgres:16-alpine") as pg:
        url = normalize_sync_sqlalchemy_url(pg.get_connection_url())
        migrations.upgrade(url, baseline_only=True)
        engine = create_engine(url)
        tenant, org = uuid.uuid4(), uuid.uuid4()
        try:
            with engine.begin() as connection:
                command.upgrade(
                    migrations._config(CHAINS, "payment_service", connection),
                    "payment_service_0054_payout_addr",
                )
                connection.execute(
                    text(
                        "INSERT INTO tenants (id, name, is_active) VALUES (:id, 'subject-migration', true)"
                    ),
                    {"id": tenant},
                )
                connection.execute(
                    text("""INSERT INTO host_payout_addresses
                    (tenant_id, host_org_id, chain_id, address, verified_by_sub,
                     proof_audience, proof_message_hash, proof_signature_hash, verified_at)
                    VALUES (:tenant, :org, 84532, :address, :subject, 'test', :hash, :hash, now())"""),
                    {
                        "tenant": tenant,
                        "org": org,
                        "address": "0x" + "a" * 40,
                        "subject": "h" * 256,
                        "hash": "b" * 64,
                    },
                )
            migrations.upgrade(url, chains=["payment_service"])
            with engine.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT verified_by_sub FROM host_payout_addresses")
                    ).scalar_one()
                    == "h" * 256
                )
                connection.execute(
                    text("UPDATE host_payout_addresses SET verified_by_sub=:subject"),
                    {"subject": "h" * 512},
                )
            with pytest.raises(IntegrityError), engine.begin() as connection:
                command.downgrade(
                    migrations._config(CHAINS, "payment_service", connection),
                    "payment_service_0054_payout_addr",
                )
            assert (
                migrations.current_heads(url)["payment_service"]
                == migrations.chain_heads()["payment_service"]
            )
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT verified_by_sub FROM host_payout_addresses")
                    ).scalar_one()
                    == "h" * 512
                )
        finally:
            engine.dispose()


def assert_subject_scope_delta(before, after):
    old = dict(before["tables"]["users"]["indexes"])
    new = dict(after["tables"]["users"]["indexes"])
    assert old.pop("ix_users_keycloak_sub_unique") == (
        "CREATE UNIQUE INDEX ix_users_keycloak_sub_unique ON public.users "
        "USING btree (keycloak_sub) WHERE (keycloak_sub IS NOT NULL)"
    )
    assert new.pop("uq_users_platform_keycloak_sub") == (
        "CREATE UNIQUE INDEX uq_users_platform_keycloak_sub ON public.users "
        "USING btree (keycloak_sub) WHERE ((tenant_id IS NULL) AND (keycloak_sub IS NOT NULL))"
    )
    assert new.pop("uq_users_tenant_keycloak_sub") == (
        "CREATE UNIQUE INDEX uq_users_tenant_keycloak_sub ON public.users "
        "USING btree (tenant_id, keycloak_sub) WHERE ((tenant_id IS NOT NULL) AND (keycloak_sub IS NOT NULL))"
    )
    assert old == new
    # Other schema fields still compare exactly in the existing delta check.
    after["tables"]["users"]["indexes"] = before["tables"]["users"]["indexes"]


def assert_journal_schema_delta(after):
    sequence = after["sequences"].pop("usdc_deposit_addresses_hd_index_seq")
    assert sequence["owned_by"] == [["usdc_deposit_addresses", "hd_index"]]
    assert sequence["definition"][0][1:4] == [0, 1, 2147483647]
    for table in ("usdc_ledger_postings", "usdc_ledger_entries", "usdc_deposit_addresses", "usdc_payment_intents", "usdc_chain_transfers"):
        assert after["tables"].pop(table)["rls"] == [True, True]
    for function in ("usdc_journal_immutable()", "usdc_posting_seal_only()", "usdc_entry_draft_only()", "usdc_posting_balanced()", "usdc_intent_progress_only()", "usdc_transfer_progress_only()"):
        assert after["functions"].pop(function)
