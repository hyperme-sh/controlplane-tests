"""Negative fixtures prove CI rejects undeclared dependency edges."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('check_import_boundaries', ROOT / 'scripts/check_import_boundaries.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
CONTRACT = json.loads((ROOT / 'scripts/import_boundaries.json').read_text())


def write(root, path, source):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)


@pytest.mark.parametrize('statement', [
    'import turn_service',
    'from api.internal_private import secret',
    'import controlplane_db.internal_private',
])
def test_hypermesh_forbidden_imports_fail(tmp_path, statement):
    write(tmp_path, 'products/hypermesh/src/hypermesh_service/probe.py', statement)
    assert checker.violations(tmp_path, CONTRACT)


def test_from_parent_cannot_hide_private_module(tmp_path):
    write(tmp_path, 'core/lib/controlplane-core/src/api/core/private.py', '')
    write(tmp_path, 'products/hypermesh/src/hypermesh_service/probe.py', 'from api.core import private')
    assert 'api.core.private' in checker.violations(tmp_path, CONTRACT)[0]


@pytest.mark.parametrize(('path', 'statement', 'allowed'), [
    ('products/hypermesh-gateway/src/hypermesh_gateway_service/probe.py', 'from hypermesh_service.ticket import verify_ticket', True),
    ('products/hypermesh-gateway/src/hypermesh_gateway_service/probe.py', 'import hypermesh_service.seed', False),
    ('products/hypermesh-gateway/tests/test_probe.py', 'import hypermesh_service.seed', True),
    ('products/aiuditor/tests/test_probe.py', 'import hypermesh_service.seed', False),
    ('core/auth-service/src/auth_service/probe.py', 'import hypermesh_service.seed', False),
    ('core/e2e-tests/tests/test_probe.py', 'import hypermesh_service.seed', True),
    ('products/hypermesh/src/hypermesh_service/probe.py', 'from controlplane_db.models import HypermeshDevice', True),
])
def test_named_exceptions_and_public_interfaces(tmp_path, path, statement, allowed):
    write(tmp_path, path, statement)
    assert (not checker.violations(tmp_path, CONTRACT)) == allowed


def test_repo_passes_and_all_python_products_are_registered():
    assert checker.violations(ROOT, CONTRACT) == []
    actual = {str(path.parent.relative_to(ROOT)) for path in (ROOT / 'products').glob('*/pyproject.toml')}
    assert actual == set(CONTRACT['products'].values())
