"""Frozen schema and source edits must fail the chain contract before deployment."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import shutil
import sys

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
