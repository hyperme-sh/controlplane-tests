"""Qualify migration templates and schema snapshots on a disposable server."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from spanopticon_tests.fixtures.postgres import MigrationTemplates
from spanopticon_tests.schema import schema_snapshot


def test_real_migration_templates_clone_isolation_and_schema_changes(tmp_path, monkeypatch):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires a disposable administrative PostgreSQL TEST_DATABASE_URL')
    migrations = tmp_path / 'migrations'
    (migrations / 'versions').mkdir(parents=True)
    (migrations / 'env.py').write_text('''
import os
from alembic import context
from sqlalchemy import create_engine
engine = create_engine(os.environ['DATABASE_URL'])
try:
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
''')
    (migrations / 'versions/01_table.py').write_text('''
from alembic import op
import sqlalchemy as sa
revision = 'one'
down_revision = None
def upgrade():
    op.create_table('consumer_item', sa.Column('id', sa.Integer(), primary_key=True))
def downgrade():
    op.drop_table('consumer_item')
''')
    (migrations / 'versions/02_value.py').write_text('''
from alembic import op
import sqlalchemy as sa
revision = 'two'
down_revision = 'one'
def upgrade():
    op.add_column('consumer_item', sa.Column('value', sa.Text()))
def downgrade():
    op.drop_column('consumer_item', 'value')
''')
    ini = tmp_path / 'alembic.ini'
    ini.write_text('[alembic]\nscript_location = ' + str(migrations) + '\n')
    monkeypatch.setenv('SPANOPTICON_TEST_ALEMBIC_INI', str(ini))
    monkeypatch.setenv('DATABASE_URL', 'must-be-restored')
    prefix = 'fixture_' + uuid.uuid4().hex[:16]
    templates = MigrationTemplates(url, prefix=prefix)
    engines = []
    try:
        first = templates.template_at('one')
        head = templates.template_at('head')
        assert head == templates.template_at('two')
        assert os.environ['DATABASE_URL'] == 'must-be-restored'
        _, clone_url = templates.database(head)
        for database_url in (templates.url(first), templates.url(head), clone_url):
            engines.append(create_engine(database_url))
        with engines[0].connect() as cx:
            original = schema_snapshot(cx)
        with engines[1].connect() as cx:
            upgraded = schema_snapshot(cx)
        with engines[2].begin() as cx:
            assert schema_snapshot(cx) == upgraded
            cx.execute(text("INSERT INTO consumer_item(id,value) VALUES(1,'clone only')"))
            cx.execute(text('ALTER TABLE consumer_item ENABLE ROW LEVEL SECURITY'))
            changed = schema_snapshot(cx)
        assert original != upgraded
        assert [column[0] for column in upgraded['tables']['consumer_item']['columns']] == ['id', 'value']
        assert changed['tables']['consumer_item']['rls'] == [True, False]
        assert changed != upgraded
        with engines[1].connect() as cx:
            assert cx.execute(text('SELECT count(*) FROM consumer_item')).scalar_one() == 0
    finally:
        for engine in engines:
            engine.dispose()
        templates.dispose()
        admin = create_engine(url, isolation_level='AUTOCOMMIT')
        try:
            with admin.connect() as cx:
                names = cx.execute(text('SELECT datname FROM pg_database WHERE starts_with(datname,:prefix)'), {'prefix': prefix}).scalars().all()
                for name in names:
                    cx.exec_driver_sql('DROP DATABASE "' + name.replace('"','""') + '" WITH (FORCE)')
        finally:
            admin.dispose()
