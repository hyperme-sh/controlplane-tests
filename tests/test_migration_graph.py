"""Template reuse must reject graph shapes without a linear upgrade order."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from spanopticon_tests.fixtures import postgres


def configure_graph(tmp_path, monkeypatch, *, merged=True, dependency=False):
    migrations = tmp_path / "migrations"
    versions = migrations / "versions"
    versions.mkdir(parents=True)
    (migrations / "env.py").write_text('''
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
    graph = [("root", None), ("left", "root"), ("right", "root")]
    if merged:
        graph.append(("joined", ("left", "right")))
    if dependency:
        graph = [("root", None), ("left", "root")]
    for revision, parent in graph:
        dep = "root" if dependency and revision == "left" else None
        (versions / f"{revision}.py").write_text(f'''
from alembic import op
import sqlalchemy as sa
revision = {revision!r}
down_revision = {parent!r}
depends_on = {dep!r}
def upgrade():
    op.create_table({revision!r}, sa.Column('id', sa.Integer(), primary_key=True))
def downgrade():
    op.drop_table({revision!r})
''')
    ini = tmp_path / "alembic.ini"
    ini.write_text(f"[alembic]\nscript_location = {migrations}\n")
    monkeypatch.setenv("SPANOPTICON_TEST_ALEMBIC_INI", str(ini))


@pytest.mark.parametrize("targets", [("left", "right"), ("right", "left"), ("head",)])
def test_merged_branches_rejected_before_any_template_mutation(tmp_path, monkeypatch, targets):
    configure_graph(tmp_path, monkeypatch)
    templates = postgres.MigrationTemplates("postgresql+psycopg://unused/unused")
    mutations = []
    monkeypatch.setattr(templates, "_create", lambda *args: mutations.append(args))
    monkeypatch.setattr(postgres, "_alembic", lambda *args: mutations.append(args))
    try:
        for target in targets:
            with pytest.raises(RuntimeError, match="linear Alembic graph"):
                templates.template_at(target)
        assert mutations == []
        assert templates._templates == {}
    finally:
        templates.dispose()


@pytest.mark.parametrize("dependency,match", [(False, "one Alembic head"), (True, "linear Alembic graph")])
def test_other_unsupported_graphs_fail_before_creation(tmp_path, monkeypatch, dependency, match):
    configure_graph(tmp_path, monkeypatch, merged=False, dependency=dependency)
    templates = postgres.MigrationTemplates("postgresql+psycopg://unused/unused")
    mutations = []
    monkeypatch.setattr(templates, "_create", lambda *args: mutations.append(args))
    monkeypatch.setattr(postgres, "_alembic", lambda *args: mutations.append(args))
    try:
        with pytest.raises(RuntimeError, match=match):
            templates.template_at("head")
        assert mutations == []
    finally:
        templates.dispose()


@pytest.mark.parametrize("targets", [("left", "right"), ("right", "left")])
def test_real_postgres_rejects_branch_graph_without_creating_databases(tmp_path, monkeypatch, targets):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires disposable PostgreSQL TEST_DATABASE_URL")
    configure_graph(tmp_path, monkeypatch)
    prefix = "graph_" + uuid.uuid4().hex[:16]
    templates = postgres.MigrationTemplates(url, prefix=prefix)
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        for target in (*targets, "head"):
            with pytest.raises(RuntimeError, match="linear Alembic graph"):
                templates.template_at(target)
            with admin.connect() as cx:
                assert cx.execute(text("SELECT datname FROM pg_database WHERE starts_with(datname,:prefix)"), {"prefix": prefix}).all() == []
    finally:
        templates.dispose()
        with admin.connect() as cx:
            for name in cx.execute(text("SELECT datname FROM pg_database WHERE starts_with(datname,:prefix)"), {"prefix": prefix}).scalars().all():
                cx.exec_driver_sql('DROP DATABASE "' + name.replace('"', '""') + '" WITH (FORCE)')
        admin.dispose()
