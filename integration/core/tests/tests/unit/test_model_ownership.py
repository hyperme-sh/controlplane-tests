"""Product model moves preserve schema and isolated migration/runtime registries."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]


def run(code):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "core/lib/controlplane-db/src")
    return subprocess.run([sys.executable, "-c", code], env=env,
                          check=True, capture_output=True, text=True)


def test_platform_import_has_no_product_models_or_imports():
    run("""
import sys
from controlplane_db import models
assert len(models.Base.metadata.tables) == 35
assert not any(name.startswith(('Hypermesh', 'Aiuditor', 'Operator')) for name in vars(models))
assert not hasattr(models, 'HostOrganizationBinding')
assert not any(name.startswith(('hypermesh_service', 'aiuditor_service', 'user_service')) for name in sys.modules)
""")


def test_mixed_migration_registry_is_complete_and_separate_from_runtime():
    run("""
import sys
from controlplane_db.alembic.legacy_models import Base as MigrationBase
from controlplane_db.models import Base
assert len(MigrationBase.metadata.tables) == 77
assert len(Base.metadata.tables) == 35
assert MigrationBase.metadata is not Base.metadata
assert not any(name.startswith(('hypermesh_service', 'aiuditor_service', 'user_service')) for name in sys.modules)
""")


def test_extraction_has_zero_postgresql_ddl_or_model_declaration_changes():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check_model_schema.py')],
                            check=True, capture_output=True, text=True)
    assert 'zero PostgreSQL ORM schema changes' in result.stdout
