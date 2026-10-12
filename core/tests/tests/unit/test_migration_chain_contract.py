"""Frozen schema and source edits must fail the chain contract before deployment."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('check_migration_chains', ROOT/'scripts/check_migration_chains.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_chain_contract_and_frozen_source_match():
    assert checker.check(ROOT) == []


def test_planted_baseline_and_legacy_edits_fail(tmp_path):
    source = ROOT/checker.DB
    shutil.copytree(source, tmp_path/checker.DB)
    (tmp_path/'scripts').mkdir()
    shutil.copyfile(ROOT/'scripts/ownership.json', tmp_path/'scripts/ownership.json')
    baseline = tmp_path/checker.DB/'migration_chains/hypermesh/versions/baseline.sql'
    baseline.write_text(baseline.read_text() + '\nCREATE TABLE unrelated(id integer);\n')
    legacy = tmp_path/checker.DB/'alembic/versions/20261001_0053_onboarding_runs.py'
    legacy.write_text(legacy.read_text() + '\n# planted frozen-source edit\n')
    errors = checker.check(tmp_path)
    assert 'hypermesh: frozen baseline changed' in errors
    assert 'versions/20261001_0053_onboarding_runs.py: frozen mixed chain source changed' in errors


def test_installed_wheel_contains_runner_and_five_baselines():
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('UV_NO_SYNC', None)
    result = subprocess.run([
        'uv', 'run', '--isolated', '--no-editable', '--frozen', '--no-dev',
        '--refresh-package', 'controlplane-db',
        '--package', 'controlplane-db', 'python', '-c',
        "import json; from pathlib import Path; import controlplane_db.migrations as m; import controlplane_db.startup as startup; assert callable(startup.main); "
        "assert len(list(Path(m.__file__).with_name('migration_chains').glob('*/versions/baseline.sql'))) == 5; "
        "print(json.dumps(m.chain_heads()))",
    ], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
    assert set(json.loads(result.stdout)) == {'platform', 'events', 'hypermesh', 'aiuditor', 'payment_service'}


def test_new_revision_in_wrong_owner_chain_fails(tmp_path):
    shutil.copytree(ROOT/checker.DB, tmp_path/checker.DB)
    (tmp_path/'scripts').mkdir()
    shutil.copyfile(ROOT/'scripts/ownership.json', tmp_path/'scripts/ownership.json')
    path = tmp_path/checker.DB/'migration_chains/platform/versions/0054_planted.py'
    path.write_text('''revision = "platform_0099_planted"
down_revision = "platform_0054_subject_scope"
def upgrade():
    op.add_column("hypermesh_leases", column)
''')
    assert any('hypermesh_leases: migration belongs to hypermesh' in error for error in checker.check(tmp_path))
    path.write_text(path.read_text().replace('hypermesh_leases', 'users'))
    assert checker.check(tmp_path) == []


@pytest.mark.parametrize(
    "revision",
    ["0054_planted", "platform_planted_0054", "hypermesh_0054_planted", "platform_0054", "platform_0054_Planted"],
)
def test_owner_revision_must_use_the_chain_id_format(tmp_path, revision):
    shutil.copytree(ROOT/checker.DB, tmp_path/checker.DB)
    (tmp_path/'scripts').mkdir()
    shutil.copyfile(ROOT/'scripts/ownership.json', tmp_path/'scripts/ownership.json')
    path = tmp_path/checker.DB/'migration_chains/platform/versions/0099_planted.py'
    path.write_text(f'''revision = "{revision}"
down_revision = "platform_0054_subject_scope"
def upgrade():
    pass
''')
    assert any('revision must be one literal <chain>_<NNNN>_<slug> id' in error for error in checker.check(tmp_path))
    path.write_text(path.read_text().replace(f'revision = "{revision}"', 'revision = "platform_0099_planted"'))
    assert checker.check(tmp_path) == []


@pytest.mark.parametrize(
    "chain,table",
    [
        ("platform", "hypermesh_leases"),
        ("events", "users"),
        ("hypermesh", "users"),
        ("aiuditor", "hypermesh_leases"),
        ("payment_service", "users"),
    ],
)
def test_baseline_wrapper_cannot_add_foreign_owner_operations(tmp_path, chain, table):
    shutil.copytree(ROOT / checker.DB, tmp_path / checker.DB)
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/ownership.json", tmp_path / "scripts/ownership.json"
    )
    path = tmp_path / checker.DB / f"migration_chains/{chain}/versions/0053_baseline.py"
    original = path.read_text()
    path.write_text(original.replace("def upgrade():\n", f'def upgrade():\n    op.drop_table("{table}")\n'))
    assert any(
        f"{table}: migration belongs to" in error for error in checker.check(tmp_path)
    )
    path.write_text(original)
    assert checker.check(tmp_path) == []


@pytest.mark.parametrize(
    "operation",
    [
        'op.drop_constraint("fk_name", "hypermesh_leases")',
        'op.drop_constraint("fk_name", table_name="hypermesh_leases")',
        'op.drop_index("index_name", "hypermesh_leases")',
        'op.drop_index("index_name", table_name="hypermesh_leases")',
    ],
)
def test_drop_operations_cannot_cross_owner_chains(tmp_path, operation):
    shutil.copytree(ROOT / checker.DB, tmp_path / checker.DB)
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/ownership.json", tmp_path / "scripts/ownership.json"
    )
    path = tmp_path / checker.DB / "migration_chains/platform/versions/0054_planted.py"
    source = (
        'revision = "platform_0099_planted"\ndown_revision = "platform_0054_subject_scope"\ndef upgrade():\n    '
        + operation
        + "\n"
    )
    path.write_text(source)
    assert any(
        "hypermesh_leases: migration belongs to hypermesh" in error
        for error in checker.check(tmp_path)
    )
    path.write_text(source.replace("hypermesh_leases", "users"))
    assert checker.check(tmp_path) == []


def test_baseline_inventory_cannot_omit_frozen_tables(tmp_path):
    shutil.copytree(ROOT / checker.DB, tmp_path / checker.DB)
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/ownership.json", tmp_path / "scripts/ownership.json")
    path = tmp_path / checker.DB / "migration_chains/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["chains"]["platform"]["baseline_tables"] = []
    path.write_text(json.dumps(manifest))
    assert "platform: baseline inventory differs from frozen SQL" in checker.check(tmp_path)


@pytest.mark.parametrize(
    "operation",
    [
        'op.execute("UPDATE public.billing_records SET amount=0")',
        'op.execute("UPDATE public . billing_records SET amount=0")',
        'op.execute(\'DELETE FROM "public"."billing_records"\')',
        'op.execute("ALTER TABLE billing_records ADD COLUMN planted integer")',
        'op.execute("COMMENT ON TABLE billing_records IS \'planted\'")',
        'op.execute(\'comment on table "public"."billing_records" is NULL\')',
        'op.execute("COMMENT /* metadata */ ON TABLE public . billing_records IS \'planted\'")',
        'op.execute("SECURITY LABEL FOR planted ON TABLE billing_records IS \'planted\'")',
        'op.execute("GRANT SELECT ON TABLE billing_records TO PUBLIC")',
        'op.execute("REVOKE SELECT ON TABLE billing_records FROM PUBLIC")',
        'op.execute("TRUNCATE billing_tax_orders, billing_records")',
        'op.drop_table("billing_records")',
        'op.execute("CREATE INDEX planted ON billing_records (id)")',
        'op.execute("CREATE UNIQUE INDEX CONCURRENTLY planted ON public.billing_records (id)")',
        'op.execute("CREATE TRIGGER planted BEFORE INSERT ON billing_records FOR EACH ROW EXECUTE FUNCTION tax_history_retained()")',
        'op.execute("CREATE OR REPLACE TRIGGER planted AFTER UPDATE ON public.billing_records FOR EACH ROW EXECUTE FUNCTION tax_history_retained()")',
        'op.execute("CREATE CONSTRAINT TRIGGER planted AFTER INSERT ON billing_records DEFERRABLE FOR EACH ROW EXECUTE FUNCTION tax_history_retained()")',
        'op.execute("CREATE TEMP TABLE billing_records (id integer)")',
        'op.execute("SELECT revision_id INTO billing_records FROM billing_tax_orders")',
    ],
)
def test_tax_validation_read_exception_never_permits_platform_writes(
    tmp_path, operation
):
    shutil.copytree(ROOT / checker.DB, tmp_path / checker.DB)
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/ownership.json", tmp_path / "scripts/ownership.json"
    )
    path = (
        tmp_path
        / checker.DB
        / "migration_chains/payment_service/versions/0063_tax_calculations.py"
    )
    path.write_text(path.read_text() + "\n" + operation + "\n")
    assert any(
        "billing_records: migration belongs to platform" in error
        for error in checker.check(tmp_path)
    )


def test_exceptional_billing_read_can_share_literal_with_owned_ddl(tmp_path):
    shutil.copytree(ROOT / checker.DB, tmp_path / checker.DB)
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/ownership.json", tmp_path / "scripts/ownership.json"
    )
    path = (
        tmp_path
        / checker.DB
        / "migration_chains/payment_service/versions/0063_tax_calculations.py"
    )
    path.write_text(
        path.read_text()
        + '\nop.execute("SELECT id FROM billing_records FOR UPDATE; CREATE INDEX planted ON billing_tax_orders (revision_id)")\n'
    )
    assert checker.check(tmp_path) == []
