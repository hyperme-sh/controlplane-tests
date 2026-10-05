"""Planted owner violations exercise the same static gate run by CI."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "table_checker", ROOT / "scripts/check_table_boundaries.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_current_runtime_edges_have_explicit_owners_or_named_exceptions():
    manifest = json.loads((ROOT / "scripts/ownership.json").read_text())
    assert checker.violations(ROOT, manifest) == []


@pytest.mark.parametrize(
    "source",
    [
        'query = "SELECT * FROM hypermesh_leases"',
        'query = "UPDATE public.hypermesh_leases SET status = :status"',
        "query = 'DELETE FROM \"hypermesh_leases\" WHERE id = :id'",
        'query = f"SELECT * FROM hypermesh_leases WHERE id = {lease_id}"',
        "from hypermesh_service.models import LeaseRow",
        "import hypermesh_service.models as models\nquery = models.LeaseRow",
        "from hypermesh_service import models as hm\nquery = hm.LeaseRow",
        'query = table("hypermesh_leases")',
        'query = "SELECT * FROM users, hypermesh_leases WHERE users.id = hypermesh_leases.id"',
        'query = "ALTER TABLE IF EXISTS hypermesh_leases ADD COLUMN bad integer"',
        'query = "CREATE INDEX bad ON hypermesh_leases(id)"',
        "from hypermesh_service.models import *",
        'class Intruder:\n    __tablename__ = "hypermesh_leases"',
    ],
)
def test_cross_owner_sql_and_orm_fail(source, tmp_path):
    model = "products/hypermesh/src/hypermesh_service/models.py"
    destination = tmp_path / model
    destination.parent.mkdir(parents=True)
    destination.write_text('class LeaseRow:\n    __tablename__ = "hypermesh_leases"\n')
    path = tmp_path / "core/payment-service/src/payment_service/intruder.py"
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n")
    manifest = {
        "tables": {"hypermesh_leases": "products/hypermesh"},
        "non_orm_tables": {},
        "model_sources": {"products/hypermesh": model},
        "owners": {
            "products/hypermesh": {"package": "products/hypermesh"},
            "core/payment-service": {"package": "core/payment-service"},
        },
        "exceptions": [],
    }
    assert any(
        "owned by products/hypermesh" in error
        for error in checker.violations(tmp_path, manifest)
    )
    manifest["exceptions"] = [
        {"table": "hypermesh_leases", "source": path.relative_to(tmp_path).as_posix()}
    ]
    assert checker.violations(tmp_path, manifest) == []
    sibling = path.with_name("new_intruder.py")
    sibling.write_text(source + "\n")
    assert any(
        "new_intruder.py" in error for error in checker.violations(tmp_path, manifest)
    )


def test_sql_values_and_comments_are_not_table_references():
    assert checker.sql_tables(
        "SELECT 'FROM hypermesh_leases' -- FROM hypermesh_payouts\nFROM users"
    ) == {"users"}
